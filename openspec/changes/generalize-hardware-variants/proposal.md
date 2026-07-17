# Generalize hardware variants

## Why

Vendors ship a single firmware bundle (one URL) that contains multiple
hardware-variant firmware files which are **not interchangeable** — flashing
the wrong one can brick the radio. The app currently handles this ad hoc, and
the only thing standing between a user and a bricked radio is whether they read
a paragraph of free-text `notes`.

Concrete evidence in the current tree:

- **BF-F8HP Pro NRF/NRFB.** `radios.json` carries two sibling entries,
  `bf-f8hp-pro` (`NRF_ONLY_*.kdhx`) and `bf-f8hp-pro-nrfb` (`NRFB_ONLY_*.kdhx`),
  both pointing at the *same* `F8HPPRO-V53-Update-Bundle.zip`. The only
  identification guidance — "with the radio OFF, hold the 8 key while turning it
  on; the display shows NRF or NRFB" — lives inside the `notes` string
  (`radios.json:15`, `:28`). PR #21 (`a460154`) added this split after
  BaofengTech silently repacked the bundle into two files, which broke the
  previous single-file download outright.
- **Radtel RT-490 old/new PCB.** `rt-490` (old PCB, real `firmware_url`) vs
  `rt-490-new` (new PCB, `firmware_url: null`). Identification — "If your RT-490
  has channel name editing, you have the new PCB" — again lives only in `notes`
  (`radios.json:67`, `:80`), and the new-PCB notes explicitly warn that wrong
  firmware "will brick the radio."
- **The guard added in PR #21 only fails safe, it doesn't guide.**
  `firmware_download.select_firmware_file()` refuses to guess when a pattern
  matches zero or multiple files (`firmware_download.py:291-328`), and
  `TestFirmwareVariantSelection` locks that in. But the guard triggers *after*
  download, and its remedy is a wall of text telling the user to "pick the radio
  entry that matches your hardware version" — the app never actually asks which
  hardware they have.

So the identification knowledge exists, but it is (a) unstructured, (b) buried
in prose the user may skip, and (c) invisible to the UI, which cannot gate on
it. The goal is a first-class *variants* concept so the app **walks the user
through** identifying their hardware before it selects firmware, instead of
hoping they read the notes.

## What Changes

- **Add a variant-group concept to `radios.json`** (additive schema): sibling
  radio entries that represent hardware variants of one physical model are
  linked by a shared `variant_group` id, and a top-level `variant_groups`
  block describes each group's display name, the identification question, the
  ordered identification steps, and which member id each answer selects. Member
  entries keep their existing ids, urls, and `firmware_filename_pattern`.
  **Existing ids are preserved** (`bf-f8hp-pro`, `bf-f8hp-pro-nrfb`, `rt-490`,
  `rt-490-new`) — see design.md for why this rules out a nested-`variants` list.
- **Collapse grouped siblings into one dropdown row.** The firmware dropdown
  shows a single family line per variant group (e.g. "BTECH BF-F8HP Pro")
  instead of one row per variant. Selecting a family launches the
  identification walkthrough rather than pre-selecting a variant. Ungrouped
  radios are unaffected.
  - *(Internal, not a compatibility surface, but behavior-changing:*
    `_get_selected_radio()`'s dropdown-index→radio mapping and the
    `gui_columns.FirmwareColumn` dropdown population must learn about groups.)*
- **Add a variant-identification walkthrough.** When a family is selected, the
  app presents the group's question and steps and offers one choice per variant
  plus an explicit **"I'm not sure"**. Download stays disabled until a concrete
  variant is chosen; choosing one resolves to the member radio id, and every
  downstream step (manifest lookup, `download_and_extract`, flashing) runs
  unchanged against that resolved id.
- **Refuse to guess a variant.** The app SHALL NOT auto-pick a variant or fall
  through to "the first one." The post-download `select_firmware_file` guard is
  retained as defense in depth.
- **Safe default for "I'm not sure."** Selecting "I'm not sure" (or leaving the
  question unanswered) SHALL stop before download, keep Download disabled, and
  surface a link to the group's `firmware_page` so the user can confirm their
  hardware.
- **Make identification steps translatable.** The question and steps are
  translated through the existing `radio.<id>.<field>` / `t_radio_field`
  machinery, extended with a documented sibling for group-level strings
  (`variant_group.<group_id>.<field>`). Per-variant answer labels reuse
  `t_radio_field` with a new `variant_label` field so they slot into the
  existing translation-completeness test across all 7 non-English languages.
- **Migrate both existing pairs** (BF-F8HP Pro, RT-490) onto the new structure,
  moving the identification prose out of `notes` and into structured, translated
  identification steps.
- **No breaking change to the remote manifest.** `firmware_manifest.json` stays
  keyed by the same radio ids; released clients keep resolving firmware exactly
  as they do today.

## Impact

- **Affected spec (delta):** `firmware-selection` — new requirements for the
  variant walkthrough, refusal to guess, unknown-variant safe default, and i18n
  of identification steps.
- **Affected code:**
  - `radios.json` — additive `variant_group` field on member entries +
    top-level `variant_groups` block; migrate BF-F8HP Pro and RT-490.
  - `firmware_download.py` — `load_radios`/`get_radio_by_id` gain group-aware
    helpers (`load_variant_groups`, `get_variant_group`, `resolve_variant`);
    `select_firmware_file` guard unchanged.
  - `i18n.py` — add documented `t_variant_field(group_id, field, fallback)`
    keyed `variant_group.<group_id>.<field>`; `t_radio_field` gains a
    `variant_label` field usage.
  - `gui_columns.py` (`FirmwareColumn`) — collapse grouped siblings into one
    family row.
  - `gui_main.py` — `_get_selected_radio`, `on_radio_changed`,
    `_update_radio_info`, `_format_radio_info`, `on_download` learn to render
    the walkthrough, gate Download on a resolved variant, and honor the
    "I'm not sure" safe default.
  - `translations/*.json` (7 languages) — add
    `variant_group.<group_id>.{question,steps}` and
    `radio.<id>.variant_label` keys for both migrated groups.
  - `tests.py` — extend `TestRadioDefinitions` (variant-group integrity),
    `TestRadioStringTranslations` (new translatable fields), and add coverage
    for `resolve_variant` / the unknown-variant safe default.
- **Compatibility surfaces (must stay stable):**
  - Radio **ids** — consumed by `firmware_manifest.json` keys and by
    `radio.<id>.<field>` translation keys. Preserved.
  - **Remote manifest** fetched from `master` by released clients. Unchanged
    shape; old clients ignore the new radios.json fields.
</content>
</invoke>
