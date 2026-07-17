## ADDED Requirements

### Requirement: Cross-platform test execution
The test suite (`tests.py`) SHALL run to completion on `ubuntu-latest`,
`windows-latest`, and `macos-latest` on every push to `master` and every
pull request, with wxPython installed and importable on all three, so that
GUI tests exercise real widget behavior on each platform instead of
self-skipping.

#### Scenario: PR touching GUI code runs on all three OSes
- **WHEN** a pull request is opened or updated
- **THEN** the `test` job runs as a matrix over `ubuntu-latest`,
  `windows-latest`, and `macos-latest` with `fail-fast: false`, and each
  matrix leg installs wxPython using the OS-appropriate recipe (apt on
  Linux; `pip install wxPython --prefer-binary` on Windows/macOS)

#### Scenario: GUI tests execute rather than skip on every OS
- **WHEN** the test suite runs on any of the three matrix legs
- **THEN** tests that call `self.skipTest(...)` when `gui_themes`,
  `gui_main`, or `gui_handset` are not importable SHALL NOT skip — wx SHALL
  be importable on all three legs, and the reported test count SHALL
  include those GUI-dependent tests as executed, not skipped

#### Scenario: Linux leg still runs headless
- **WHEN** the Linux matrix leg runs the test suite
- **THEN** it SHALL invoke the suite under `xvfb-run` (or equivalent) so
  that importing `wx.adv` succeeds without a real display, exactly as it
  does today

#### Scenario: One OS failing does not hide the others
- **WHEN** the test suite fails on exactly one OS in the matrix (e.g.
  Windows)
- **THEN** the Linux and macOS legs SHALL still run to completion and
  report their own pass/fail status, rather than being cancelled

### Requirement: Workflow files are linted
Every workflow file under `.github/workflows/` SHALL be checked by
`actionlint` on every push and pull request, independent of and
non-duplicative of the existing CodeQL `Analyze (actions)` job.

#### Scenario: A workflow YAML/shell error is introduced
- **WHEN** a pull request modifies a `.github/workflows/*.yml` file to
  introduce a schema error (invalid key), a broken `${{ }}` expression, or
  a shellcheck-flagged bug in a `run:` block
- **THEN** the actionlint job SHALL fail on that pull request before merge

#### Scenario: actionlint job scope stays read-only
- **WHEN** the actionlint job runs
- **THEN** it SHALL run with `permissions: contents: read` only — it never
  needs write access to the repository

### Requirement: Firmware manifest drift is detected on a schedule
A weekly scheduled workflow SHALL re-verify every `firmware_manifest.json`
entry that has both a non-null `firmware_url` and a non-null
`firmware_sha256` against the live vendor bundle, and SHALL surface any
detected drift as a GitHub issue rather than failing silently or
duplicating existing reports.

#### Scenario: Vendor silently repacks a pinned bundle
- **WHEN** the weekly drift-check workflow downloads the bundle at a pinned
  entry's `firmware_url` and its recomputed SHA-256 does not match the
  manifest's `firmware_sha256`
- **THEN** the workflow SHALL search open issues for an existing
  `firmware-drift`-labeled issue referencing that radio id before creating
  one, SHALL open a new issue only if none exists, and the issue body SHALL
  contain the expected hash, the actual hash, the URL, and the detection
  timestamp

#### Scenario: Drift already reported and still present
- **WHEN** the weekly workflow re-detects the same hash mismatch for a
  radio id that already has an open `firmware-drift` issue
- **THEN** the workflow SHALL add a comment to the existing issue instead
  of opening a duplicate issue

#### Scenario: Previously drifted entry is corrected
- **WHEN** the weekly workflow finds that a radio id with an open
  `firmware-drift` issue now matches its pinned hash again (manifest was
  updated, or the vendor reverted the bundle)
- **THEN** the workflow SHALL comment on and close the existing issue for
  that radio id

#### Scenario: Vendor server is unreachable or slow
- **WHEN** downloading a manifest entry's bundle fails after the
  workflow's configured retries (timeout, connection error, non-2xx
  response)
- **THEN** the workflow SHALL record this as "could not verify" for that
  entry and SHALL NOT open or update a `firmware-drift` issue for it — a
  transient vendor/network failure SHALL NOT be reported as drift

#### Scenario: Entries out of scope for drift checking
- **WHEN** a manifest entry has a null `firmware_url` or a null
  `firmware_sha256`
- **THEN** the drift-check workflow SHALL skip that entry without error and
  without opening any issue for it

#### Scenario: Vendor servers are not hammered
- **WHEN** the drift-check workflow runs
- **THEN** it SHALL run on a weekly cron schedule (plus manual
  `workflow_dispatch`), SHALL process manifest entries sequentially, and
  SHALL NOT run on every push or pull request
