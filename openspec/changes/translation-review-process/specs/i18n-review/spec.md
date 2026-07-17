# Translation Review Process — Specification

## Overview

This specification defines the process and system requirements for tracking and managing community review of non-English translations in FlintWave Flash. The goal is to ensure that safety-critical strings (bootloader instructions, hardware warnings, confirmation dialogs) are validated by native speakers before a language is marked as reviewed.

---

## ADDED Requirements

### Requirement: Safety-Critical String Identification

**Requirement**: The system SHALL identify and prioritize safety-critical translation keys that demand native-speaker review.

#### Scenario: Bootloader Instruction Keys
- **Given**: A radio entry in `radios.json` with a `bootloader_keys` field
- **When**: That field is translated into a non-English language and stored in `translations/<code>.json` under key `radio.<id>.bootloader_keys`
- **Then**: The translation SHALL be reviewed by a native speaker to ensure the step-by-step instructions are clear and match the English intent (e.g., button labels, key sequences, timing)
- **Rationale**: Mistakes in bootloader instructions cause flashing failures; users may inadvertently put the radio in the wrong mode

#### Scenario: Hardware Variant Warnings
- **Given**: A radio entry in `radios.json` with a `notes` field containing variant warnings (e.g., "NRF and NRFB firmware are NOT interchangeable")
- **When**: That field is translated into a non-English language and stored as `radio.<id>.notes`
- **Then**: The translation SHALL be reviewed by a native speaker to ensure the warning is unambiguous and conveys the risk (bricking if wrong variant is selected)
- **Rationale**: Mistranslation of variant warnings can lead to permanent hardware damage

#### Scenario: Confirmation Dialog Keys
- **Given**: Confirmation dialogs (`dialog.confirm_single`, `dialog.confirm_batch_*`) that show before flashing begins
- **When**: These dialogs are translated and stored in `translations/<code>.json`
- **Then**: The translation SHALL preserve the urgency of safety warnings ("Do not disconnect," "green Rx LED," bootloader key sequence legibility)
- **Rationale**: These dialogs are the last chance to warn users before an irreversible operation

#### Scenario: Untested Radio Warnings
- **Given**: Dialogs that warn users about untested radios (`dialog.untested_*`)
- **When**: Translated into a non-English language
- **Then**: The translation SHALL convey the risk of flashing firmware on untested hardware
- **Rationale**: Untested radios may not follow the KDH protocol exactly; mistranslation of this risk could encourage users to ignore warnings

---

### Requirement: Reviewed State Tracking

**Requirement**: The system SHALL track the review status of each language catalog via the `_meta.reviewed` field.

#### Scenario: Whole-Catalog Reviewed State
- **Given**: A language catalog `translations/<code>.json`
- **When**: A native speaker has reviewed and fixed all safety-critical strings (radio.*, dialog.confirm_*, dialog.untested_*), plus any obvious echo or completeness issues
- **Then**: The catalog's `_meta.reviewed` field SHALL be set to `true`
- **Rationale**: A boolean is simple to check in code (i18n.py), simple to update (one edit, no partial states), and sufficient for safety: if reviewed=true, all safety-critical keys have been validated

#### Scenario: Unreviewed Catalogs
- **Given**: Any non-English language at initial import or after a new radio is added
- **When**: The catalog has not been reviewed by a native speaker
- **Then**: `_meta.reviewed` SHALL be `false`
- **Consequence**: The language is marked as "Machine translated — help review" in the UI (optional but recommended)
- **Rationale**: Users can choose to use the language knowing it may need improvement; contributors see a call to action

#### Scenario: Fallback Behavior
- **Given**: A language with `_meta.reviewed: false`
- **When**: A string is missing or not yet translated
- **Then**: The app SHALL fall back to English for that key (existing i18n.py behavior)
- **Consequence**: The app remains usable in unreviewed languages; only reviewed languages guarantee complete, safe translations
- **Rationale**: Graceful degradation; no lockout for incomplete translations

---

### Requirement: Contribution Documentation

**Requirement**: The system SHALL document the translation review process for community contributors.

