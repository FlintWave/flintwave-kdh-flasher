"""
Auto-updater for flintwave-kdh-flasher.
Checks GitHub for newer releases; downloads and installs updates where the
OS allows, or shows a "Restart to Update" button in the status bar.
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
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

# Asset patterns per platform. The key is used by get_platform_asset_url().
ASSET_PATTERNS = {
    "linux_appimage": "FlintWave-Flash-x86_64.AppImage",
    "windows_exe":    "FlintWave-Flash.exe",
    "windows_setup":  "FlintWave-Flash-Setup.exe",
    "macos_dmg":      "FlintWave-Flash.dmg",
}


def is_git_install():
    """Check if running from a git clone (vs packaged binary)."""
    return os.path.isdir(os.path.join(REPO_DIR, ".git"))


def is_frozen():
    """Check if running as a PyInstaller bundle."""
    return getattr(sys, 'frozen', False)


def get_local_version():
    """Get the VERSION string from the running code."""
    try:
        from gui_main import VERSION
        return VERSION
    except Exception:
        pass
    try:
        for gui_file in ("gui_main.py", "flash_firmware_gui.py"):
            gui_path = os.path.join(REPO_DIR, gui_file)
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


def get_latest_release_full():
    """Query GitHub API for the full latest release object.

    Returns the parsed JSON dict, or None on error. The dict includes
    'tag_name', 'html_url', and 'assets' (list of asset dicts with
    'name', 'browser_download_url', 'size').
    """
    try:
        req = urllib.request.Request(API_URL, headers={
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "flintwave-kdh-flasher-updater",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


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


def get_platform_asset_url(release_data):
    """Pick the right download asset for this platform from a release dict.

    Returns (asset_name, download_url, size_bytes) or (None, None, None).
    """
    if not release_data or "assets" not in release_data:
        return None, None, None

    assets = {a["name"]: a for a in release_data["assets"]}

    if sys.platform.startswith("linux"):
        target = ASSET_PATTERNS["linux_appimage"]
    elif sys.platform == "win32":
        target = ASSET_PATTERNS["windows_exe"]
    elif sys.platform == "darwin":
        target = ASSET_PATTERNS["macos_dmg"]
    else:
        return None, None, None

    asset = assets.get(target)
    if not asset:
        return None, None, None
    return asset["name"], asset["browser_download_url"], asset.get("size", 0)


def download_update(url, dest_path, progress_callback=None):
    """Download an update asset to dest_path.

    progress_callback(bytes_downloaded, total_bytes) is called periodically.
    Returns True on success; raises on error.
    """
    req = urllib.request.Request(url, headers={
        "User-Agent": "flintwave-kdh-flasher-updater",
        "Accept": "application/octet-stream",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        downloaded = 0
        with open(dest_path, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if progress_callback:
                    progress_callback(downloaded, total)
    return True


def can_auto_install():
    """Whether this platform supports in-place install + restart.

    Linux AppImage and Windows portable .exe can be replaced in-place.
    macOS .dmg requires the user to drag-install manually.
    Git installs use git pull instead.
    """
    if is_git_install():
        return True
    if not is_frozen():
        return False
    if sys.platform.startswith("linux"):
        return True
    if sys.platform == "win32":
        return True
    return False


def get_current_executable():
    """Path to the running executable (frozen builds only)."""
    if is_frozen():
        return sys.executable
    return None


def install_update(downloaded_path):
    """Replace the running binary with the downloaded update.

    Returns (success, message). On Linux/Windows frozen builds, stages the
    new binary next to the old one and swaps on restart. On git installs,
    does git pull. macOS .dmg is opened for the user to drag-install.
    """
    if is_git_install():
        return apply_update()

    exe = get_current_executable()
    if not exe:
        return False, "Cannot determine current executable path."

    if sys.platform == "darwin":
        try:
            subprocess.Popen(["open", downloaded_path])
            return True, "DMG opened. Drag the new version to Applications."
        except Exception as e:
            return False, str(e)

    # Linux AppImage / Windows portable: stage the new binary
    staged = exe + ".update"
    try:
        shutil.copy2(downloaded_path, staged)
        if sys.platform.startswith("linux"):
            os.chmod(staged, 0o755)
        return True, staged
    except Exception as e:
        return False, str(e)


def apply_staged_update():
    """Swap the staged binary into place and restart.

    Called just before exit. On Windows the running .exe can't be replaced
    directly, so we rename the current one aside, move the new one in, and
    spawn the replacement.
    """
    exe = get_current_executable()
    if not exe:
        return
    staged = exe + ".update"
    if not os.path.exists(staged):
        return

    if sys.platform == "win32":
        old = exe + ".old"
        try:
            if os.path.exists(old):
                os.remove(old)
            os.rename(exe, old)
            os.rename(staged, exe)
        except Exception:
            # If rename failed, try to restore
            try:
                if not os.path.exists(exe) and os.path.exists(old):
                    os.rename(old, exe)
            except Exception:
                pass
            return
    else:
        # Linux: direct replace
        try:
            os.replace(staged, exe)
        except Exception:
            return

    restart_app()


def restart_app():
    """Re-launch the application and exit the current process."""
    if is_git_install():
        python = sys.executable
        os.execv(python, [python] + sys.argv)
    elif is_frozen():
        exe = get_current_executable()
        if exe:
            if sys.platform == "win32":
                subprocess.Popen([exe] + sys.argv[1:])
                sys.exit(0)
            else:
                os.execv(exe, [exe] + sys.argv[1:])


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


def _get_update_branch():
    """Determine which branch to pull for updates."""
    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            ref = result.stdout.strip()
            if ref.startswith("refs/remotes/origin/"):
                return ref.rsplit("/", 1)[-1]
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
            if branch and branch != "HEAD":
                return branch
    except Exception:
        pass

    return None


def apply_update():
    """Pull latest from origin (git installs only).

    Returns (success, message).
    """
    if not is_git_install():
        return False, "Cannot auto-update packaged installs. Download the latest from the releases page."

    if not _verify_origin():
        return False, "Remote origin does not match expected repository. Update manually."

    branch = _get_update_branch()
    if not branch:
        return False, "Could not determine update branch for origin. Update manually."

    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only", "origin", branch],
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
