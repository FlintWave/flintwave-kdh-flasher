# One-Click Test Reports

## Why

14 of the entries in `radios.json` are `tested: false`, including the entire
RT-950 Pro BTF protocol path, which has never been confirmed on real
hardware. `tested` flags only ever flip from a community member filing a
GitHub issue after a real flash. The app already builds and offers that
report — `show_test_report_dialog()` in `gui_dialogs.py` fires from
`_offer_test_report()` after every flash attempt in `gui_main.py`
(`_flash_thread` and `_flash_thread_btf`), prefilled with radio, firmware
file, result, OS, Python version, and a truncated log, opening
`github.com/.../issues/new` with `title`/`body`/`labels=test-report` query
params. Issue #20 (BF-F8HP Pro NRF confirmation, see `CHANGELOG.md` v26.07.0)
proves the mechanism works end-to-end.

But the mechanism has three gaps that blunt it as a steady source of
confirmations:

- **It nags.** The dialog fires unconditionally on every flash, success or
  failure, with no memory. Flash the same radio+firmware twice (e.g. batch
  flashing a stack of identical handsets) and the identical prompt appears
  every time — the classic path to users reflexively hitting Skip.
- **It isn't testable as a unit.** The URL and report-body construction
  live inline inside `show_test_report_dialog()`, a function that also
  constructs `wx.Dialog` widgets. `tests.py`'s `TestReportURLs` and
  `TestReportGeneration` re-derive the same logic by hand rather than
  calling the real code, so a change to the real body/URL builder isn't
  actually guarded by those tests.
- **There's no maintainer-side convention** connecting an incoming
  `test-report` issue back to a specific `radios.json` entry's `tested`
  flag, so triage is manual guesswork every time.

Failures are arguably the more valuable report (a failure on an untested
radio is exactly the signal maintainers need), so the offer should not be
success-only, but it does need to stop nagging once a given radio+firmware
combination has already been reported — or explicitly skipped.

## What Changes

- Extract report body and GitHub issue URL construction out of
  `show_test_report_dialog()` into pure, unit-testable functions (no wx
  dependency), so `tests.py` exercises the real code path instead of a
  hand-rolled copy.
- Add nag suppression: once a report has been submitted (or explicitly
  skipped) for a given radio id + firmware version, don't offer again for
  that same combination. Remembered in `~/.flintwave-flash/state.json`
  alongside the existing `last_flashed` record, via `firmware_manifest.py`'s
  `_load_state`/`_save_state`.
- Keep offering after both success and failure (unchanged), but gate the
  offer on the new suppression check.
- Add an explicit "don't ask again for this radio+firmware" affordance
  distinct from the existing per-instance Skip, and reflect this choice in
  the dialog/i18n copy.
- Document (in `design.md`, not code) a lightweight maintainer-side
  convention — the existing `test-report` label plus a `radio: <id>` line
  already present in the prefilled body — so a triaged report maps
  unambiguously to a `radios.json` `tested` flip.
- Add/extend i18n keys for the new "don't ask again" affordance across all
  8 catalogs (`translations/en.json` plus the 7 translated catalogs
  enumerated in `tests.py`'s `TestRadioStringTranslations.REQUIRED_LANGS`).
- Add tests: URL-length safety margin for the prefilled log payload, pure
  function coverage for body/URL construction, and nag-suppression state
  transitions (first flash prompts, repeat flash of the same
  radio+firmware doesn't, a different firmware version on the same radio
  prompts again).

No changes to `radios.json` `tested` values themselves — those only change
when a maintainer reviews and merges an actual community report.

## Impact

- Affected code: `gui_dialogs.py` (report dialog + new pure helpers),
  `gui_main.py` (`_offer_test_report` call sites in `_flash_thread` and
  `_flash_thread_btf`), `firmware_manifest.py` (new state key alongside
  `last_flashed`), `translations/*.json` (8 catalogs), `tests.py`.
- Affected specs: new capability `test-reporting`.
- No changes to the serial/flash protocol, `radios.json` data, or the
  GitHub issue submission mechanism itself (still a browser-opened
  `issues/new` URL — no network calls added, no telemetry).
