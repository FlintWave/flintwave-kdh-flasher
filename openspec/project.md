# FlintWave Flash — Project Context

## Purpose

Cross-platform (Linux/Windows/macOS) wxPython GUI for flashing firmware to
handheld radios that use the KDH bootloader (`.kdhx` firmware) or the BTF
protocol (`.BTF`, Radtel RT-950 Pro). Downloads vendor firmware bundles,
verifies them, and drives the serial flash protocol, with a multilingual UI
(8 languages, RTL support).

## Tech stack

- Python 3.12, wxPython 4.2 (GUI), pyserial (radio I/O), requests (downloads)
- No package manager manifest — stdlib + the four deps above; PyInstaller builds
- Tests: single `tests.py`, stdlib `unittest` (~160 tests). GUI tests skip
  headless and run in CI. `mock_bootloader.py` emulates a strict KDH/BTF radio
  for end-to-end protocol tests without hardware.
- CI: `.github/workflows/tests.yml` (push/PR), `build-release.yml` (on `v*`
  tags → AppImage/deb/rpm/exe/dmg via PyInstaller + fpm/Inno/dmgbuild)

## Conventions

- Radio definitions live in `radios.json` (English source of truth);
  per-radio strings are translated in `translations/<code>.json` keyed
  `radio.<id>.<field>`. Tests enforce translation completeness and reject
  English-echo "translations".
- `firmware_manifest.json` is fetched remotely from master by released
  clients (raw.githubusercontent.com) to discover new firmware without an app
  update; keyed by radio id — ids are a compatibility surface.
- GUI decomposition in progress: `gui_main.py` (FlasherFrame, ~1,800 lines)
  is being split into components (`gui_titlebar`, `gui_statusbar`,
  `gui_columns`, `gui_workflow`, `gui_handset`, `gui_dialogs`, `gui_themes`).
  Construction and behavior move out; the frame keeps thin delegators so
  worker-thread call sites don't churn.
- Changes touching threads + serial + live widgets need a hardware test
  before merge (see PR #19's checklist pattern); protocol logic must be
  testable headlessly (injectable dependencies, pure helpers).
- Versioning: CalVer `vYY.MM.patch` (e.g. v26.07.0). `CHANGELOG.md` section
  per release. Squash merges titled "<summary> (#PR)".

## Domain notes

- Vendors silently repack bundles at unchanged URLs and ship multiple
  hardware-variant firmware files that are NOT interchangeable (BF-F8HP Pro
  NRF/NRFB; RT-490 old/new PCB). Flashing the wrong variant can brick a
  radio. Treat firmware selection as safety-critical.
- Hardware variants are modelled as a first-class concept: sibling radio
  entries that represent variants of one physical model share a
  `variant_group` id and are described by a top-level `variant_groups` block
  (`name`, `manufacturer`, `firmware_page`, identification `question`/`steps`,
  and an ordered `options[]` mapping each answer to a member `radio_id`). The
  UI collapses a group to one dropdown row and walks the user through
  identifying their hardware before download; it refuses to guess and stops
  safe (Download disabled + `firmware_page` link) on "I'm not sure". Group
  `question`/`steps` translate through `variant_group.<group_id>.<field>`
  (helper `t_variant_field`); per-answer labels translate through
  `radio.<id>.variant_label` (helper `t_radio_field`). Member ids stay a
  compatibility surface — never rename or remove them.
- Many `radios.json` entries are `tested: false` — community test reports
  (GitHub issues) are how entries get confirmed.
- Non-English catalogs are machine-translated until a native speaker reviews
  them (`_meta.reviewed`, per-language `translation-review` issues,
  CONTRIBUTING.md → Translations). Safety-critical strings (bootloader keys,
  variant warnings, confirm/untested dialogs) get review priority.
