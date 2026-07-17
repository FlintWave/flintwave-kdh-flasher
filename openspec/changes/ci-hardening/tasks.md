## 1. Cross-platform test matrix (`tests.yml`)

- [ ] 1.1 Add `strategy: fail-fast: false, matrix: os: [ubuntu-latest, windows-latest, macos-latest]` to the existing `test` job
- [ ] 1.2 Split the "Install dependencies" step into three OS-conditional steps (`if: runner.os == 'Linux' / 'Windows' / 'macOS'`), reusing the exact recipes from `build-release.yml`:
  - Linux: `apt-get install python3-wxgtk4.0 python3-serial python3-requests xvfb` (system Python, unchanged from today)
  - Windows: `actions/setup-python@v6` (3.12) + `pip install pyserial requests wxPython --prefer-binary`
  - macOS: `actions/setup-python@v6` (3.12) + `pip install pyserial requests wxPython --prefer-binary`
- [ ] 1.3 Make the "Run test suite" step OS-conditional: `xvfb-run -a python3 tests.py` on Linux, plain `python tests.py` (Windows uses `python`, not `python3`) on Windows/macOS
- [ ] 1.4 Keep the "Show environment" sanity step (`import wx, serial, requests`) on all three legs so a broken wx install fails fast with a clear message instead of a wall of skipped-test noise
- [ ] 1.5 Confirm the ~8 GUI tests that currently skip headless (`gui_themes`, `gui_main`/`HINT_STATES`, `gui_handset` status constants, etc. — see `tests.py`) actually execute (not skip) on all three OSes; check the run's test count/output, not just exit code
- [ ] 1.6 Update the README `Tests` badge/CI description if it currently implies Linux-only coverage
- [ ] 1.7 Land and observe at least one full matrix run on a real PR before merging to `master`, to catch any OS-specific test flakiness (e.g. path separators, timing) before it's the default gate

## 2. actionlint job

- [ ] 2.1 Add a job (new `.github/workflows/actionlint.yml`, or a job appended to `tests.yml`) that runs `rhysd/actionlint` (official Docker/binary action) against `.github/workflows/*.yml`
- [ ] 2.2 Scope its trigger to `push`/`pull_request` like `tests.yml`, and to changes touching `.github/workflows/**` at minimum (may run unconditionally too, since it's fast)
- [ ] 2.3 Run it once against the current `tests.yml`/`build-release.yml` as a smoke test; fix any pre-existing findings (e.g. unquoted `run:` expressions, unpinned action shas) surfaced, or explicitly note them as accepted/deferred
- [ ] 2.4 Confirm this job's `permissions:` block is read-only (`contents: read`) — it never needs write access
- [ ] 2.5 Document in the job (a comment) that this is intentionally separate from and non-duplicative of the existing CodeQL `Analyze (actions)` job — see `design.md` §4 for the conclusion

## 3. Scheduled manifest-drift workflow

- [ ] 3.1 Create `.github/workflows/manifest-drift.yml` with `on: schedule` (weekly cron) + `on: workflow_dispatch` for manual runs
- [ ] 3.2 Set `permissions: issues: write, contents: read` (narrowest scope that can search/create/comment/close issues)
- [ ] 3.3 Write the drift-check script (inline Python step, reusing `firmware_download.py`'s chunked SHA-256 logic rather than re-implementing hashing) that:
  - [ ] 3.3.1 Loads `firmware_manifest.json`, iterates entries where `firmware_url is not null and firmware_sha256 is not null`
  - [ ] 3.3.2 Downloads each with a bounded timeout and a small retry/backoff count; sets a descriptive `User-Agent`
  - [ ] 3.3.3 Computes SHA-256 of the download and compares to the pinned value
  - [ ] 3.3.4 Distinguishes "verified drift" (hash mismatch) from "could not verify" (download failed after retries) and never reports the latter as drift
  - [ ] 3.3.5 Processes entries sequentially (not in parallel) to avoid hammering vendor servers
- [ ] 3.4 Wire up issue lifecycle via `gh` CLI:
  - [ ] 3.4.1 On verified drift: `gh issue list --search "<radio_id> in:title label:firmware-drift" --state open` first
  - [ ] 3.4.2 No match → `gh issue create` labeled `firmware-drift`, titled `Firmware drift: <radio_id>`, body with expected hash, actual hash, URL, timestamp
  - [ ] 3.4.3 Match found → `gh issue comment` on the existing issue instead of creating a duplicate
  - [ ] 3.4.4 On a subsequent run where a previously-flagged entry now matches again → comment and `gh issue close` the open issue for that radio id
- [ ] 3.5 Create the `firmware-drift` label in the repo (or have the workflow create it on first use via `gh label create` if missing)
- [ ] 3.6 Add a "could not verify" log/summary output (job summary, not an issue) for entries whose download failed, so maintainers can distinguish "vendor CDN was flaky this week" from silence
- [ ] 3.7 Trigger a manual `workflow_dispatch` run against the current manifest as a smoke test; confirm it correctly reports no drift for the two pinned entries (`bf-f8hp-pro`, `bf-f8hp-pro-nrfb`) and correctly skips the three unpinned/null-URL entries
- [ ] 3.8 Note in the workflow file (comment) that full manifest coverage depends on `pin-firmware-hashes` landing — until all entries are pinned, this workflow only protects the currently-pinned subset

## 4. Cross-cutting

- [ ] 4.1 `openspec validate ci-hardening --strict` passes
- [ ] 4.2 `CHANGELOG.md` entry noting the expanded test matrix, actionlint gate, and scheduled drift check
- [ ] 4.3 Each group above (1, 2, 3) is landable and reviewable as its own PR — no group depends on another shipping first
