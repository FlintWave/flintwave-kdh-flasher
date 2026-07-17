# firmware-selection (delta)

## ADDED Requirements

### Requirement: Hardware-variant identification walkthrough

When a selected radio belongs to a hardware-variant group (multiple
non-interchangeable firmware files behind one model), the app SHALL walk the
user through identifying their hardware version before any firmware is selected
or downloaded, instead of relying on free-text notes.

Variant groups SHALL be defined in `radios.json` such that member radio entries
retain their existing ids and are linked by a shared `variant_group`, and each
group declares an identification `question`, ordered identification `steps`, and
an ordered set of options each mapping one answer to exactly one member radio id.

#### Scenario: Selecting a variant group presents the identification question

- **GIVEN** a radio dropdown that lists a variant group (e.g. "BTECH BF-F8HP Pro")
  as a single family row
- **WHEN** the user selects that family row
- **THEN** the app SHALL display the group's identification question and steps in
  the language currently active
- **AND** SHALL offer one selectable option per variant plus an explicit
  "I'm not sure" choice
- **AND** SHALL keep the Download control disabled until a concrete variant is
  chosen.

#### Scenario: Choosing a variant resolves to that member and enables download

- **GIVEN** a variant group is selected with its identification question shown
- **WHEN** the user chooses one of the variant options
- **THEN** the app SHALL resolve the selection to that option's member radio id
- **AND** SHALL enable the Download control and proceed exactly as for a
  single-variant radio (manifest lookup, download, extraction, and flashing all
  operating on the resolved id).

### Requirement: The app must not guess a hardware variant

The app SHALL NOT auto-select a hardware variant, and SHALL NOT fall back to
"the first matching file" when a bundle contains multiple non-interchangeable
firmware files.

#### Scenario: No variant chosen means no firmware selected

- **GIVEN** a variant group is selected but no variant option has been chosen
- **WHEN** the user attempts to download
- **THEN** the app SHALL NOT begin a download
- **AND** SHALL NOT select any firmware file on the user's behalf.

#### Scenario: Post-download guard still refuses ambiguous bundles

- **GIVEN** a resolved variant whose `firmware_filename_pattern` matches zero or
  more than one file in the downloaded bundle (e.g. after a vendor repack)
- **WHEN** extraction completes
- **THEN** the app SHALL raise an error that names the files actually present and
  SHALL NOT flash any of them.

### Requirement: Unknown-variant safe default

When the user cannot identify their hardware variant, the app SHALL fail safe by
stopping before download rather than guessing, because flashing the wrong
variant can permanently damage the radio.

#### Scenario: "I'm not sure" stops and links to the vendor page

- **GIVEN** a variant group is selected
- **WHEN** the user chooses "I'm not sure" (or leaves the question unanswered)
- **THEN** the app SHALL keep the Download control disabled
- **AND** SHALL surface a link to the group's `firmware_page` so the user can
  confirm their hardware version before proceeding.

### Requirement: Translatable identification steps

Variant identification strings SHALL be translatable through the existing
per-radio translation mechanism (`radio.<id>.<field>` resolved by
`t_radio_field`) or a documented extension of it, so identification guidance is
presented in the user's language in all shipped locales.

Per-variant answer labels SHALL be keyed `radio.<id>.variant_label` and covered
by the existing per-radio translation-completeness enforcement. Group-level
question and steps SHALL be keyed `variant_group.<group_id>.<field>` and resolved
by a documented sibling helper that falls back to the English source in
`radios.json`.

#### Scenario: Identification question and steps render in the active language

- **GIVEN** a non-English language is active and its catalog contains the group's
  `variant_group.<group_id>.question` and `.steps` keys
- **WHEN** the user selects that variant group
- **THEN** the app SHALL display the translated question and steps
- **AND** SHALL display each option using its translated
  `radio.<id>.variant_label`.

#### Scenario: Missing translation falls back to English source, never a raw key

- **GIVEN** a language catalog is missing a variant identification key
- **WHEN** that string is rendered
- **THEN** the app SHALL fall back to the English source defined in `radios.json`
  rather than showing the raw translation key.

#### Scenario: Translation completeness is enforced across all shipped locales

- **GIVEN** the test suite runs
- **THEN** it SHALL assert that every `variant_label` and every group
  `question`/`steps` string has a non-echoed translation in each of the 7
  non-English catalogs, failing if any locale is missing a key or merely echoes
  the English source.
</content>