#### Scenario: Translator Onboarding
- **Given**: A contributor who speaks ‹Language› fluently and wants to review that language's translation
- **When**: They consult `CONTRIBUTING.md`
- **Then**: The Translations section SHALL provide:
  1. A clear definition of "safety-critical strings" (radio.*, dialog.confirm_*, dialog.untested_*)
  2. Step-by-step instructions: find the tracking issue, fix strings locally, run tests, submit a PR
  3. Examples of how to avoid the "English echo" trap (translations must differ from English)
  4. Explanation of test expectations: completeness and no-echo checks
- **Rationale**: Lower barrier to entry; clear expectations; actionable guidance

#### Scenario: Test Expectations Documented
- **Given**: A translator reading `CONTRIBUTING.md`
- **When**: They review the "Test Expectations" section
- **Then**: They SHALL understand:
  1. The `test_every_radio_field_translated_in_every_lang` test requires all `radio.<id>.<field>` keys present in every language
  2. The `test_translations_are_actually_translated` test rejects any `radio.<id>.<field>` value identical to English (catches model echo and copy-paste)
  3. Both tests must pass for a PR to merge
- **Consequence**: Translators can run tests locally before submitting a PR and fix issues immediately
- **Rationale**: Tests provide fast feedback; translators unblock themselves without waiting for maintainer review

#### Scenario: Safety-Critical Prioritization
- **Given**: A translator with limited time
- **When**: They review `CONTRIBUTING.md`
- **Then**: The Translations section SHALL prioritize key groups:
  1. 🔴 CRITICAL: radio.*, dialog.confirm_*, dialog.untested_*
  2. 🟡 IMPORTANT: dialog.* (other), hint.*, log.*
  3. 🟢 NICE-TO-HAVE: button.*, tooltip.*, status.*, etc.
- **Consequence**: A partial review (e.g., CRITICAL + IMPORTANT) is clearly valuable; nice-to-have groups are deprioritized but available for thoroughness
- **Rationale**: Incremental contribution model; encourages participation even if not comprehensive

---

### Requirement: Per-Language Review Tracking

**Requirement**: The system SHALL provide per-language GitHub issues to track review progress and invite community participation.

#### Scenario: Issue Creation
- **Given**: Each of the 7 non-English languages (zh-CN, fr, de, it, es, ar, ru)
- **When**: The translation review process is initiated
- **Then**: A GitHub issue SHALL be created for each language with:
  1. Title: ‹Language› (‹code›) Translation Review
  2. A prioritized checklist (CRITICAL, IMPORTANT, NICE-TO-HAVE groups)
  3. Instructions for contributors (link to CONTRIBUTING.md)
  4. A space to track merged PRs per group
- **Consequence**: Contributors see at a glance which groups need review; maintainers can track progress
- **Rationale**: Public, trackable way to invite and coordinate community contributions

#### Scenario: Progress Tracking
- **Given**: A PR that reviews and fixes some key groups in ‹Language›
- **When**: The PR is merged
- **Then**: The maintainer updates the tracking issue to mark those groups as reviewed and link the PR
- **Consequence**: Issue checklist reflects merged work; contributors see progress; the next reviewer knows what's left
- **Rationale**: Transparency and coordination; prevents duplicate work

#### Scenario: Issue Closure
- **Given**: All CRITICAL and IMPORTANT groups for a language have been reviewed and merged
- **When**: `_meta.reviewed` is set to `true` in the language's JSON
- **Then**: The tracking issue SHALL be closed
- **Consequence**: The language is marked as reviewed; the language picker no longer shows "Machine translated" hint
- **Rationale**: Clear end state; signals to users that the language is native-reviewed

---

### Requirement: In-App Language Picker Hint (Optional)

**Requirement**: The language picker MAY display a hint for unreviewed languages to encourage contribution.

#### Scenario: Language Label for Unreviewed Languages
- **Given**: A language where `_meta.reviewed: false` (e.g., French)
- **When**: The user opens the language picker dialog or dropdown
- **Then**: The language label MAY be annotated with " — Machine translated, help review"
  - Example: "Français — Machine translated, help review"
- **Consequence**: Users are aware the language may need improvement; they see an implicit call to review
- **Rationale**: Low-friction way to invite contributions; contextual and non-intrusive

#### Scenario: No Hint for Reviewed Languages
- **Given**: A language where `_meta.reviewed: true` (or English, which is always reviewed)
- **When**: The user opens the language picker
- **Then**: The label shows normally without a hint
- **Consequence**: Reviewed languages stand out implicitly (no suffix)
- **Rationale**: Simplicity; no clutter for reviewed languages

