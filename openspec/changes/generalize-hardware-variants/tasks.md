# Tasks: generalize hardware variants

Ordered so the test suite stays green after every numbered group. Data +
helpers land before the UI consumes them; migration of live entries happens only
once both the readers and the tests understand the new shape.

## 1. Schema + test scaffolding (data model, no behavior change)

- [ ] 1.1 Document the `variant_group` member field and top-level
      `variant_groups` block (with `name`, `manufacturer`, `firmware_page`,
      `question`, `steps`, `options[].radio_id`, `options[].label`) in a comment
      in `radios.json` and in `openspec/project.md` domain notes.
- [ ] 1.2 Add `TestRadioDefinitions` cases: every `variant_group` referenced by
      a member exists in `variant_groups`; every group `option.radio_id`
      resolves to a real, distinct radio id; group members all share the same
      `variant_group`; option order is deterministic. (Passes vacuously until
      groups are added.)
- [ ] 1.3 Add a `TestVariantGroups` case class asserting each group has a
      non-empty `question` and `steps`, at least two options, and a
      `firmware_page`.

## 2. Data helpers in firmware_download.py (pure, headless-testable)

- [ ] 2.1 Add `load_variant_groups()` and `get_variant_group(group_id)` reading
      the new `variant_groups` block (empty/None-safe when absent).
- [ ] 2.2 Add `resolve_variant(group_id, radio_id)` → the concrete member radio
      dict, and a `variant_members(group_id)` helper listing member ids in
      option order. Keep `get_radio_by_id` and `select_firmware_file` unchanged.
- [ ] 2.3 Unit-test the helpers, including the unknown / "not sure" path
      (resolve returns None → callers must stop; assert no firmware id is
      produced).

## 3. i18n plumbing

- [ ] 3.1 Add `t_variant_field(group_id, field, fallback)` to `i18n.py`, keyed
      `variant_group.<group_id>.<field>`, falling back to the English source in
      `variant_groups`. Document it next to `t_radio_field`.
- [ ] 3.2 Add `variant_label` to
      `TestRadioStringTranslations.TRANSLATABLE_FIELDS` (completeness + no-echo
      now cover per-variant labels once labels exist).
- [ ] 3.3 Add a translation-completeness test (mirroring
      `TestRadioStringTranslations`) for `variant_group.<id>.question` and
      `variant_group.<id>.steps` across all 7 non-English catalogs, rejecting
      English echoes.
- [ ] 3.4 Add UI string keys to `translations/en.json`:
      `button.identify_first`, `info.variant_question`, `info.variant_steps`,
      `info.variant_not_sure`, `info.variant_confirm_link`.

## 4. Migrate the BF-F8HP Pro group

- [ ] 4.1 Add `variant_group: "bf-f8hp-pro-family"` to `bf-f8hp-pro` and
      `bf-f8hp-pro-nrfb`; add the `bf-f8hp-pro-family` entry to `variant_groups`
      (question/steps from the current NRF/NRFB notes; options → the two ids
      with labels "Display shows NRF" / "Display shows NRFB"). Trim the
      duplicated identification prose from each member's `notes` but keep the
      remaining cabling/pressure guidance.
- [ ] 4.2 Add `radio.bf-f8hp-pro.variant_label`,
      `radio.bf-f8hp-pro-nrfb.variant_label`, and
      `variant_group.bf-f8hp-pro-family.{question,steps}` to all 7 non-English
      catalogs (real translations, not English echoes). Reuse the existing
      NRF/NRFB translated prose already in each catalog as the source.
- [ ] 4.3 Run the suite — schema + translation tests must pass with the group in
      place; ids unchanged so `TestFirmwareVariantSelection` still passes.

## 5. Migrate the RT-490 group

- [ ] 5.1 Add `variant_group: "rt-490-family"` to `rt-490` and `rt-490-new`; add
      the `rt-490-family` group (question "Which PCB revision?", steps about
      channel-name editing; options → `rt-490` "No channel-name editing (old
      PCB)" / `rt-490-new` "Has channel-name editing (new PCB)"). Keep
      `rt-490-new`'s `firmware_url: null` behavior intact.
- [ ] 5.2 Add the matching `variant_label` and
      `variant_group.rt-490-family.{question,steps}` keys to all 7 catalogs.
- [ ] 5.3 Run the suite green.

## 6. Selection layer (gui_columns + gui_main)

- [ ] 6.1 `gui_columns.FirmwareColumn`: build the dropdown so each variant group
      contributes exactly one family row (label = translated group `name`);
      ungrouped radios render as today. Record the row→(radio | group) mapping.
- [ ] 6.2 `gui_main._get_selected_radio()`: return the concrete radio for
      ungrouped rows and for a group whose variant is already resolved; return a
      sentinel/None (with the group) when a family row is selected but
      unresolved.
- [ ] 6.3 `_update_radio_info()` / `_format_radio_info()`: when an unresolved
      group is selected, render the translated `question` + `steps` + one option
      per variant + "I'm not sure", and set the Download button disabled with the
      `button.identify_first` label. Reuse `t_variant_field` and the
      `variant_label` lookups.
- [ ] 6.4 Wire the option control so choosing a variant resolves the group to
      that member id and re-runs `_update_radio_info` (Download re-enables and
      shows the normal version label); choosing "I'm not sure" keeps Download
      disabled and shows the `firmware_page` confirm link.

## 7. Download/flash gating

- [ ] 7.1 `on_download`: guard that a group selection has a resolved variant
      before proceeding; otherwise no-op (button should already be disabled —
      belt and suspenders). Everything after resolution is unchanged and runs on
      the concrete id (untested dialog, `download_and_extract`, hashing, flash).
- [ ] 7.2 Confirm the post-download `select_firmware_file` guard still fires for
      repack/pattern-mismatch cases (regression check, no code change expected).

## 8. Tests + docs closeout

- [ ] 8.1 Add GUI-level (or headless helper) tests: selecting a family disables
      Download; resolving a variant enables it; "I'm not sure" keeps it disabled
      and exposes the confirm link.
- [ ] 8.2 Full suite green in all 7 languages; update `CHANGELOG.md` and any
      `USAGE.md` note about picking your hardware variant.
- [ ] 8.3 Verify a simulated old-client path: unchanged remote manifest keys
      still resolve for `bf-f8hp-pro`, `bf-f8hp-pro-nrfb`, `rt-490`,
      `rt-490-new` (compatibility-surface assertion).
</content>
