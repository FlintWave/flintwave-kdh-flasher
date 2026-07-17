## Context

`tests.yml` today runs one job on `ubuntu-latest`: it installs wxPython via
the apt package `python3-wxgtk4.0` (using the *system* Python, not
`actions/setup-python`, specifically to avoid an interpreter mismatch between
a pip-installed Python and an apt-installed wx binary extension), then runs
`xvfb-run -a python3 tests.py` because `gui_themes`/`gui_main` import
`wx.adv` and that import alone needs a display, even off-screen.

`build-release.yml` already has three OS-specific recipes for getting
wxPython installed and importable, because it builds PyInstaller binaries for
all three OSes on tag push:

| OS | Python source | wx install |
|---|---|---|
| Linux | `apt python3-wxgtk4.0` (system Python) | apt, not pip |
| Windows | `actions/setup-python@v6` (3.12) | `pip install wxPython --prefer-binary` |
| macOS | `actions/setup-python@v6` (3.12) | `pip install wxPython --prefer-binary` |

This proposal reuses those exact recipes for the test matrix instead of
inventing new ones — the release workflow is the existing proof that each
install path works.

There is no top-level CI job today that lints the workflow YAML itself, and
no scheduled job at all (both existing workflows are triggered by push/PR/tag
only).

## Goals / Non-Goals

**Goals:**
- Run the real (non-skipped) test suite, GUI tests included, on all three
  OSes users actually run the app on.
- Keep PR feedback latency close to today's (one wx install + one full
  `tests.py` run, ~a few minutes) rather than 3x-ing wall-clock time.
- Lint workflow YAML on every push/PR without duplicating what CodeQL's
  `actions` analysis already does.
- Detect a silent vendor firmware repack within a week, automatically,
  without spamming maintainers with duplicate or false-positive issues.

**Non-Goals:**
- Multi-Python-version matrix (e.g. 3.11/3.12/3.13). The project targets a
  single Python 3.12 (per `openspec/project.md`); wx's platform-specific
  behavior is the axis under test here, not Python-version compatibility.
  Out of scope for this change.
- Splitting `tests.py` into a GUI suite and a non-GUI suite as separate
  files/entry points. The existing per-test `skipTest`/`skipUnless` pattern
  is left as-is; "GUI tests run" is a side effect of wx being importable in
  the environment, not a separate `pytest -k` selection.
- Retrying or "self-healing" firmware URLs. Drift detection only reports;
  a human still decides whether a repack is legitimate (update the pin) or
  suspicious (investigate).
- Auto-updating `firmware_manifest.json` hashes. The drift workflow never
  writes to the manifest — that stays a human-reviewed PR, consistent with
  `pin-firmware-hashes`' model of hashes as a reviewed, pinned surface.

## Decisions

### 1. Test matrix shape

```yaml
strategy:
  fail-fast: false
  matrix:
    os: [ubuntu-latest, windows-latest, macos-latest]
```