---

### Requirement: Test Suite Quality Gates (Existing)

**Requirement**: The system SHALL enforce translation quality via the existing test suite.

#### Scenario: Completeness Check
- **Given**: A PR that modifies a non-English translation file
- **When**: Tests run (`python3 tests.py TestRadioStringTranslations`)
- **Then**: The `test_every_radio_field_translated_in_every_lang` test SHALL verify:
  - For every radio in `radios.json` with a `bootloader_keys`, `connector`, or `notes` field
  - And for every required language (zh-CN, fr, de, it, es, ar, ru)
  - The key `radio.<id>.<field>` exists in `translations/<code>.json`
- **Consequence**: Missing radio.* translations block the PR
- **Rationale**: Guarantees no language has incomplete radio metadata

#### Scenario: English Echo Prevention
- **Given**: A PR that modifies a non-English translation file
- **When**: Tests run
- **Then**: The `test_translations_are_actually_translated` test SHALL verify:
  - For every `radio.<id>.<field>` key
  - In every required language
  - The translated value is NOT identical to the English source (modulo whitespace)
- **Consequence**: Exact copies of English strings block the PR
- **Rationale**: Catches machine-translation failures and copy-paste errors; ensures effort has been made to translate

---

## Non-Requirements

- **Automated machine translation**: This spec does not mandate automated translation tooling. Translations are assumed to be reviewed and fixed manually by native speakers.
- **Translation memory or TMS**: No external translation management system is required. GitHub issues and the existing file structure are sufficient.
- **Translation of in-app changelogs or release notes**: Only the UI strings (`translations/*.json`) are in scope. Release notes stay in English.
- **Versioning of _meta**: No version field is added to `_meta`; `_meta.reviewed` is a boolean, not versioned. Breaking changes (e.g., restructuring keys) are out of scope.

---

## Design Rationale

### Why `_meta.reviewed` as a Whole-Catalog Boolean?

**Alternative considered**: Per-section granularity (e.g., `_meta.sections.radio_reviewed: true`, `_meta.sections.dialog_reviewed: true`).

**Decision**: Whole-catalog boolean.

**Rationale**:
1. **Simplicity**: A single bool is trivial to check in code (`if i18n.is_reviewed(code)`), simple for maintainers to update, and clear to users
2. **Sufficient safety**: Tests enforce completeness and no-echo for all strings, so any drift is caught quickly
3. **Signal clarity**: Reviewed=true means all strings (especially safety-critical ones) have been validated by a native speaker; no partial states
4. **Low maintenance**: No need to track or reconcile multiple sub-sections; avoids state inconsistency (e.g., radio reviewed but dialogs not)

If granular tracking becomes necessary in the future, per-section fields can be added to `_meta` without breaking this spec; the simple boolean remains the primary gate.

### Why GitHub Issues for Tracking?

**Alternative considered**: Wiki page, spreadsheet, dedicated tool.

**Decision**: GitHub issues (one per language).

**Rationale**:
1. **Decentralized coordination**: Issues show work in progress; contributors claim groups to avoid duplication
2. **Audit trail**: Issue history and linked PRs provide a clear record of who reviewed what and when
3. **Integration with CI/CD**: PR links in issue updates create natural traceability
4. **Low barrier**: No new tools or logins required; everyone uses GitHub anyway
5. **Community invitation**: Public issues invite participation; easier to find than a hidden wiki

### Why CONTRIBUTING.md?

**Alternative considered**: Separate translation guide, in-app help, video tutorials.

**Decision**: CONTRIBUTING.md.

**Rationale**:
1. **Standard location**: Developers and contributors expect contribution guidelines in CONTRIBUTING.md
2. **Accessible offline**: Works without network; can be read before cloning
3. **Version control**: Changes to the process are tracked in git and easy to reference
4. **Searchable**: Easy to link from README and issues

Additional materials (wiki, videos) can complement CONTRIBUTING.md but are not primary.

---

## Implementation Phases

### Phase 1: Process & Documentation (Minimal)
1. Create CONTRIBUTING.md with Translations section
2. Create 7 per-language GitHub issues with checklists
3. Update README.md to mention the review process
4. Document `_meta.reviewed` convention in code comments

**Impact**: Low effort, immediate invitation for contributions. Existing tests already enforce quality.

