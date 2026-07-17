# Translation Review Process — Implementation Tasks

## 1. CONTRIBUTING.md: Add Translations Section

- [ ] Create `CONTRIBUTING.md` at the repo root with:
  
  ```markdown
  ## Translations
  
  ### How Translation Works
  
  FlintWave Flash ships with English (bundled) and 7 optional languages:
  - Chinese (Simplified): zh-CN
  - French: fr
  - German: de
  - Italian: it
  - Spanish: es
  - Arabic: ar (RTL layout)
  - Russian: ru
  
  English lives in `translations/en.json` and is the source of truth. 
  Non-English translations are in `translations/<code>.json` and are initially machine-translated.
  When you pick a non-English language in the app for the first time, it downloads from GitHub and caches locally.
  
  ### Safety-Critical Strings
  
  Some strings carry user-safety implications:
  
  1. **Radio bootloader instructions** (`radio.<id>.bootloader_keys`):
     - English source in `radios.json` defines the key-press sequence to enter bootloader mode
     - Translated versions must match *the intent* even if exact button names differ by region
     - Example: if English says "SK1 + SK2", the translation should match those button labels in that language/market
     - Errors here can cause users to fail flashing or inadvertently put the radio in wrong mode
  
  2. **Hardware variant warnings** (`radio.<id>.notes`):
     - Many radios have variants with incompatible firmware (BF-F8HP Pro NRF/NRFB, RT-490 old/new PCB)
     - The notes section explicitly warns against flashing the wrong variant
     - **Critical**: mistranslation here can lead to permanent bricking
     - Read the English notes carefully and ensure the warning is clear in the target language
  
  3. **Confirmation dialogs** (`dialog.confirm_single`, `dialog.confirm_batch_*`):
     - These are the final safety-check dialogs shown before flashing
     - They repeat the bootloader key sequence and emphasize "do not disconnect during flash"
     - Translation must preserve urgency and clarity
  
  4. **Untested radio warnings** (`dialog.untested_*`):
     - Warn users when flashing firmware to radios that haven't been validated with this tool
     - Translation must convey the risk appropriately
  
  ### Reviewing Translations
  
  #### Step 1: Find your language's tracking issue
  
  Each language has a GitHub issue tracking review progress:
  - [#N0] Chinese (zh-CN) Translation Review
  - [#N1] French (fr) Translation Review
  - [#N2] German (de) Translation Review
  - [#N3] Italian (it) Translation Review
  - [#N4] Spanish (es) Translation Review
  - [#N5] Arabic (ar) Translation Review
  - [#N6] Russian (ru) Translation Review
  
  Check the issue's checklist to see which key groups still need review.
  
  #### Step 2: Fix strings locally
  
  1. Clone the repo:
     ```bash
     git clone https://github.com/FlintWave/flintwave-kdh-flasher.git
     cd flintwave-kdh-flasher
     ```
  
  2. Edit `translations/<code>.json` with a text editor (not Google Translate).
     - Keep the JSON structure intact (keys, spacing, quotes)
     - Edit only the string values, not the keys
     - For radio.* fields, check `radios.json` for the English source
  
  3. Prioritize safety-critical strings first (radio.* fields, then dialog.confirm_*, dialog.untested_*).
  
  4. Tips for avoiding the "English echo" trap:
     - Tests reject translations identical to English
     - Don't just copy-paste the English value
     - If you can't translate a term (e.g., "SK1" in bootloader_keys), keep it as-is but translate the surrounding text
     - Some technical terms may not have standard translations; research your language's radio community first
  
  #### Step 3: Test locally (optional but recommended)
  
  ```bash
  # Run the test suite to catch completeness and echo errors:
  python3 tests.py TestRadioStringTranslations
  
  # Or run the full test suite:
  python3 tests.py
  ```
  
  This verifies:
  - All radio.* keys are present in your language
  - None of your translations are identical to the English source (the "echo" check)
  
  #### Step 4: Submit a PR
  
  1. Create a branch: `git checkout -b translate/<code>`
  2. Commit your changes:
     ```bash
     git add translations/<code>.json
     git commit -m "Review and fix <language> translation: <key groups reviewed>"
     ```
  3. Push and create a PR, linking to the tracking issue (e.g., "Addresses #N0").
  4. In the PR description, list which key groups you've reviewed:
     ```
     ## Translation Review: <language>
     
     Reviewed and fixed:
     - [x] radio.* (bootloader_keys, connector, notes)
     - [x] dialog.confirm_*, dialog.untested_*
     - [x] button, tooltip, hints
     ```
  
  #### Step 5: Maintainer merges and marks reviewed
  
  Once a language's safety-critical strings (radio.*, dialog.confirm_*, dialog.untested_*) have been reviewed by a native speaker:
  
  1. Maintainer sets `_meta.reviewed: true` in the catalog
  2. Maintainer closes the tracking issue
  3. The language picker in the app no longer shows "Machine translated — help review"
  
  ### Test Expectations
  
  Two tests gate translation PRs:
  
  1. **Completeness** (`test_every_radio_field_translated_in_every_lang`):
     - Every `radio.<id>.<field>` key in English must exist in every non-English catalog
     - Ensures no language is missing critical hardware or bootloader info
  
  2. **No Echo** (`test_translations_are_actually_translated`):
     - No `radio.<id>.<field>` value can be identical to the English source
     - Catches copy-paste errors and machine-translation failures
     - If a term (e.g., "K1 Kenwood 2-pin") has no translation, leave it as-is in quotes (to differ from the raw string)
  
  Both tests run on every PR. If they fail, the PR cannot merge.
  
  ### File Format
  
  Translation files are JSON:
  ```json
  {
    "_meta": {
      "language": "de",
      "language_label": "Deutsch",
      "reviewed": false,
      "source": "machine"
    },
    "app.title": "FlintWave Flash",
    "button.flash_firmware": "Firmware flashen",
    "radio.bf-f8hp-pro.bootloader_keys": "SK1 + SK2 ...",
    ...
  }
  ```
  
  The `_meta` section is metadata; the tests strip it before loading. When `_meta.reviewed` is set to `true`, it signals that a native speaker has checked all strings (especially safety-critical ones).
  ```

