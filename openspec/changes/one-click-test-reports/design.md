# Design: One-Click Test Reports

## When to prompt

Offer after **every** terminal flash outcome — success and failure alike —
same as today (`_offer_test_report` is already called from both the
success path and the `except` block in `_flash_thread` and
`_flash_thread_btf`). Failures on a `tested: false` radio are high-signal:
they either confirm the radio needs real work, or (if the failure was
transient — wrong port, cable) get filtered out by the reporter themselves
before submitting, since they see the full body and can just hit Skip.

The offer is gated by nag suppression (below), not by success/failure —
that keeps the one behavior change (suppression) orthogonal to an existing,
already-shipped behavior (offer on both outcomes) rather than bundling a
second, unrelated decision into this change.

## How not to nag

Key the suppression on **(radio id, firmware version)**, the same grain
`record_flash` already uses for `last_flashed`. Rationale: a version bump is
exactly the case where a fresh report is still valuable (new firmware can
regress a previously-working radio), so suppression must not key on radio
id alone.

State shape, alongside the existing `last_flashed` block in
`~/.flintwave-flash/state.json`:

```json
{
  "last_flashed": { "...": "..." },
  "test_reports": {
    "<radio_id>": {
      "<version>": "submitted" | "skipped"
    }
  }
}
```

- `"submitted"` is written when the user clicks Submit (browser opens).
- `"skipped"` is written only when the user explicitly checks "don't ask
  again for this radio+firmware" and then dismisses — a plain Skip (the
  existing button, unchanged) does not suppress future offers, since a
  reflexive Skip right after a failure is not the same signal as a
  considered "I don't want to be asked about this again."
- Missing version (e.g. `record_flash` didn't detect one from the
  filename) falls back to keying on radio id alone with a sentinel
  version string `"unknown"`, so suppression still degrades gracefully
  instead of crashing or never suppressing.
- Two new pure functions in `firmware_manifest.py`, mirroring
  `record_flash`/`get_last_flashed`:
  - `mark_test_report(radio_id, version, status)` — status is
    `"submitted"` or `"skipped"`.
  - `get_test_report_status(radio_id, version)` — returns the stored
    status or `None`.
- `_offer_test_report` in `gui_main.py` checks
  `get_test_report_status(radio["id"], file_version)` before calling
  `show_test_report_dialog`; if already `"submitted"` or `"skipped"`, it
  skips the dialog entirely (no interruption at all, not even a
  lighter-weight one — that's the point).

## Privacy: what's in the prefilled log

Unchanged from today's behavior, called out explicitly here because the
change makes the report fire more habitually (once per radio+version
instead of being skippable-into-oblivion by fatigue):

- Body includes: radio name, firmware filename (basename only — no local
  path), success/failure, OS name + release, Python version, error message
  string (if failure), and the last 2000 characters of the in-app log
  panel.
- The log panel only ever contains protocol step names, chunk-progress
  lines, and error text produced by this app's own `log_msg` calls — no
  filesystem paths beyond the firmware basename, no serial port contents
  beyond protocol framing bytes rendered as hex/text by existing log
  lines, no credentials (the app has none to leak).
- Nothing is transmitted automatically. `wx.LaunchDefaultBrowser` opens a
  normal `github.com/.../issues/new?...` URL; the user sees the full
  rendered issue body in GitHub's own compose form (and in the in-app
  preview `TextCtrl` before that) and must click GitHub's own "Submit new
  issue" — the same two-step confirmation as today, unchanged by this
  proposal.
- The "don't ask again" checkbox state and the `submitted`/`skipped`
  status are the only new data written to disk, and they never leave the
  machine — `state.json` is local-only, same as `last_flashed`.

## URL length

GitHub's `issues/new` prefill has a practical URL-length ceiling (browsers
commonly cap around 8k characters; GitHub itself has been observed
truncating well past that). Current budget with the existing 2000-char log
truncation:

- Fixed prose (title, labels, radio/firmware/result/OS/python lines,
  headers): well under 500 chars even in the most verbose translated
  locale.
- Log payload: capped at 2000 raw characters, but `urllib.parse.urlencode`
  percent-encodes newlines and other bytes, which can expand the encoded
  length up to ~3x in the worst case (e.g. a log full of control
  characters) — so 2000 raw chars can become ~6000 encoded chars.
- Total worst case (~6500 encoded chars) stays under the ~8k practical
  ceiling but not with much headroom once title/labels are added.

Decision: keep the 2000-char raw cap (no change), but add a test that
encodes a worst-case-density log (e.g. all newlines, which encode 3 bytes
each as `%0A`) and asserts the final encoded URL stays under a documented
budget (8000 chars), so a future change to the log format or truncation
length can't silently blow past what browsers/GitHub will accept.

## Maintainer side: mapping reports to `tested` flips

No new tooling in this change (kept out of scope — this proposal is
app-side only). The existing shape already carries what's needed:

- Every report is labeled `test-report` (unchanged) — a maintainer can
  filter the issue tracker on that label.
- The body's first line is always `Radio: {radio_name}` and, when present,
  an error line — both already exact-matchable against `radios.json`
  `name` fields.
- Convention documented here (not enforced in code): when a maintainer
  reads a `test-report` issue reporting **success** on a radio whose
  `radios.json` entry is `tested: false`, flipping that entry to
  `tested: true` and closing the issue with a reference to it (as already
  done for issue #20, see `CHANGELOG.md` v26.07.0) is the expected
  workflow. A **failure** report on an already-`tested: true` entry is a
  regression bug report, not a flag flip.
- Future, out-of-scope idea (noted so it isn't lost, not built here): an
  issue template or a small script that greps open `test-report` issues
  against `radios.json` ids to flag stale `tested: false` entries with an
  open confirming report.