### Phase 2: In-App Hint (Optional, Nice-to-Have)
1. Add `is_reviewed(code)` helper to i18n.py
2. Annotate language labels in picker with "Machine translated" hint for unreviewed languages
3. Test that labels display and language switching still works

**Impact**: Minimal code (10-20 lines), visual signal to users about review status.

### Phase 3: Maintenance (Ongoing)
1. When a PR reviews a language's key groups, maintainer marks issue checklist items as done
2. When all CRITICAL and IMPORTANT groups are reviewed, maintainer sets `_meta.reviewed: true` and closes the issue
3. If a new radio is added to radios.json, maintainers add the corresponding radio.* keys to all translation files and mark existing languages as needing review again

---

## Testing & Validation

### Manual Testing Checklist
- [ ] Verify CONTRIBUTING.md is clear and correct (have someone non-native read it)
- [ ] Verify GitHub issues have correct links and formatting
- [ ] Verify existing test suite still passes: `python3 tests.py`
- [ ] (If in-app hint implemented) Verify language picker displays correctly with new labels
- [ ] (If in-app hint implemented) Verify Arabic RTL layout works with longer labels
- [ ] (If in-app hint implemented) Verify language switching still works

### Automated Testing
- Existing tests (`TestRadioStringTranslations`) automatically enforce completeness and no-echo
- No new automated tests required for the tracking process itself (GitHub issues are not part of the codebase)

---

## Success Criteria

1. **Adoption**: At least one native speaker per language submits a PR reviewing and fixing CRITICAL + IMPORTANT groups within 60 days
2. **Coverage**: At least 5 of 7 languages reach `_meta.reviewed: true` (at least CRITICAL + IMPORTANT groups reviewed)
3. **Quality**: All merged PRs pass existing test suite; zero echo failures; zero completeness failures
4. **Process clarity**: Contributors report that CONTRIBUTING.md is clear and they can run tests and submit PRs without friction
5. **Sustainability**: Maintainers can update tracking issues efficiently; adding a new radio doesn't derail the process

---

## Appendix A: Example Translation Checklist (per-language issue)

```markdown
## Chinese (zh-CN) Translation Review

[ ] CRITICAL — Safety-Critical Strings
  [ ] radio.*.bootloader_keys (7 keys) — [PR #NNN]
  [ ] radio.*.notes (7 keys) — [PR #NNN]
  [ ] dialog.confirm_* (3 keys) — [PR #NNN]
  [ ] dialog.untested_* (3 keys) — [PR #NNN]

[ ] IMPORTANT — UI Workflow Strings
  [ ] dialog.* other (37 keys) — [PR #OOO]
  [ ] hint.* (26 keys) — [PR #OOO]
  [ ] log.* (59 keys) — [PR #PPP]

[ ] NICE-TO-HAVE — UI Chrome
  [ ] button.* (13 keys) — [PR #QQQ]
  [ ] tooltip, statusbar, titlebar, status, column, app, info, etc. (20 keys) — [PR #QQQ]

**When CRITICAL + IMPORTANT are done**: A maintainer will set `_meta.reviewed: true` and close this issue.
```

---

## Appendix B: i18n.py Integration

Helper function to add to `i18n.py`:

```python
def is_reviewed(code: str) -> bool:
    """Check if a language catalog has been reviewed by a native speaker.
    
    Returns True if _meta.reviewed is true in the language's bundled or cached catalog.
    English is always considered reviewed.
    """
    if code == "en":
        return True
    
    # Check bundled catalog first (for testing)
    bundled_path = os.path.join(_bundled_translations_dir(), f"{code}.json")
    data = _read_json_file(bundled_path)
    if data and data.get("_meta", {}).get("reviewed") is True:
        return True
    
    # Check cached catalog
    cached = _load_cached(code)
    if cached:
        # Note: _load_cached strips _meta, so we need to re-read with _meta
        cache_path = os.path.join(_cache_translations_dir(), f"{code}.json")
        data = _read_json_file(cache_path)
        if data and data.get("_meta", {}).get("reviewed") is True:
            return True
    
    return False
```

Usage in language picker:

```python
# In the language picker UI code:
for code, label in i18n.LANGUAGES:
    if code == "en":
        display_label = label
    else:
        suffix = "" if i18n.is_reviewed(code) else " — Machine translated, help review"
        display_label = f"{label}{suffix}"
    # Render display_label in the dropdown
```
