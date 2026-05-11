"""
Auto-updater for flintwave-kdh-flasher.
Checks GitHub for newer releases and either updates in-place (git)
or directs the user to download the latest release (packaged installs).
"""

import json
import os
import re
import subprocess
import sys
import urllib.request

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_URL = "https://github.com/FlintWave/flintwave-kdh-flasher"
RELEASES_URL = "https://github.com/FlintWave/flintwave-kdh-flasher/releases/latest"

EXPECTED_ORIGINS = {
    "https://github.com/FlintWave/flintwave-kdh-flasher.git",
    "https://github.com/FlintWave/flintwave-kdh-flasher",
    "git@github.com:FlintWave/flintwave-kdh-flasher.git",
}
API_URL = "https://api.github.com/repos/FlintWave/flintwave-kdh-flasher/releases/latest"


def is_git_install():
    """Check if running from a git clone (vs packaged binary)."""
    return os.path.isdir(os.path.join(REPO_DIR, ".git"))


def is_frozen():
    """Check if running as a PyInstaller bundle."""
    return getattr(sys, 'frozen', False)


def get_local_version():
    """Get the VERSION string from the running code."""
    # In frozen (PyInstaller) builds, import the version directly
    # since source files aren't on disk.
    try:
        from gui_main import VERSION
        return VERSION
    except Exception:
        pass
    # Fallback: read from source file (git installs)
    try:
        gui_path = os.path.join(REPO_DIR, "flash_firmware_gui.py")
        if os.path.exists(gui_path):
            with open(gui_path) as f:
                for line in f:
                    m = re.match(r'^VERSION\s*=\s*"([^"]+)"', line)
                    if m:
                        return m.group(1)
    except Exception:
        pass
    return None


def get_latest_release():
    """Query GitHub API for latest release tag and URL.

    Returns (tag_name, html_url) or (None, None) on error.
    """
    try:
        req = urllib.request.Request(API_URL, headers={
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "flintwave-kdh-flasher-updater",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return data.get("tag_name"), data.get("html_url")
    except Exception:
        return None, None


def get_local_commit():
    """Get local git HEAD commit (git installs only)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=5
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except Exception:
        return None


def get_remote_commit():
    """Get remote HEAD commit (git installs only)."""
    try:
        result = subprocess.run(
            ["git", "ls-remote", "origin", "HEAD"],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.split()[0]
    except Exception:
        pass
    return None


def check_for_update():
    """Check if a newer version is available.

    Returns (has_update, local_info, remote_info).
    - For git installs: compares commit SHAs
    - For packaged installs: compares version tag against latest release
    """
    if is_git_install():
        local = get_local_commit()
        remote = get_remote_commit()
        if not local or not remote:
            return False, local, remote
        return local != remote, local[:10], remote[:10]
    else:
        local_ver = get_local_version()
        tag, url = get_latest_release()
        if not tag:
            return False, local_ver, None
        remote_ver = tag.lstrip("v")
        if not local_ver:
            return False, None, remote_ver
        return local_ver != remote_ver, local_ver, remote_ver


def _verify_origin():
    """Verify git remote origin matches expected repositories."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip() in EXPECTED_ORIGINS
    except Exception:
        pass
    return False


def apply_update():
    """Pull latest from origin (git installs only).

    Returns (success, message).
    """
    if not is_git_install():
        return False, "Cannot auto-update packaged installs. Download the latest from the releases page."

    if not _verify_origin():
        return False, "Remote origin does not match expected repository. Update manually."

    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only", "origin", "master"],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, result.stderr.strip()
    except Exception as e:
        return False, str(e)


def get_releases_url():
    """Return the URL to the releases page."""
    return RELEASES_URL
