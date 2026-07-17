## Why

CI has three gaps that would each have caught (or would help catch) real problems:

1. **`tests.yml` only runs on `ubuntu-latest`.** Most users run the released
   Windows/macOS binaries (see `build-release.yml`), but wxPython's behavior —
   layout, native widget quirks, theme rendering, RTL — genuinely differs per
   platform. A regression that only breaks the Windows or macOS build is
   invisible to CI until a user reports it against a tagged release.

2. **Workflow YAML has no linter.** CodeQL's `Analyze (actions)` job already
   scans `.github/workflows/*.yml` (visible in the repo's code-scanning
   analyses: category `/language:actions`, 17 rules), but that pack is a
   *security* scanner — injection via untrusted `${{ }}` expansion in `run:`,
   excessive `permissions:`, credential handling. It does not validate YAML
   schema, catch shellcheck-class bugs in `run:` blocks, or flag broken
   `needs:`/context references. `actionlint` covers exactly that gap and
   would have caught it earlier, as syntax/typo errors in workflow files
   currently only surface when a job actually runs (or fails to).

3. **Firmware integrity has no ongoing check.** `pin-firmware-hashes` (see
   `openspec/changes/pin-firmware-hashes/`) adds pinned SHA-256 hashes to
   `firmware_manifest.json` and verifies them at *download time* — but that
   only protects a user who happens to download after a repack is noticed.
   The 2026-07-10 BaofengTech silent repack (unchanged URL, changed bytes)
   went undetected for a week because nothing checked the manifest against
   the live vendor bundle proactively. A scheduled drift check closes that
   window from "whenever a user notices" to "within a week."

## What Changes

- **Cross-platform test matrix**: extend `tests.yml` to run the full suite
  (including the ~8 GUI tests that currently self-skip headless) on
  `windows-latest` and `macos-latest`, reusing the dependency-install recipes
  `build-release.yml` already has working for each OS. `fail-fast: false` so
  one platform's failure doesn't hide the others' results.
- **actionlint job**: add a fast, dependency-light job that lints every
  `.github/workflows/*.yml` file on every push/PR, distinct from and
  complementary to the existing CodeQL `actions` analysis.
- **Scheduled manifest-drift workflow**: a new weekly-cron workflow that
  re-downloads every `firmware_manifest.json` entry with a non-null
  `firmware_url` *and* non-null `firmware_sha256`, recomputes SHA-256, and
  compares against the pinned value. On mismatch it opens (or updates, never
  duplicates) a GitHub issue; if a previously-flagged entry now matches again
  it closes the issue.

## Capabilities

### New Capabilities
- `ci`: cross-platform test execution, workflow linting, and scheduled
  firmware-manifest drift detection — the non-functional guarantees the
  project's CI provides about itself and about the firmware supply chain.

## Impact

- **Affected files**: `.github/workflows/tests.yml` (matrix expansion),
  new `.github/workflows/actionlint.yml`, new
  `.github/workflows/manifest-drift.yml`. No application source code changes.
- **CI cost/time**: test job count goes from 1 to 3 (roughly 3x total
  runner-minutes for the test suite); actionlint job is seconds-scale;
  drift workflow runs once a week, off the PR critical path.
- **Permissions**: the drift workflow needs `issues: write` (new) in
  addition to `contents: read`; it is the first workflow in this repo to
  write anything via `GITHUB_TOKEN`.
- **External dependency**: the drift workflow depends on vendor servers
  (BaofengTech, Radtel/Shopify CDN) being reachable; failures there must
  degrade to "couldn't verify" rather than a false drift report or a hard
  CI failure.
- **No user-facing or protocol changes.**
