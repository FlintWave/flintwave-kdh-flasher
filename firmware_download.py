"""
Firmware downloader for KDH bootloader radios.
Downloads firmware bundles from manufacturer websites and extracts .kdhx files.
"""

import io
import json
import os
import re
import fnmatch
import zipfile
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import requests

# Only allow downloads from known manufacturer domains
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024  # 50 MB

ALLOWED_DOMAINS = {
    "baofengtech.com",
    "www.baofengtech.com",
    "baofengradio.com",
    "www.baofengradio.com",
    "www.radtels.com",
    "radtels.com",
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
    if parsed.hostname not in ALLOWED_DOMAINS:
        raise ValueError(
            f"Domain '{parsed.hostname}' not in allowed list: {sorted(ALLOWED_DOMAINS)}"
        )
    if ".." in parsed.path:
        raise ValueError("Path traversal detected in URL")
    return True


def download_firmware_bundle(url, progress_callback=None):
    """Download a firmware bundle ZIP from a manufacturer URL.

    Returns the path to the downloaded file.
    """
    validate_url(url)

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)

    # Derive filename from URL
    parsed = urlparse(url)
    filename = os.path.basename(parsed.path)
    if not filename or not filename.endswith(".zip"):
        filename = "firmware-bundle.zip"
    # Sanitize filename
    filename = re.sub(r'[^\w\-.]', '_', filename)
    dest = os.path.join(DOWNLOAD_DIR, filename)

    resp = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        stream=True,
        timeout=30,
    )
    resp.raise_for_status()

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


def extract_kdhx(zip_path, pattern="*.kdhx"):
    """Extract .kdhx files from a firmware bundle ZIP.

    Returns list of extracted file paths.
    """
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
                import shutil
                with zf.open(name) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst, length=65536)
                extracted.append(dest)

    return extracted


def download_and_extract(radio_id, progress_callback=None):
    """Download firmware for a radio and extract the .kdhx file.

    Returns (kdhx_path, radio_info) or raises on error.
    """
    radio = get_radio_by_id(radio_id)
    if not radio:
        raise ValueError(f"Unknown radio: {radio_id}")

    url = radio.get("firmware_url")
    if not url:
        page = radio.get("firmware_page", "the manufacturer's website")
        raise ValueError(
            f"No direct download URL for {radio['name']}. "
            f"Download manually from: {page}"
        )

    zip_path = download_firmware_bundle(url, progress_callback)

    pattern = radio.get("firmware_filename_pattern", "*.kdhx")
    kdhx_files = extract_kdhx(zip_path, pattern)

    if not kdhx_files:
        raise ValueError(f"No .kdhx files found in downloaded bundle")

    # Return the first (usually only) kdhx file
    return kdhx_files[0], radio
