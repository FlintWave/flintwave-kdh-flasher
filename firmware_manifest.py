"""
Remote firmware manifest fetcher and local flash state tracker.

Fetches firmware_manifest.json from the GitHub repo to discover new
firmware versions without requiring an app update. Caches the manifest
locally and tracks which firmware versions have been flashed to each radio.
"""

import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone

import requests

MANIFEST_URL = (
    "https://raw.githubusercontent.com/FlintWave/flintwave-kdh-flasher"
    "/master/firmware_manifest.json"
)
USER_AGENT = "flintwave-flash/1.0 (https://github.com/FlintWave/flintwave-kdh-flasher)"

STATE_DIR = os.path.join(os.path.expanduser("~"), ".flintwave-flash")
STATE_FILE = os.path.join(STATE_DIR, "state.json")
_LEGACY_STATE_DIR = os.path.join(os.path.expanduser("~"), ".flintwave-kdh-flasher")

MANIFEST_CACHE_TTL = 300  # 5 minutes


def _migrate_state_dir():
    """One-shot rename of the pre-rebrand user-data dir.

    If the legacy ~/.flintwave-kdh-flasher exists and the new
    ~/.flintwave-flash does not, rename it so cached manifest data and
    last-flashed records survive the rebrand. Silent on failure — the rest of
    the module recreates the dir as needed via _save_state.
    """
    try:
        if os.path.isdir(_LEGACY_STATE_DIR) and not os.path.exists(STATE_DIR):
            os.rename(_LEGACY_STATE_DIR, STATE_DIR)
    except OSError:
        pass


_migrate_state_dir()


