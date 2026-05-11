# Usage Guide

## Installation

### Installers (recommended)

Download the latest release from the [Releases page](https://github.com/FlintWave/flintwave-kdh-flasher/releases/latest):

| OS | File | Install |
|----|------|---------|
| Debian/Ubuntu/Pop!_OS/Mint | `.deb` | `sudo dpkg -i flintwave-flash_*.deb` |
| Fedora/RHEL/openSUSE | `.rpm` | `sudo rpm -i flintwave-flash-*.rpm` |
| Windows | `FlintWave-Flash-Setup.exe` | Run the installer |
| macOS | `FlintWave-Flash.dmg` | Open and drag to Applications |

After installing, search for "FlintWave" in your app launcher, Start Menu, or Applications folder.

### Portable (no install needed)

| OS | File | How to run |
|----|------|-----------|
| Linux | `.AppImage` | `chmod +x *.AppImage` and double-click |
| Windows | `FlintWave-Flash.exe` | Double-click to run |

### Install from source

#### Linux

```bash
sudo apt install python3-wxgtk4.0 python3-serial python3-requests python3-rarfile unrar git
git clone https://github.com/FlintWave/flintwave-kdh-flasher.git
cd flintwave-kdh-flasher
python3 flash_firmware_gui.py
```

Add yourself to the `dialout` group for serial port access:
```
sudo usermod -aG dialout $USER
```
Log out and back in for the group change to take effect.

#### Linux (one-liner)

```
curl -sL https://raw.githubusercontent.com/FlintWave/flintwave-kdh-flasher/master/install.sh | bash
```

#### macOS

```bash
brew install python wxpython unrar
pip3 install pyserial requests rarfile
git clone https://github.com/FlintWave/flintwave-kdh-flasher.git
cd flintwave-kdh-flasher
python3 flash_firmware_gui.py
```

#### Windows

1. Install [Python 3.10+](https://python.org) — **check "Add Python to PATH" during install**
2. Open Command Prompt:
```
py -m pip install pyserial wxPython requests rarfile
```
3. Download and extract the [source ZIP](https://github.com/FlintWave/flintwave-kdh-flasher/archive/refs/heads/master.zip)
4. Run: `py flash_firmware_gui.py`

## Flashing Firmware

The window is laid out as three columns — **Firmware → Handset → Flash** — separated by `›` arrows. Each column unlocks once the previous step is complete; a soft green pulse on the arrow signals the next step is available. Below the columns: an **Instructions** panel (left) that updates with per-radio info, and a scrolling **Log** (right).

### Step 1: Select your radio

Click the **Firmware** column dropdown (default: *— Select your radio —*) and pick your model. The Instructions panel updates with that radio's bootloader key combination, connector type, tested status, and latest firmware version. Picking a radio enables the **Download** button.

If your radio isn't listed, select **"Other KDH Radio"** — it works with any radio that uses the KDH bootloader. You'll need to browse for a `.kdhx` firmware file manually.

### Step 2: Get the firmware file

**Option A — Automatic download:**
Click **"Download v…"**. The tool downloads the firmware bundle from the manufacturer's website, extracts the `.kdhx` file, and fills in the path automatically. The app checks a remote manifest for the latest known firmware URLs, so this may work even if you haven't updated the app recently.

**Option B — Manual download:**
Visit your radio manufacturer's website, download the firmware bundle (usually a `.zip` or `.rar`), extract it, and click **Browse…** to pick the `.kdhx` file. For `.rar` archives, the app extracts them automatically if `unrar` or `7z` is installed.

Once a firmware file is loaded, the **Handset** column unlocks (the first arrow pulses green).

### Step 3: Pick your handset(s)

Plug in your programming cable. The **Handset** column lists every USB serial port detected. The first time the column unlocks (after Steps 1 + 2), the app probes each port with a bootloader handshake — ports that answer are marked **Ready** and known cables (PC03 / FTDI / CH340 / Prolific / CP2102) are auto-checked. Before the column unlocks the list stays passive (no serial I/O), and hot-plugging a cable while still gated only updates the list view — no automatic probing.

- **One handset checked** — the app does a single flash to that port.
- **Multiple handsets checked** — the app flashes them sequentially in batch mode (great for OEM-style multi-radio runs). Each row's `Status` and `%` columns track per-port progress.
- **Refresh / Probe** — re-scan ports and re-probe on demand. Plug/unplug events refresh the list automatically but don't re-probe; click this when you want fresh handshake results.
- **All / None** — select all detected handsets at once.

**Cable tips:**
- Turn the radio's volume to **maximum** before connecting.
- Push the cable connector **firmly** into the radio — it needs more force than you'd expect.
- You may need to **hold pressure on the connector during the entire flash** — the K1 2.5mm ring contact that carries return data is sensitive to movement.
- If you get "no response" errors, try pressing harder or at a slight angle.

Once at least one handset is checked, the **Flash** column unlocks (the second arrow pulses green).

### Step 4: Verify with a dry run (optional)

Click **Dry Run** to validate the firmware file without touching the radio. This checks:
- File size is within protocol limits
- ARM vector table has valid stack pointer and reset handler
- All data packets build correctly with valid CRCs
- SHA-256 hash is displayed for verification against published hashes

### Step 5: Test serial communication (optional)

With the radio in bootloader mode (see below), click **Diagnostics** to test whether the tool can communicate with the (first checked) handset. If you see a response in the log, you're good to flash.

### Step 6: Flash

1. Put each radio in **bootloader mode**:
   - Power off the radio completely
   - Hold the bootloader keys (shown in the Instructions panel — e.g., SK1 + SK2 for BF-F8HP Pro)
   - While holding both keys, turn the power/volume knob to power on
   - The screen stays blank and the green Rx LED lights up
   - Do NOT release the keys until the LED is on

2. Click **Flash Firmware**. Read and confirm the warning dialog — it shows your specific radio's instructions, and notes whether you're flashing a single handset or running a batch.

3. Watch the progress bar complete and the per-handset rows update. **Do not disconnect any cable or power off any radio during flashing.** If a port fails mid-batch you'll be prompted to skip it or stop the run.

4. When complete, power cycle each radio and verify the firmware version via **Menu > Radio Info**.

5. After flashing, you'll be offered the option to submit a test report. This helps us track which radios have been verified.

## Firmware Version Checking

The app tracks firmware versions to help you avoid accidental same-version or downgrade flashes:

- **Latest version display:** When you select a radio, the info line shows the latest known firmware version (fetched from the remote manifest on GitHub).
- **Same-version warning:** If you try to flash the same version you last flashed, you'll get a confirmation prompt.
- **Downgrade warning:** If you try to flash an older version than what was last flashed, you'll see a warning.
- **Post-flash log:** After a successful flash, the log shows whether you're on the latest version.

Version information is parsed from firmware filenames (e.g., `BTECH_V0.53_260116.kdhx` is version 0.53). The KDH bootloader protocol does not support reading the current firmware version from the radio, so version tracking relies on what this tool has flashed.

## Bootloader Mode Quick Reference

| Radio | Keys to Hold |
|-------|-------------|
| BTECH BF-F8HP Pro | SK1 (top) + SK2 (bottom) — not PTT. Volume to max. Hold cable firmly. |
| Baofeng UV-25 Plus/Pro | SK2 + SK3 (two buttons below PTT) |
| Radtel RT-470 / RT-490 | Check your radio's manual |
| Others | Check your radio's manual or `radios.json` |

**Important:** The side keys are the small buttons above and below the large PTT button. Do not hold PTT itself.

## Language

A language dropdown in the title bar lets you switch the UI between English, Simplified Chinese (中文), French (Français), German (Deutsch), Italian (Italiano), Spanish (Español), Arabic (العربية), and Russian (Русский). Arabic switches the entire layout to right-to-left automatically.

English ships bundled in the binary. The first time you pick another language the app downloads the catalog from this repo and caches it under `~/.flintwave-flash/translations/<code>.json`; subsequent launches load from the cache without a network call. Your last-used language is remembered between sessions. If the download fails (offline, etc.) the dropdown reverts and a brief notice is logged.

The non-English catalogs are machine-translated starting points (`_meta.reviewed: false` in each file) and welcome community review PRs.

## Themes and Accessibility

The app ships with two Catppuccin palettes:

- **Mocha** — dark
- **Latte** — light

On launch the app reads `wx.SystemSettings.GetAppearance()` and starts in the theme that matches your OS color scheme. The bottom-left **☀ / ☾** icon in the status bar toggles between them at any time.

The bottom-left **`Npt`** label cycles UI font size through 9 / 11 / 12 / 14 / 16 pt.

## Command Line Interface

The CLI provides the same flash functionality without the GUI.

### Flash firmware

```
python3 flash_firmware.py /dev/ttyUSB0 firmware.kdhx
```

On Windows: `py flash_firmware.py COM3 firmware.kdhx`

### Dry run

```
python3 flash_firmware.py --dry-run none firmware.kdhx
```

### Diagnostics

```
python3 flash_firmware.py --diag /dev/ttyUSB0
```

## Auto-Updates

The app checks GitHub for the latest release on each launch (in the background — no modal dialog).

If a newer version is available, an **Update Available** link appears in the bottom-right of the status bar. Clicking it opens the GitHub releases page in your default browser so you can download the appropriate installer for your platform. The app does not attempt to apply updates in-place.

## Troubleshooting

### "No response from radio"

- Make sure the radio is in bootloader mode (blank screen, green LED)
- Check that the cable is plugged in and showing in the **Handset** column (click **Refresh / Probe** to re-scan)
- Try unplugging and replugging the cable — the polling loop picks up changes within a couple of seconds
- On Linux, ensure your user is in the `dialout` group (the app will surface a clear hint in the Log if it hits a permission error)

### "Permission denied" opening /dev/ttyUSB*

- Your Linux user is not in the `dialout` group.
- Fix it once: `sudo usermod -aG dialout $USER`, then **log out and back in** (a full re-login, not just a new terminal).

### Radio powers off when sending data

- The cable's RX line may be faulty — try a different cable
- Some cables have direction control issues on certain OS/driver combinations

### "Firmware too large" or "too many chunks"

- The protocol supports up to 255 chunks (261,120 bytes). Your file may not be a valid `.kdhx` firmware file.

### Themes don't apply to all widgets

- On Linux, GTK CSS is used for native widget theming. Try toggling the ☀ / ☾ icon in the status bar to force a re-apply.
- On Windows, some native widgets may not fully support custom colors.

### Windows: "Python not found" or "pip not found"

- Use `py` instead of `python` or `python3`
- Use `py -m pip install` instead of `pip install`
- If `py` doesn't work, reinstall Python from python.org with "Add Python to PATH" checked

## Adding New Radios

Edit `radios.json` to add your radio:

```json
{
  "id": "your-radio-id",
  "name": "Radio Name",
  "manufacturer": "Brand",
  "model_type": "MODEL",
  "firmware_url": null,
  "firmware_page": "https://manufacturer.com/downloads",
  "firmware_filename_pattern": "*.kdhx",
  "bootloader_keys": "Key combination to enter bootloader",
  "connector": "K1 Kenwood 2-pin",
  "tested": false,
  "notes": "Any relevant notes"
}
```

Submit a PR to share your addition with the community.

## Updating Firmware URLs

Found a new firmware version for a supported radio? You can update `firmware_manifest.json` and submit a PR. The app fetches this file from GitHub at runtime, so users get the update without needing a new app version.

```json
{
  "your-radio-id": {
    "firmware_version": "1.23",
    "firmware_url": "https://manufacturer.com/firmware-v1.23.zip",
    "firmware_sha256": null,
    "release_notes": "V1.23: brief description of changes"
  }
}
```

The `firmware_url` must be HTTPS and from an allowed domain (see `ALLOWED_DOMAINS` in `firmware_download.py`). If you need a new domain added, note it in your PR.
