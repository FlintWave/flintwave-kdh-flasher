"""
Runtime i18n for FlintWave Flash.

English ships bundled in the binary (translations/en.json); other languages
are fetched on demand from the GitHub repo and cached under
~/.flintwave-flash/translations/<code>.json. Translation keys are namespaced
dotted strings (e.g. "button.flash_firmware") so the English wording can be
edited without invalidating every locale.

Public API:
    t(key)                            — look up a string, with EN fallback
    LANGUAGES                         — ordered (code, native_label) list
    load_bundled_en()                 — load translations/en.json at startup
    set_language_sync_if_cached(code) — load a cached locale synchronously
    set_language(code, on_done)       — switch language; fetches if needed
    is_rtl(code)                      — True for right-to-left locales
    current_code()                    — currently active language code
"""

import json
import os
import sys
import tempfile
import threading
import time

import requests


# Ordered for display in the dropdown. Native-script labels.
LANGUAGES = [
    ("en", "English"),
    ("zh-CN", "中文"),
    ("fr", "Français"),
    ("de", "Deutsch"),
    ("it", "Italiano"),
    ("es", "Español"),
    ("ar", "العربية"),
    ("ru", "Русский"),
]

LANGUAGE_CODES = [code for code, _ in LANGUAGES]

# Extensible: add "he", "fa", "ur" when those locales ship.
RTL_LANGUAGES = {"ar"}

TRANSLATIONS_URL_TEMPLATE = (
    "https://raw.githubusercontent.com/FlintWave/flintwave-kdh-flasher"
    "/master/translations/{code}.json"
)
USER_AGENT = (
    "flintwave-flash/1.0 (https://github.com/FlintWave/flintwave-kdh-flasher)"
)

# Module state. _en_catalog is the always-loaded fallback; _catalog holds the
# currently active language (may be identical to _en_catalog when current is "en").
_en_catalog: dict = {}
_catalog: dict = {}
_current_code: str = "en"


def _bundled_translations_dir() -> str:
    """Return the directory holding bundled translation JSON files.

    Works both in the source tree and inside a PyInstaller bundle
    (sys._MEIPASS points at the extracted bundle root).
    """
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, "translations")


def _cache_translations_dir() -> str:
    """Return the user-data dir for downloaded translation caches."""
    return os.path.join(os.path.expanduser("~"), ".flintwave-flash", "translations")


def _read_json_file(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return None


def _strip_meta(catalog: dict) -> dict:
    """Filter out the optional `_meta` housekeeping key from a catalog dict."""
    return {k: v for k, v in catalog.items() if k != "_meta" and isinstance(v, str)}


def load_bundled_en() -> None:
    """Load translations/en.json into the module's English fallback catalog.

    Called once at startup, before any t() lookups happen. If the file is
    missing (shouldn't ever be), the fallback stays empty and t() returns the
    raw key — visible bug, no crash.
    """
    global _en_catalog, _catalog, _current_code
    path = os.path.join(_bundled_translations_dir(), "en.json")
    data = _read_json_file(path) or {}
    _en_catalog = _strip_meta(data)
    _catalog = dict(_en_catalog)
    _current_code = "en"


def _load_cached(code: str) -> dict | None:
    """Read a previously downloaded catalog from the cache dir, if present."""
    if code == "en":
        return dict(_en_catalog)
    path = os.path.join(_cache_translations_dir(), f"{code}.json")
    data = _read_json_file(path)
    return _strip_meta(data) if data else None


def _write_cached(code: str, data: dict) -> None:
    """Atomically write a downloaded catalog to the cache dir."""
    cache_dir = _cache_translations_dir()
    os.makedirs(cache_dir, mode=0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=cache_dir, suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, os.path.join(cache_dir, f"{code}.json"))
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def fetch_translation(code: str) -> dict | None:
    """Fetch a translation catalog from GitHub, caching to disk.

    Mirrors the structure of firmware_manifest.fetch_manifest. Returns the
    parsed dict on success, the cached copy if the network call fails and a
    cache exists, or None if there's no cache and the network failed.
    """
    if code == "en":
        return dict(_en_catalog)

    cached = _load_cached(code)
    url = TRANSLATIONS_URL_TEMPLATE.format(code=code)
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            return cached
        stripped = _strip_meta(data)
        try:
            _write_cached(code, data)
        except OSError:
            pass
        return stripped
    except Exception:
        return cached


def _apply_catalog(code: str, data: dict) -> None:
    """Install a loaded catalog as the active one."""
    global _catalog, _current_code
    _catalog = data or {}
    _current_code = code


def set_language_sync_if_cached(code: str) -> bool:
    """Load a language from cache (or the bundled English) without network.

    Used at startup so a user whose chosen locale is already cached doesn't see
    an English flash-of-unstyled-content before their language kicks in.
    Returns True if the catalog was loaded.
    """
    if code == "en":
        _apply_catalog("en", dict(_en_catalog))
        return True
    cached = _load_cached(code)
    if cached is not None:
        _apply_catalog(code, cached)
        return True
    return False


def set_language(code: str, on_done) -> None:
    """Switch to `code`, downloading the catalog in a thread if needed.

    `on_done(success: bool)` is invoked when the language is ready. For the
    English / cached path it is called synchronously on the calling thread;
    for the network path it is invoked from a worker thread — the GUI handler
    is responsible for marshaling back to the UI thread (e.g. via wx.CallAfter).
    """
    if code == "en":
        _apply_catalog("en", dict(_en_catalog))
        on_done(True)
        return

    cached = _load_cached(code)
    if cached is not None:
        _apply_catalog(code, cached)
        on_done(True)
        # Best-effort refresh in the background — silently update the cache
        # so the next launch has fresher strings.
        threading.Thread(
            target=_background_refresh, args=(code,), daemon=True
        ).start()
        return

    def worker():
        data = fetch_translation(code)
        if data is None:
            on_done(False)
            return
        _apply_catalog(code, data)
        on_done(True)

    threading.Thread(target=worker, daemon=True).start()


def _background_refresh(code: str) -> None:
    """Re-fetch a cached catalog in the background to keep the cache fresh."""
    try:
        fetch_translation(code)
    except Exception:
        pass


def t(key: str) -> str:
    """Return the translated string for `key`, falling back to English then
    to the raw key.
    """
    val = _catalog.get(key)
    if val is None:
        val = _en_catalog.get(key)
    if val is None:
        return key
    return val


def is_rtl(code: str | None = None) -> bool:
    """True if the given language code uses right-to-left layout."""
    if code is None:
        code = _current_code
    return code in RTL_LANGUAGES


def current_code() -> str:
    """Return the currently active language code."""
    return _current_code


def index_of(code: str) -> int:
    """Return the dropdown index of a language code, or 0 (English) if unknown."""
    for i, (c, _) in enumerate(LANGUAGES):
        if c == code:
            return i
    return 0