One job, one Python version (3.12) per OS — 3 jobs total, not 3×N. Each
matrix leg's "install dependencies" step is OS-conditional (`if:
runner.os == 'Linux'` / `'Windows'` / `'macOS'`), lifted verbatim from
`build-release.yml`'s three install recipes, so there is exactly one place
(`build-release.yml`) that has ever had to solve "how do you get wx on this
OS" from scratch; `tests.yml` just reuses the answer.

`fail-fast: false` is a deliberate trade-off: without it, a flaky/failing
Windows leg would cancel the in-progress macOS and Linux legs too, and a
contributor debugging "is this Linux-only?" would get no macOS/Windows
signal at all. The cost is that a genuinely broken PR burns full
runner-minutes on all three OSes instead of failing fast on the first. Given
this repo's low PR volume and the value of "which OS(es) does this break"
as debugging signal, correctness-of-signal wins over saving runner-minutes.

Kept at one Python version deliberately (see Non-Goals) — a 3 OS × 2+ Python
matrix would 6x today's CI cost for a variable (Python version) this project
has never varied in production or in `build-release.yml`.

### 2. Headless display handling per OS

Only Linux GitHub-hosted runners lack a display server; `xvfb-run -a` stays
Linux-only, exactly as today. Windows and macOS GitHub-hosted runners already
run an interactive desktop session (Windows: a real desktop session; macOS:
WindowServer is up), so wx widgets can be created without any headless
shim — no `xvfb` equivalent is needed or available for those runners. This
means the matrix's "run tests" step must branch only on how it invokes
Python (`xvfb-run -a python3 tests.py` on Linux vs. plain `python tests.py`
elsewhere), not on any other headless-specific setup.

### 3. wx install cost/flakiness per OS

- **Linux**: apt install of a prebuilt `.deb` — fast (~seconds), no compile,
  no flakiness beyond normal apt mirror hiccups. Unchanged from today.
- **Windows/macOS**: `pip install wxPython --prefer-binary` pulls a wheel
  from PyPI when one exists for the runner's Python/OS/arch combination
  (both do, for CPython 3.12 on Windows x64 and macOS, per
  `build-release.yml` already doing this successfully for releases). This is
  the same install path already exercised by every tagged release build, so
  its cost/flakiness profile is known: a wheel pull, not a source build.
  Should PyPI lack a wheel for a given runner image update, the job would
  fall back to a source build (slow, needs system headers) — treated as an
  existing, shared risk with `build-release.yml`, not something new this
  change introduces. No extra pinning beyond what `build-release.yml` already
  uses.
- Optionally, `actions/setup-python`'s built-in `cache: pip` can be added on
  the Windows/macOS legs to avoid re-resolving/downloading the same wheel on
  every run; left as a follow-up rather than blocking this change, since it's
  a speed optimization, not correctness.

### 4. actionlint vs. CodeQL `Analyze (actions)` — no duplication

Checked what's already running: `gh api repos/.../code-scanning/analyses`
shows a `codeql:analyze` job with `category: /language:actions` running
alongside `/language:python` on every push/PR (17 actions rules, 43 python
rules, both currently 0 findings). That is CodeQL's relatively new "Actions"
language pack. It is a **security** scanner:

- Script/code injection via untrusted `${{ github.event.*, github.head_ref,
  ... }}` expansion inline in a `run:` shell block
- Overly-broad `permissions:` blocks or missing least-privilege scoping
- Risky trigger patterns (e.g. `pull_request_target` combined with checkout
  of untrusted code)
- Credential/secret exposure patterns

`actionlint` is a **schema/lint** tool. It does not overlap with the above;
it instead catches:

- Invalid YAML / wrong keys / wrong types against the workflow schema
- Broken `${{ }}` expression syntax, references to undefined contexts,
  typos in `needs:`/`matrix.*`/`steps.<id>.outputs.*`
- shellcheck run over every `run:` block (quoting bugs, unset variables,
  etc. in the bash steps this repo already leans on heavily for AppImage/
  fpm/Inno Setup scripting)
- Dead job references, unreachable steps, deprecated syntax (e.g.
  `::set-output`)

**Conclusion**: zero meaningful overlap. CodeQL's actions pack has never
been a substitute for a YAML/shell linter and vice versa; both stay. This
proposal adds `actionlint` as its own job (`.github/workflows/actionlint.yml`
or a job inside `tests.yml`) using the official `rhysd/actionlint`
Docker/binary action, running only against `.github/workflows/*.yml`,
independent of and non-blocking-on the CodeQL workflow.

### 5. Manifest-drift issue lifecycle

Trigger: `on.schedule` weekly cron (e.g. Monday 06:00 UTC) plus
`workflow_dispatch` for on-demand manual runs. Runs on `ubuntu-latest`; no wx
needed (pure `requests` + `hashlib`, reusing `firmware_download.py`'s
existing chunked-hash logic rather than re-implementing it).

Per manifest entry with non-null `firmware_url` **and** non-null
`firmware_sha256` (entries with either null are out of scope for this
workflow — a null hash is `pin-firmware-hashes`' concern, a null URL has
nothing to download):

1. Download with a bounded timeout (connect + read) and a small number of
   retries with backoff (vendor servers are known to be slow/flaky; this is
   a weekly job, not latency-sensitive, so patience is cheap). A descriptive
   `User-Agent` identifies the traffic as the project's bot, not a scraper
   trying to look like a browser.
2. Distinguish two outcomes and never conflate them:
   - **Verified drift**: download succeeded, SHA-256 differs from the pin.
   - **Could not verify**: download failed after retries (network/vendor
     issue). This is logged and optionally surfaced as a lower-urgency
     "unreachable" note, but MUST NOT be reported as drift — a flaky vendor
     CDN must never manufacture a false "silent repack" alert.
3. On verified drift: search open issues (via `gh issue list --search
   "in:title <radio_id> label:firmware-drift" --state open`) before creating
   anything.
   - No existing issue → open one, labeled `firmware-drift`, titled
     `Firmware drift: <radio_id>`, body has expected hash, actual hash,
     URL, and timestamp.
   - Existing issue → append a comment with the latest observation
     (timestamp + hash) instead of opening a duplicate. This mirrors the
     idempotent "does it already exist? update, don't duplicate" pattern
     `build-release.yml`'s `release` job already uses for re-triggered tag
     pushes (`gh release view` → update vs. create).
4. On a subsequent run where a previously-flagged entry now matches its
   pin again (maintainer updated the hash, or the vendor reverted): comment
   on and close the open `firmware-drift` issue for that radio ID rather
   than leaving it open forever. Only entries that currently have an open
   drift issue are checked this way — no separate bookkeeping file is
   needed; "is there an open issue for this radio id" is itself the state.
5. Entries are checked sequentially, not in parallel, and only once a week —
   deliberately low request volume per vendor, addressing "don't hammer
   them."

Permissions: this workflow needs `permissions: issues: write` (and
`contents: read`) — narrower than blanket `write-all`, and it is the first
workflow in the repo that writes anything via `GITHUB_TOKEN`, which is worth
calling out explicitly in review.

## Risks / Trade-offs

- **3x test-job runner-minutes** for every push/PR. Accepted: catching a
  Windows/macOS-only regression before a tagged release is worth the cost
  for a project whose users are overwhelmingly on those platforms.
- **wx install flakiness on Windows/macOS** is a pre-existing risk this
  change inherits from `build-release.yml`, not one it introduces; if PyPI
  wheel availability regresses, both workflows are affected and both would
  need the same fix (e.g. pinning a known-good wxPython version).
- **Drift workflow false negatives**: a vendor that repacks *and* happens to
  produce identical bytes some other way, or an entry with a null pin,
  isn't covered — this is why `pin-firmware-hashes` (all entries pinned) is
  a prerequisite for this workflow's coverage to be complete across the
  whole manifest, not just the two `bf-f8hp-pro*` entries that are pinned
  today.
- **GitHub Issues as the notification channel**: no email/Slack alerting is
  in scope; someone has to be watching the repo's issues (or its
  notifications) for the weekly run to be acted on promptly. Acceptable for
  this project's scale; revisit if response time to drift issues becomes a
  problem.