- [ ] Verify the file is readable and correctly formatted

## 2. Create Per-Language Tracking Issues

Create 7 GitHub issues (one per non-English language) with the following template:

### Issue Template

**Title**: `<Language> (‹code›) Translation Review`

**Body**:
```markdown
## Overview

The ‹Language› translation (`translations/‹code›.json`) is currently machine-translated and marked `_meta.reviewed: false`.

This issue tracks community review of the ‹Language› strings, prioritizing safety-critical ones:

## Key Groups (Priority Order)

### 🔴 CRITICAL — Safety-Critical Strings (Must be reviewed by a native speaker)

These strings affect user safety. Mistranslation can cause flashing failures or hardware damage.

- **radio.*.bootloader_keys** — ~7 keys, step-by-step bootloader mode instructions
  - [ ] Reviewed: all bootloader key sequences match English intent, clear for native speaker
  - Tracking: Opened [PR linking], merged [date]
  
- **radio.*.notes** — ~7 keys, hardware-variant warnings (e.g., "NRF vs NRFB not interchangeable")
  - [ ] Reviewed: all variant warnings are clear and warn against wrong firmware
  - Tracking: Opened [PR linking], merged [date]
  
- **dialog.confirm_single, dialog.confirm_batch_title, dialog.confirm_batch_body** — 3 keys
  - [ ] Reviewed: confirmation dialogs are clear, bootloader key sequence is legible, warnings preserve urgency
  - Tracking: Opened [PR linking], merged [date]
  
- **dialog.untested_title, dialog.untested_body, dialog.untested_warning** — 3 keys
  - [ ] Reviewed: untested radio warnings are clear and convey risk
  - Tracking: Opened [PR linking], merged [date]

### 🟡 IMPORTANT — UI Workflow Strings

Dialog text, error messages, and workflow hints. Affect user experience but not safety.

- **dialog.* (error dialogs, report submission)** — ~37 keys
  - [ ] Reviewed
  - Tracking: Opened [PR linking], merged [date]
  
- **hint.* (workflow instructions)** — ~26 keys
  - [ ] Reviewed
  - Tracking: Opened [PR linking], merged [date]
  
- **log.* (progress and diagnostic messages)** — ~59 keys
  - [ ] Reviewed
  - Tracking: Opened [PR linking], merged [date]

### 🟢 NICE-TO-HAVE — UI Chrome

Button labels, tooltips, status messages. Low-impact but nice to have correct.

- **button.* (button labels)** — 13 keys
  - [ ] Reviewed
  - Tracking: Opened [PR linking], merged [date]
  
- **tooltip.*,statusbar.*,titlebar.* (UI labels)** — ~15 keys
  - [ ] Reviewed
  - Tracking: Opened [PR linking], merged [date]
  
- **status.*, column.*, app.*, info.*, etc.** — ~20 keys
  - [ ] Reviewed
  - Tracking: Opened [PR linking], merged [date]

## When All Critical + Important Groups Are Reviewed

Once all 🔴 CRITICAL and 🟡 IMPORTANT groups are reviewed and merged:

1. A maintainer will set `_meta.reviewed: true` in `translations/‹code›.json`
2. The language picker in the app will no longer show "Machine translated — help review"
3. This issue will be closed

## How to Help

See [CONTRIBUTING.md → Translations](#translations) for the step-by-step review and PR process.

**We're looking for native speakers** who can:
- Spot awkward phrasing or mistranslations
- Ensure safety-critical warnings are clear in ‹Language›
- Review at least the 🔴 CRITICAL and 🟡 IMPORTANT groups

Even partial reviews welcome! Comment on the issue to claim a group so others know someone is working on it.
```

