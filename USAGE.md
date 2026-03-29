# Usage Guide

## Installation

### Installers (recommended)

Download the latest release from the [Releases page](https://github.com/FlintWave/flintwave-kdh-flasher/releases/latest):

| OS | File | Install |
|----|------|---------|
| Debian/Ubuntu/Pop!_OS/Mint | `.deb` | `sudo dpkg -i flintwave-kdh-flasher_*.deb` |
| Fedora/RHEL/openSUSE | `.rpm` | `sudo rpm -i flintwave-kdh-flasher-*.rpm` |
| Windows | `FlintWave-KDH-Flasher-Setup.exe` | Run the installer |
| macOS | `FlintWave-KDH-Flasher.dmg` | Open and drag to Applications |

After installing, search for "FlintWave" in your app launcher, Start Menu, or Applications folder.

### Portable (no install needed)

| OS | File | How to run |
|----|------|-----------|
| Linux | `.AppImage` | `chmod +x *.AppImage` and double-click |
| Windows | `FlintWave-KDH-Flasher.exe` | Double-click to run |

### Install from source

#### Linux

```bash
sudo apt install python3-wxgtk4.0 python3-serial python3-requests git
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
brew install python wxpython
pip3 install pyserial requests
git clone https://github.com/FlintWave/flintwave-kdh-flasher.git
cd flintwave-kdh-flasher
python3 flash_firmware_gui.py
```

#### Windows

1. Install [Python 3.10+](https://python.org) — **check "Add Python to PATH" during install**
2. Open Command Prompt:
```
py -m pip install pyserial wxPython requests
```
3. Download and extract the [source ZIP](https://github.com/FlintWave/flintwave-kdh-flasher/archive/refs/heads/master.zip)
4. Run: `py flash_firmware_gui.py`

## Flashing Firmware

### Step 1: Select your radio

Use the **Radio** dropdown at the top of the window. The info line below shows the bootloader key combination and connector type for your model.

### Step 2: Get the firmware file

**Option A — Automatic download:**
If the "Download Latest" button is enabled, click it. The tool downloads the firmware bundle from the manufacturer's website, extracts the `.kdhx` file, and fills in the path automatically.

**Option B — Manual download:**
Visit your radio manufacturer's website, download the firmware bundle (usually a `.zip`), extract it, and click **Browse...** to select the `.kdhx` file.

### Step 3: Connect your programming cable

Plug in your programming cable (PC03 or compatible K1 2-pin Kenwood cable), then click **Find Cable...** to detect it. The wizard scans for USB serial devices and auto-highlights known cable chips (FTDI, CH340, Prolific, etc.).

**Important cable tips:**
- Turn the radio's volume to **maximum** before connecting
- Push the cable connector **firmly** into the radio — it needs more force than you'd expect
- You may need to **hold pressure on the connector during the entire flash** — the K1 2.5mm ring contact that carries return data is sensitive to movement
- If you get "no response" errors, try pressing harder or at a slight angle

**Tip:** If your cable isn't listed, unplug it, click Rescan, plug it back in, and click Rescan again. The new entry is your cable.

### Step 4: Verify with a dry run

Click **Dry Run** to verify the firmware file without touching the radio. This checks:
- File size is within protocol limits
- ARM vector table has valid stack pointer and reset handler
- All data packets build correctly with valid CRCs
- SHA-256 hash is displayed for verification against published hashes

### Step 5: Test serial communication

With the radio in bootloader mode (see below), click **Run Diagnostics** to test whether the tool can communicate with the radio. If you see a response, you're good to flash.

### Step 6: Flash

1. Put the radio in **bootloader mode**:
   - Power off the radio completely
   - Hold the bootloader keys (shown in the info line — e.g., SK1 + SK2 for BF-F8HP Pro)
   - While holding both keys, turn the power/volume knob to power on
   - The screen stays blank and the green Rx LED lights up
   - Do NOT release the keys until the LED is on

2. Click **Flash Firmware**. Read and confirm the warning dialog — it shows your specific radio's instructions.

3. Wait for the progress bar to complete. **Do not disconnect the cable or power off the radio during flashing.**

4. When complete, power cycle the radio and verify the firmware version via **Menu > Radio Info**.

5. After flashing, you'll be offered the option to submit a test report. This helps us track which radios have been verified.

## Bootloader Mode Quick Reference

| Radio | Keys to Hold |
|-------|-------------|
| BTECH BF-F8HP Pro | SK1 (top) + SK2 (bottom) — not PTT. Volume to max. Hold cable firmly. |
| Baofeng UV-25 Plus/Pro | SK2 + SK3 |
| Others | Check your radio's manual or `radios.json` |

**Important:** The side keys are the small buttons above and below the large PTT button. Do not hold PTT itself.

## Themes and Accessibility

Use **View > Theme** to switch between:
- **System Default** — follows your OS theme
- **Latte** — Catppuccin light theme
- **Frappé** — Catppuccin medium-dark theme
- **Macchiato** — Catppuccin dark theme
- **Mocha** — Catppuccin darkest theme
- **High Contrast** — black background, yellow/green text

Use **View > Log Font Size** to adjust text size (8pt, 9pt, 11pt, or 14pt).

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

The app checks GitHub for updates on each launch.

- **Source installs (git clone):** prompted to update automatically via `git pull`, then the app restarts.
- **Packaged installs (.deb, .rpm, .exe, .dmg, AppImage):** prompted to open the releases page to download the latest version.

## Troubleshooting

### "No response from radio"

- Make sure the radio is in bootloader mode (blank screen, green LED)
- Check that the cable is plugged in and detected (use Find Cable)
- Try unplugging and replugging the cable
- On Linux, ensure your user is in the `dialout` group

### Radio powers off when sending data

- The cable's RX line may be faulty — try a different cable
- Some cables have direction control issues on certain OS/driver combinations

### "Firmware too large" or "too many chunks"

- The protocol supports up to 255 chunks (261,120 bytes). Your file may not be a valid `.kdhx` firmware file.

### Themes don't apply to all widgets

- On Linux, GTK CSS is used for native widget theming. Try switching to System Default and back.
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
