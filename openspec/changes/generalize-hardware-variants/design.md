# Design: generalize hardware variants

## Context

Two radio models today ship as non-interchangeable hardware variants behind a
single vendor bundle URL:

| Model | Variant entries (ids) | Distinguisher |
| --- | --- | --- |
| BTECH BF-F8HP Pro | `bf-f8hp-pro` (NRF), `bf-f8hp-pro-nrfb` (NRFB) | radio off → hold `8` on power-on → display shows NRF / NRFB |
| Radtel RT-490 | `rt-490` (old PCB), `rt-490-new` (new PCB) | does it have channel-name editing? |

Both are handled ad hoc: two sibling `radios.json` entries, each with a
`firmware_filename_pattern` selecting its file from the shared bundle, and the
identification procedure written only in free-text `notes`. PR #21 added a
post-download guard (`select_firmware_file`) that *refuses to guess* but never
*asks*. This change makes variant identification a first-class, translatable,
UI-gated step.

Hard constraint carried through every decision: **radio ids are a compatibility
surface.** Released client binaries fetch `firmware_manifest.json` from `master`
keyed by id, and every translation catalog keys per-radio strings as
`radio.<id>.<field>`. Changing or removing an existing id silently breaks
already-shipped installs.

## Decision 1 — Schema: sibling entries linked by `variant_group` (chosen) vs a nested `variants` list

### Chosen: keep sibling entries, add a `variant_group` linkage + a `variant_groups` block

Member entries stay exactly as they are (own id, own `firmware_url`, own
`firmware_filename_pattern`) and gain one field:

```json
{ "id": "bf-f8hp-pro", "variant_group": "bf-f8hp-pro-family", ... }
{ "id": "bf-f8hp-pro-nrfb", "variant_group": "bf-f8hp-pro-family", ... }
```

A new top-level block describes each group and, crucially, maps each
identification answer to a concrete member id:

```json
"variant_groups": {
  "bf-f8hp-pro-family": {
    "name": "BTECH BF-F8HP Pro",
    "manufacturer": "BTECH",
    "firmware_page": "https://baofengtech.com/product/bf-f8hp-pro/",
    "question": "Which hardware version is your BF-F8HP Pro?",
    "steps": "With the radio OFF, hold the 8 key while turning it on. The display shows NRF or NRFB.",
    "options": [
      { "radio_id": "bf-f8hp-pro",      "label": "Display shows NRF" },
      { "radio_id": "bf-f8hp-pro-nrfb", "label": "Display shows NRFB" }
    ]
  }
}
```

`question`/`steps` are English source (mirroring how `radios.json` holds English
source for per-radio fields); `label` per option is English source too. The
option order is the presentation order; `radio_id` is the concrete entry the app
resolves to.

**Why this wins:**

- **Ids are preserved.** All four existing ids remain top-level entries, so
  `firmware_manifest.json` keys and `radio.<id>.<field>` translation keys keep
  resolving. This directly satisfies the "preserving existing ids matters"
  constraint.
- **Backward compatible by construction.** `variant_group` and
  `variant_groups` are additive. A released client running old `radios.json`
  simply doesn't see them and behaves exactly as today (two separate dropdown
  rows), while still resolving firmware from the unchanged remote manifest. New
  clients reading new `radios.json` get the walkthrough. No manifest migration.
- **Minimal blast radius downstream.** `download_and_extract`,
  `get_radio_firmware_info`, and the flash workers all still receive a concrete
  radio id; only the *selection* layer (dropdown + `_get_selected_radio`) grows
  group awareness. The safety guard from PR #21 stays untouched.

### Alternative: per-radio nested `variants` list

Collapse each family to one entry whose id is the model, with variants nested:

```json
{ "id": "bf-f8hp-pro", "variants": [
    { "variant_id": "nrf",  "firmware_filename_pattern": "NRF_ONLY_*.kdhx" },
    { "variant_id": "nrfb", "firmware_filename_pattern": "NRFB_ONLY_*.kdhx" } ] }
```

Conceptually cleaner, but it **breaks the compatibility surface**:

- The `bf-f8hp-pro-nrfb` and `rt-490-new` ids disappear as top-level keys.
  Released clients that look up `manifest["bf-f8hp-pro-nrfb"]` get nothing, and
  the per-variant firmware would have to be re-keyed inside a restructured
  manifest — a breaking manifest change that old clients can't read.
- Translation keys `radio.bf-f8hp-pro-nrfb.*` / `radio.rt-490-new.*` orphan;
  every catalog needs a coordinated re-key, and until then `t_radio_field`
  falls back to English.
- We'd have to invent an id scheme for nested variants anyway (`<id>#<variant>`)
  to keep the manifest and translations addressable — which is just
  `variant_group` linkage wearing a more disruptive costume.

The nested model would be the right call for a *greenfield* schema. Given four
ids already in the wild, the sibling-linkage model buys the same UX at zero
compatibility cost.

## Decision 2 — UX: where the picker lives and how "I don't know" is handled

### When/where