def _load_state():
    """Load state from disk. Returns empty dict on missing/corrupt file."""
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def _save_state(state):
    """Write state atomically (temp file + rename)."""
    os.makedirs(STATE_DIR, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=STATE_DIR, suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp, STATE_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def fetch_manifest(force=False):
    """Fetch the remote firmware manifest, with caching.

    Returns the manifest dict (the "radios" key mapping radio IDs to info),
    or None if fetch fails and no cache exists.
    """
    state = _load_state()
    cache = state.get("manifest_cache", {})

    # Use cache if fresh enough
    if not force and cache.get("data") and cache.get("last_fetched"):
        try:
            age = time.time() - cache["last_fetched"]
            if age < MANIFEST_CACHE_TTL:
                return cache["data"]
        except (TypeError, ValueError):
            pass

    # Fetch from remote
    try:
        resp = requests.get(
            MANIFEST_URL,
            headers={"User-Agent": USER_AGENT},
            timeout=10,
        )
        resp.raise_for_status()
        manifest = resp.json()
        radios = manifest.get("radios", {})

        # Cache it
        state["manifest_cache"] = {
            "last_fetched": time.time(),
            "data": radios,
        }
        _save_state(state)
        return radios

    except Exception:
        # Return stale cache if available
        if cache.get("data"):
            return cache["data"]
        return None


def get_radio_firmware_info(radio_id, manifest=None):
    """Look up firmware info for a radio from the manifest.

    For Radtel radios, also checks the manufacturer's page for newer
    firmware (the page is scrapable, unlike Baofeng/BTECH).

    Returns dict with keys: firmware_version, firmware_url, firmware_sha256,
    release_notes. Returns None if radio not in manifest.
    """
    if manifest is None:
        manifest = fetch_manifest()

    manifest_info = manifest.get(radio_id) if manifest else None

    # For Radtel radios, try live scraping for a newer version
    if radio_id in _RADTEL_SCRAPE_PATTERNS:
        scraped = _scrape_radtel_firmware(radio_id)
        if scraped:
            # Use scraped if manifest has no URL, or scraped version is newer
            if not manifest_info or not manifest_info.get("firmware_url"):
                return scraped
            from firmware_version import compare_versions
            scraped_ver = scraped.get("firmware_version", "")
            manifest_ver = manifest_info.get("firmware_version", "")
            if scraped_ver and manifest_ver and compare_versions(scraped_ver, manifest_ver) > 0:
                return scraped

    return manifest_info


RADTEL_DOWNLOAD_URL = "https://www.radtels.com/pages/software-download"

# Radtel scraper: radio_id -> regex to find firmware archive URLs on the page
_RADTEL_SCRAPE_PATTERNS = {
    "rt-470": re.compile(
        r'(https://cdn\.shopify(?:cdn)?\.(?:com|net)/s/files/[^\s"?]+RT-?470[^\s"?]*\.rar)',
        re.IGNORECASE,
    ),
    "rt-490": re.compile(
        r'(https://cdn\.shopify(?:cdn)?\.(?:com|net)/s/files/[^\s"?]+(?:rt.?490|Firmware_Version)[^\s"?]*\.(?:zip|rar))',
        re.IGNORECASE,
    ),
}

# Exclude CPS/programming software and beta builds
_RADTEL_EXCLUDE = re.compile(
    r'CPS|Programming|Software|CHIRP|Driver|Setup|Installer|[Bb]eta', re.IGNORECASE
)

_radtel_page_cache = None
_radtel_page_cache_time = 0


def _fetch_radtel_page():
    """Fetch and cache the Radtel download page HTML."""
    global _radtel_page_cache, _radtel_page_cache_time

    if _radtel_page_cache and (time.time() - _radtel_page_cache_time) < MANIFEST_CACHE_TTL:
        return _radtel_page_cache

    resp = requests.get(
        RADTEL_DOWNLOAD_URL,
        headers={"User-Agent": USER_AGENT},
        timeout=10,
    )
    resp.raise_for_status()
    _radtel_page_cache = resp.text
    _radtel_page_cache_time = time.time()
    return _radtel_page_cache


def _scrape_radtel_firmware(radio_id):
    """Scrape the Radtel download page for the latest firmware.

    Returns a firmware info dict or None.
    """
    pattern = _RADTEL_SCRAPE_PATTERNS.get(radio_id)
    if not pattern:
        return None

    try:
        html = _fetch_radtel_page()
        urls = pattern.findall(html)
        if not urls:
            return None

        # Filter out CPS/software links, keep only firmware
        urls = [u for u in urls if not _RADTEL_EXCLUDE.search(u)]
        if not urls:
            return None

        # Find the highest version among the URLs
        from firmware_version import parse_version, extract_version_from_filename
        best_url = None
        best_ver = None
        best_parsed = (0, 0, 0)
        for url in set(urls):
            filename = url.rsplit("/", 1)[-1]
            ver = extract_version_from_filename(filename)
            if ver:
                parsed = parse_version(ver)
                if parsed > best_parsed:
                    best_parsed = parsed
                    best_ver = ver
                    best_url = url

        if not best_url or best_parsed == (0, 0, 0):
            return None

        return {
            "firmware_version": best_ver,
            "firmware_url": best_url,
            "firmware_sha256": None,
            "release_notes": f"v{best_ver} (from radtels.com)",
        }

    except Exception:
        return None


def record_flash(radio_id, version, sha256):
    """Record a successful flash to local state."""
    state = _load_state()
    if "last_flashed" not in state:
        state["last_flashed"] = {}
    state["last_flashed"][radio_id] = {
        "version": version,
        "firmware_sha256": sha256,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _save_state(state)


def get_last_flashed(radio_id):
    """Get the last-flashed firmware info for a radio.

    Returns dict with version, firmware_sha256, timestamp, or None.
    """
    state = _load_state()
    return state.get("last_flashed", {}).get(radio_id)


def get_language(default="en"):
    """Return the persisted UI language code, or the default if none stored."""
    state = _load_state()
    code = state.get("language")
    return code if isinstance(code, str) and code else default


def set_language(code):
    """Persist the user's UI language choice."""
    state = _load_state()
    state["language"] = code
    _save_state(state)
