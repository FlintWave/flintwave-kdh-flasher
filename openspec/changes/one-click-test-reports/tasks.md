# Tasks: One-Click Test Reports

## 1. Extract pure report-construction functions

- [ ] 1.1 In `gui_dialogs.py`, extract the body-building logic currently
      inline in `show_test_report_dialog()` into a standalone function,
      e.g. `build_report_body(radio_name, firmware_path, success,
      error_msg, log_content)` — no `wx` references, returns a plain
      string.
- [ ] 1.2 Extract the subject/title construction into a standalone
      function, e.g. `build_report_subject(radio_name, success)`.
- [ ] 1.3 Extract the GitHub issue URL construction into a standalone
      function, e.g. `build_report_url(title, body)`, returning the full
      `https://github.com/.../issues/new?...` string.
- [ ] 1.4 Update `show_test_report_dialog()` to call these three
      functions instead of inlining the logic, keeping its own scope to
      widget construction and layout.

## 2. Nag-suppression state

- [ ] 2.1 In `firmware_manifest.py`, add `mark_test_report(radio_id,
      version, status)` writing into a new top-level `test_reports` block
      in state (`state["test_reports"][radio_id][version] = status`),
      following the same load/mutate/`_save_state` pattern as
      `record_flash`.
- [ ] 2.2 Add `get_test_report_status(radio_id, version)` returning the
      stored status string or `None`, mirroring `get_last_flashed`.
- [ ] 2.3 Define and use a fallback version sentinel (e.g. `"unknown"`)
      when `file_version` is falsy, in both the mark and get paths, so
      suppression still works for firmware whose version can't be parsed
      from the filename.

## 3. Wire suppression into the flash-completion path

- [ ] 3.1 In `gui_main.py`, before calling `show_test_report_dialog` from
      `_offer_test_report`, check `fm.get_test_report_status(radio["id"],
      file_version)`; if it is `"submitted"` or `"skipped"`, return
      without showing the dialog.
- [ ] 3.2 Thread `file_version` (already computed earlier in
      `_flash_thread`/`_flash_thread_btf` via
      `fv.extract_version_from_filename`) through to
      `_offer_test_report` so the same value used for `record_flash` is
      used for suppression lookups — avoid re-deriving it twice.
- [ ] 3.3 Confirm both call sites (`_flash_thread` success/failure,
      `_flash_thread_btf` success/failure) pass the radio dict and
      version through consistently.

## 4. "Don't ask again" affordance in the dialog

- [ ] 4.1 Add a `wx.CheckBox` to the report dialog, unchecked by default,
      labeled via a new i18n key (see section 6).
- [ ] 4.2 On Submit: always call `mark_test_report(radio_id, version,
      "submitted")` regardless of the checkbox (a submitted report always
      suppresses future offers for that combination).
- [ ] 4.3 On Skip: call `mark_test_report(radio_id, version, "skipped")`
      only if the checkbox is checked; otherwise leave state untouched so
      a plain Skip keeps prompting on future flashes.
- [ ] 4.4 Pass `radio_id` and `version` into `show_test_report_dialog` (it
      currently takes `radio_name`, not the id) so it can call
      `mark_test_report` directly, or return the checkbox/button outcome
      to the caller for `_offer_test_report` to persist — pick whichever
      keeps `gui_dialogs.py` free of a `firmware_manifest` import if that
      matters for the existing module layering (check current imports
      before deciding).

## 5. URL length safety

- [ ] 5.1 Add a test that builds a worst-case log tail (2000 characters,
      all newlines or another maximally-expanding character) through
      `build_report_body` + `build_report_url` and asserts the resulting
      URL length stays under 8000 characters.
- [ ] 5.2 If the worst case exceeds budget, reduce the raw log truncation
      length (currently 2000 chars in `show_test_report_dialog`, now
      relocated into `build_report_body`) until it fits, and note the new
      constant.

## 6. i18n keys (all 8 catalogs: `en` plus the 7 in
   `TestRadioStringTranslations.REQUIRED_LANGS` — `zh-CN`, `fr`, `de`,
   `it`, `es`, `ar`, `ru`)

- [ ] 6.1 Add `dialog.report.dont_ask_again` ("Don't ask again for this
      radio and firmware version" or equivalent) to `translations/en.json`.
- [ ] 6.2 Add the same key, properly translated (not English-echoed), to
      `translations/zh-CN.json`, `fr.json`, `de.json`, `it.json`,
      `es.json`, `ar.json`, `ru.json`.
- [ ] 6.3 Run/extend `tests.py`'s translation-completeness checks (pattern
      matches `TestRadioStringTranslations`) to also cover
      `dialog.report.*` keys across all 7 non-English catalogs, catching
      both missing keys and English-echoed values.

## 7. Tests

- [ ] 7.1 Update `TestReportURLs` and `TestReportGeneration` in
      `tests.py` to call the real `build_report_body` /
      `build_report_subject` / `build_report_url` functions from
      `gui_dialogs` instead of re-deriving the logic inline.
- [ ] 7.2 Add tests for `mark_test_report` / `get_test_report_status` in
      `firmware_manifest.py`: fresh state returns `None`; marking
      `"submitted"` then querying returns `"submitted"`; a different
      version for the same radio id returns `None`; missing/falsy version
      uses the fallback sentinel consistently between mark and get.
- [ ] 7.3 Add a test for the URL length budget (see 5.1) as a permanent
      regression guard.
- [ ] 7.4 Add a GUI-level (or thin integration) test asserting
      `_offer_test_report` does not invoke `show_test_report_dialog` when
      `get_test_report_status` already returns a non-`None` value for the
      given radio+version — mock/monkeypatch the dialog call the way
      existing GUI tests in `tests.py` skip/stub headless widget calls.

## 8. Docs

- [ ] 8.1 Add a `CHANGELOG.md` entry under an "Unreleased" or next-version
      section once implemented (not part of this proposal's scope to
      write the entry now, but flag it so release notes aren't skipped).