- **Dropdown collapses a group to one family row.** `FirmwareColumn` builds the
  choice list from `frame.radios`; grouped members are replaced by a single
  entry labelled by `variant_groups.<id>.name`. Ungrouped radios render as
  today. `_get_selected_radio()` maps the selected row back to either a concrete
  radio or an unresolved group.
- **The walkthrough appears on selection, in the existing info surface.**
  Selecting a family renders the group `question` + `steps` in the same
  hint/info panel that already shows bootloader keys and notes
  (`_format_radio_info` / `_set_hint`), plus one selectable option per variant
  and an explicit "I'm not sure." This reuses the panel the user is already
  reading while prepping the radio — no new modal on the happy path.
- **Download is gated, not guessed.** While a family is selected but no variant
  is resolved, the Download button is disabled and labelled to say identify
  first (new `button.identify_first` key). This mirrors the existing
  `_update_radio_info` pattern that already toggles the button between
  "Download v…", "Download Latest", and "No Direct URL." Once a variant is
  chosen, the flow proceeds identically to a normal single-variant radio,
  including the existing untested-radio confirmation dialog in `on_download`.

Rationale for selection-time (vs deferring the whole thing to the Download
click): the per-radio info panel is *already* the place identification prose
lives today, and gating the button is a pattern the frame already implements.
Deferring to a download-time modal would duplicate the info the panel must show
anyway and would leave the button in a misleading "ready" state.

### "I'm not sure" → safe default = stop

- Choosing "I'm not sure," or not answering, resolves to **no variant**.
  Download stays disabled; the app surfaces the group's `firmware_page` as a
  link ("Not sure? Confirm your hardware version here") so the user can check
  before risking a brick. This is the safe default because the failure mode is
  asymmetric: a wrong guess can permanently damage the receiver, whereas
  stopping only costs the user a lookup.
- The post-download `select_firmware_file` guard remains as a second line of
  defense for the case where the user picked a variant but the vendor repacked
  the bundle and the pattern no longer matches exactly one file.

## Decision 3 — i18n of identification steps

The existing mechanism: English source lives in `radios.json`; non-English
catalogs override per-radio strings under `radio.<id>.<field>`, resolved by
`t_radio_field(radio_id, field, fallback)` which falls back to the English
source. `TestRadioStringTranslations` enforces that every translatable field of
every radio has a key in all 7 non-English catalogs and that no value merely
echoes the English source.

Two string categories, two keying strategies:

1. **Per-variant answer labels** ("Display shows NRF", "Has channel-name
   editing") belong to a member radio, so they reuse `t_radio_field` with a new
   field name **`variant_label`**, keyed `radio.<id>.variant_label`. Adding
   `variant_label` to `TestRadioStringTranslations.TRANSLATABLE_FIELDS` makes
   the *existing* completeness + no-echo tests cover them for free across all
   languages.
2. **Group-level question and steps** are shared across members, so they key off
   the group, not any one radio. Add a documented sibling helper
   **`t_variant_field(group_id, field, fallback)`** keyed
   `variant_group.<group_id>.<field>` — a deliberate, minimal extension of the
   `t_radio_field` pattern (same fallback-to-English-source behavior, different
   namespace). A new test (mirroring `TestRadioStringTranslations`) enforces
   completeness + no-echo for `variant_group.*.question` and
   `variant_group.*.steps` in all 7 languages.

Rationale: reusing `radio.<id>.variant_label` for labels keeps the highest-churn
strings inside the already-enforced test with no new machinery; introducing one
sibling helper for the two genuinely group-scoped strings is the smallest
documented extension that keeps group strings addressable and testable. Both
helpers fall back to the English source in `radios.json`, so a missing
translation degrades to English rather than a raw key.

## Decision 4 — Backward compatibility

- **Old radios.json + remote manifest.** Released binaries embed their own
  `radios.json`. Because member ids are unchanged and the manifest keeps its
  current id-keyed shape, an old client keeps resolving firmware URLs and hashes
  from the remote manifest exactly as before; it just shows the two variant rows
  separately and relies on `notes` + the post-download guard (its current
  behavior). Nothing regresses.
- **New client + old cached manifest.** New selection logic only needs
  `radios.json` (local) to build the walkthrough; the manifest is consulted only
  after a concrete id is resolved, so a stale cached manifest is a non-issue.
- **Migration keeps `notes` populated** for both groups (possibly trimmed of the
  now-structured identification prose) so old clients that render `notes`
  continue to show identification guidance.

## Open questions

- Should the walkthrough be inline in the info panel (chosen) or a modal at
  download time for stronger "you must answer" enforcement? Inline is proposed;
  revisit if usability testing shows users skip it.
- Widget choice for the options (wx.RadioBox vs a compact dropdown) — left to
  implementation; must remain RTL- and translation-safe like the rest of the
  frame.
- Whether to generalize `firmware_filename_pattern` selection to key off the
  resolved variant option directly (removing per-member patterns) — deferred;
  keeping per-member patterns preserves the PR #21 guard semantics unchanged.
</content>
