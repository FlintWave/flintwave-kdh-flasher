#!/usr/bin/env python3
"""Weekly manifest-drift check (.github/workflows/manifest-drift.yml).

Downloads every firmware bundle that firmware_manifest.json pins
(non-null firmware_url AND non-null firmware_sha256) and compares the
actual SHA-256 against the pinned value. Vendors are known to silently
repack bundles at unchanged URLs (2026-07-10 BaofengTech incident);
this catches that within a week instead of when a user hits it.

Distinguishes three outcomes and never conflates them:
- drifted:    downloaded fine, hash differs  -> the workflow files an issue
- unverified: download failed after retries  -> job-summary note only,
              NEVER reported as drift (vendor CDNs are flaky)
- ok:         hash matches                   -> used to auto-close a
              previously filed drift issue

Entries are checked sequentially with a pause between them so vendor
servers are not hammered. Writes drift_result.json for the issue-lifecycle
step and prints a Markdown summary to stdout (piped to $GITHUB_STEP_SUMMARY).
Exit code is always 0 unless the manifest itself is unreadable — drift is
reported through issues, not a red scheduled run nobody looks at.
"""

import hashlib
import json
import sys
import time
import urllib.request

USER_AGENT = (
    "flintwave-flash-drift-check/1.0 "
    "(+https://github.com/FlintWave/flintwave-kdh-flasher)"
)
TIMEOUT = 120       # per-download seconds
RETRIES = 3
PAUSE_BETWEEN = 2   # seconds between entries — be gentle to vendor servers


def fetch_sha256(url):
    """Stream-download url and return (hexdigest, None), or (None, error)."""
    last_err = "unknown error"
    for attempt in range(RETRIES):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            digest = hashlib.sha256()
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                for chunk in iter(lambda: resp.read(65536), b""):
                    digest.update(chunk)
            return digest.hexdigest(), None
        except Exception as exc:  # noqa: BLE001 - any failure is "unverified"
            last_err = f"{type(exc).__name__}: {exc}"
            time.sleep(5 * (attempt + 1))
    return None, last_err


def main():
    with open("firmware_manifest.json", encoding="utf-8") as f:
        manifest = json.load(f)

    drifted, unverified, ok, skipped = [], [], [], []
    for radio_id, info in sorted(manifest["radios"].items()):
        url = info.get("firmware_url")
        pinned = info.get("firmware_sha256")
        if not url or not pinned:
            skipped.append(radio_id)
            continue
        actual, err = fetch_sha256(url)
        if actual is None:
            unverified.append({"id": radio_id, "url": url, "error": err})
        elif actual != pinned:
            drifted.append({
                "id": radio_id, "url": url,
                "expected": pinned, "actual": actual,
            })
        else:
            ok.append(radio_id)
        time.sleep(PAUSE_BETWEEN)

    with open("drift_result.json", "w", encoding="utf-8") as f:
        json.dump({"drifted": drifted, "unverified": unverified,
                   "ok": ok, "skipped": skipped}, f, indent=2)

    print("## Manifest drift check")
    print()
    if drifted:
        print(f"### DRIFT DETECTED ({len(drifted)})")
        for d in drifted:
            print(f"- **{d['id']}** — expected `{d['expected']}`, "
                  f"got `{d['actual']}` ({d['url']})")
        print()
    if unverified:
        print(f"### Could not verify ({len(unverified)}) — "
              "download failed, NOT reported as drift")
        for u in unverified:
            print(f"- **{u['id']}** — {u['error']} ({u['url']})")
        print()
    print(f"OK: {len(ok)} ({', '.join(ok) or '—'}); "
          f"skipped (no url or no pin): {len(skipped)} "
          f"({', '.join(skipped) or '—'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
