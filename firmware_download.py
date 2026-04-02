"""
Firmware downloader for KDH bootloader radios.
Downloads firmware bundles from manufacturer websites and extracts .kdhx files.
"""

import io
import json
import os
import re
import sys
import fnmatch
import shutil
import zipfile
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests


def _configure_unrar():
    """Point rarfile at the bundled unrar/unar binary if available."""
    try:
        import rarfile
    except ImportError:
        return

    # In PyInstaller builds, look next to the executable
    if getattr(sys, 'frozen', False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))

    if sys.platform == 'win32':
        bundled = os.path.join(base, 'bundled_unrar.exe')
        if os.path.exists(bundled):
            rarfile.UNRAR_TOOL = bundled
    elif sys.platform == 'darwin':
        # macOS bundles unar (The Unarchiver) — rarfile uses ALT_TOOL for it
        bundled = os.path.join(base, 'bundled_unrar')
        if os.path.exists(bundled):
            rarfile.ALT_TOOL = bundled
    else:
        # Linux — unrar is a package dependency
        pass


_configure_unrar()

# Only allow downloads from known manufacturer domains
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024  # 50 MB

ALLOWED_DOMAINS = {
    "baofengtech.com",
    "www.baofengtech.com",
    "baofengradio.com",
    "www.baofengradio.com",
    "baofeng.s3.amazonaws.com",
    "www.radtels.com",
    "radtels.com",
    "cdn.shopify.com",
    "cdn.shopifycdn.net",
}

RADIOS_FILE = os.path.join(os.path.dirname(__file__), "radios.json")
DOWNLOAD_DIR = os.path.join(os.path.expanduser("~"), ".flintwave-kdh-flasher", "firmware")

USER_AGENT = "flintwave-kdh-flasher/1.0 (https://github.com/FlintWave/flintwave-kdh-flasher)"


def load_radios():
    with open(RADIOS_FILE) as f:
        data = json.load(f)
    return data["radios"]


def get_radio_by_id(radio_id):
    for r in load_radios():
        if r["id"] == radio_id:
            return r
    return None