Create these 7 issues with language-specific titles:
- [ ] GitHub Issue #N0: Chinese (zh-CN) Translation Review
- [ ] GitHub Issue #N1: French (fr) Translation Review
- [ ] GitHub Issue #N2: German (de) Translation Review
- [ ] GitHub Issue #N3: Italian (it) Translation Review
- [ ] GitHub Issue #N4: Spanish (es) Translation Review
- [ ] GitHub Issue #N5: Arabic (ar) Translation Review
- [ ] GitHub Issue #N6: Russian (ru) Translation Review

## 3. Update _meta Convention in Existing Translations

All non-English translation files currently have `_meta.reviewed: false`. Document the convention:

- [ ] Update i18n.py docstring or inline comment explaining `_meta.reviewed`:
  - `true` = all strings (especially safety-critical: radio.*, dialog.confirm_*, dialog.untested_*) reviewed by native speaker
  - `false` = machine-translated, awaiting community review (see GitHub issues for tracking)
  - [ ] Add comment in en.json's _meta block:
    ```json
    "_meta": {
      "language": "en",
      "language_label": "English",
      "reviewed": true,
      "source": "canonical",
      "_comment": "Reviewed=true means all strings, especially safety-critical ones (radio.*, dialog.confirm_*, dialog.untested_*), have been checked by a native speaker"
    }
    ```

## 4. Optional: In-App Hint for Unreviewed Languages

Annotate the language picker dropdown to signal review status. This is optional but encourages contribution.

- [ ] In `gui_main.py` (or the language-picker component), locate the language selection dropdown or dialog
- [ ] When rendering a language option, append " — Machine translated, help review" if `_meta.reviewed: false`
  - Example: "Français — Machine translated, help review"
  - English always shows as "English" (no suffix)
  
  To implement:
  ```python
  # Pseudo-code in the language dialog:
  for code, label in i18n.LANGUAGES:
      if code == "en":
          display_label = label
      else:
          is_reviewed = load_reviewed_status(code)  # Read _meta.reviewed from cached/bundled file
          suffix = "" if is_reviewed else " — Machine translated, help review"
          display_label = f"{label}{suffix}"
  ```
  
  Helper function to check review status:
  - [ ] Add function in `i18n.py`: `is_reviewed(code: str) -> bool`
    - Check `_meta.reviewed` in the bundled or cached catalog for that language
    - Return `True` for en (always reviewed), `False` for any language marked `reviewed: false`
  
- [ ] Test that the label appears correctly in the language picker dropdown (manual verification)
- [ ] Verify that switching languages still works (no regression)
- [ ] Verify Arabic RTL layout still works with the additional text

## 5. Tests (if any)

The existing test suite (`tests.py`) already enforces:
- Completeness: all radio.* keys present in every language
- No echo: no radio.* value identical to English

No new tests are required for this proposal. However, maintainers may wish to add:

- [ ] (Optional) Test that `_meta.reviewed` is a bool (not null or string)
  - This prevents misconfigurations when a maintainer updates _meta

## 6. Documentation & Changelog

- [ ] Update `README.md` Languages section to mention the review process:
  ```markdown
  ### Languages
  
  … existing language list …
  
  The non-English catalogs are initially machine-translated. 
  Community native-speaker review is ongoing — see the [Translations](#translations) section of [CONTRIBUTING.md](CONTRIBUTING.md) for how to help.
  Unreviewed languages are marked in the language picker; reviewed languages have been checked by a native speaker.
  ```

- [ ] Update `CHANGELOG.md` in the target release section:
  ```
  - Add community translation review process with per-language tracking issues
  - Add Translations section to CONTRIBUTING.md with review workflow and test expectations
  - Update _meta convention: _meta.reviewed tracks native-speaker review status (safety-critical strings)
  - (Optional) Add in-app hint in language picker for unreviewed languages
  ```

- [ ] Update `openspec/project.md` if clarification is needed on translation safety:
  ```markdown
  ### Internationalization & Review
  
  English is bundled; 7 other languages download on demand and cache locally.
  Translations are organized by key prefix (radio.*, dialog.*, log.*, etc.).
  Safety-critical strings (bootloader_keys, hardware warnings, confirmation dialogs) are tracked in per-language GitHub issues
  and marked _meta.reviewed: true only after native-speaker review.
  See CONTRIBUTING.md → Translations for the review process.
  ```

## 7. Final Verification

- [ ] `python3 tests.py` passes (no new tests break existing ones)
- [ ] CONTRIBUTING.md is readable and clear (ask a non-native speaker to review the translation section)
- [ ] GitHub issues are created and properly formatted
- [ ] README.md and CHANGELOG.md updates are merged
- [ ] (If in-app hint is implemented) Language picker displays correctly with new labels
- [ ] (If in-app hint is implemented) Arabic RTL layout still displays correctly
- [ ] PR created with summary, linking to this proposal