def validate_url(url):
    """Ensure URL is HTTPS and from an allowed domain."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"Only HTTPS URLs allowed, got: {parsed.scheme}")
    if "@" in (parsed.netloc or ""):
        raise ValueError("URLs with userinfo (@) are not allowed")
    if parsed.hostname not in ALLOWED_DOMAINS:
        raise ValueError(
            f"Domain '{parsed.hostname}' not in allowed list: {sorted(ALLOWED_DOMAINS)}"
        )
    if ".." in parsed.path:
        raise ValueError("Path traversal detected in URL")
    return True


def download_firmware_bundle(url, progress_callback=None):
    """Download a firmware bundle from a manufacturer URL.

    Supports ZIP and RAR archives. Returns the path to the downloaded file.
    """
    validate_url(url)

    os.makedirs(DOWNLOAD_DIR, mode=0o700, exist_ok=True)

    # Derive filename from URL
    parsed = urlparse(url)
    filename = os.path.basename(parsed.path)
    if not filename or not any(filename.endswith(ext) for ext in (".zip", ".rar")):
        filename = "firmware-bundle.zip"
    # Sanitize filename
    filename = re.sub(r'[^\w\-.]', '_', filename)
    dest = os.path.join(DOWNLOAD_DIR, filename)

    resp = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        stream=True,
        timeout=30,
        allow_redirects=True,
    )
    resp.raise_for_status()

    # Validate final URL after redirects — a compromised CDN could
    # redirect to an attacker-controlled server
    if resp.url != url:
        validate_url(resp.url)

    total = int(resp.headers.get("content-length", 0))
    downloaded = 0

    with open(dest, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
            downloaded += len(chunk)
            if downloaded > MAX_DOWNLOAD_BYTES:
                f.close()
                os.unlink(dest)
                raise ValueError(f"Download exceeds size limit ({MAX_DOWNLOAD_BYTES} bytes)")
            if progress_callback and total:
                progress_callback(downloaded / total * 100)

    return dest


def extract_kdhx(archive_path, pattern="*.kdhx"):
    """Extract .kdhx files from a firmware bundle (ZIP or RAR).

    Returns list of extracted file paths.
    """
    if archive_path.lower().endswith(".rar"):
        return _extract_kdhx_from_rar(archive_path, pattern)
    return _extract_kdhx_from_zip(archive_path, pattern)


def _extract_kdhx_from_zip(zip_path, pattern="*.kdhx"):
    """Extract .kdhx files from a ZIP archive."""
    extracted = []
    extract_dir = os.path.dirname(zip_path)

    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            basename = os.path.basename(name)
            # Skip directories, macOS metadata, hidden files
            if not basename or basename.startswith(".") or basename.startswith("__"):
                continue
            if fnmatch.fnmatch(basename, pattern):
                # Extract to flat directory (no nested paths)
                dest = os.path.join(extract_dir, basename)
                with zf.open(name) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst, length=65536)
                extracted.append(dest)

    return extracted


def _extract_kdhx_from_rar(rar_path, pattern="*.kdhx"):
    """Extract .kdhx files from a RAR archive.

    Uses the rarfile library (pip install rarfile / apt install python3-rarfile),
    which requires unrar to be installed on the system.
    """
    try:
        import rarfile
    except ImportError:
        raise RuntimeError(
            "Cannot extract RAR archive. Install the rarfile Python package:\n"
            "  Linux:   sudo apt install python3-rarfile unrar\n"
            "  pip:     pip install rarfile  (also needs unrar)\n"
            "  macOS:   pip install rarfile && brew install unrar\n"
            "  Windows: pip install rarfile  (install UnRAR from https://rarlab.com)"
        )

    extract_dir = os.path.dirname(rar_path)
    extracted = []

    with rarfile.RarFile(rar_path) as rf:
        for name in rf.namelist():
            basename = os.path.basename(name)
            if not basename or basename.startswith(".") or basename.startswith("__"):
                continue
            if fnmatch.fnmatch(basename, pattern):
                dest = os.path.join(extract_dir, basename)
                with rf.open(name) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst, length=65536)
                extracted.append(dest)

    return extracted


def download_and_extract(radio_id, progress_callback=None, url_override=None,
                         expected_sha256=None):
    """Download firmware for a radio and extract the .kdhx file.

    If url_override is provided, it takes precedence over radios.json.
    If expected_sha256 is provided, the downloaded archive is verified.
    Returns (kdhx_path, radio_info) or raises on error.
    """
    radio = get_radio_by_id(radio_id)
    if not radio:
        raise ValueError(f"Unknown radio: {radio_id}")

    url = url_override or radio.get("firmware_url")
    if not url:
        page = radio.get("firmware_page", "the manufacturer's website")
        raise ValueError(
            f"No direct download URL for {radio['name']}. "
            f"Download manually from: {page}"
        )

    zip_path = download_firmware_bundle(url, progress_callback)

    # Verify archive integrity if a hash is provided
    if expected_sha256:
        import hashlib
        sha256 = hashlib.sha256()
        with open(zip_path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                sha256.update(chunk)
        actual = sha256.hexdigest()
        if actual != expected_sha256:
            os.unlink(zip_path)
            raise ValueError(
                f"SHA-256 mismatch!\n"
                f"  Expected: {expected_sha256}\n"
                f"  Got:      {actual}\n"
                f"The downloaded file may be corrupted or tampered with."
            )

    pattern = radio.get("firmware_filename_pattern", "*.kdhx")
    kdhx_files = extract_kdhx(zip_path, pattern)

    if not kdhx_files:
        raise ValueError(f"No .kdhx files found in downloaded bundle")

    # Return the first (usually only) kdhx file
    return kdhx_files[0], radio
