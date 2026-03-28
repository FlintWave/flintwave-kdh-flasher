#!/bin/bash
# FlintWave KDH Flasher — Linux/macOS installer
# curl -sL https://raw.githubusercontent.com/FlintWave/flintwave-kdh-flasher/master/install.sh | bash

set -e

REPO="https://github.com/FlintWave/flintwave-kdh-flasher.git"
INSTALL_DIR="$HOME/.local/share/flintwave-kdh-flasher"

echo "==================================="
echo "  FlintWave KDH Flasher Installer"
echo "==================================="
echo

# Check Python
if command -v python3 &>/dev/null; then
    PYTHON=python3
elif command -v python &>/dev/null; then
    PYTHON=python
else
    echo "ERROR: Python 3.10+ is required but not found."
    echo "Install it from https://python.org or your package manager."
    exit 1
fi

# Check Python version
PY_VER=$($PYTHON -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$($PYTHON -c "import sys; print(sys.version_info.major)")
PY_MINOR=$($PYTHON -c "import sys; print(sys.version_info.minor)")

if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]); then
    echo "ERROR: Python 3.10+ required, found $PY_VER"
    exit 1
fi
echo "Found Python $PY_VER"

# Install Python dependencies
echo "Installing dependencies..."
$PYTHON -m pip install --user --quiet pyserial requests 2>/dev/null || \
    $PYTHON -m pip install --quiet pyserial requests

# wxPython — try pip first, fall back to system package
if ! $PYTHON -c "import wx" 2>/dev/null; then
    echo "Installing wxPython (this may take a moment)..."
    $PYTHON -m pip install --user --quiet wxPython 2>/dev/null || \
        $PYTHON -m pip install --quiet wxPython 2>/dev/null || {
        echo ""
        echo "wxPython pip install failed. Trying system package..."
        if command -v apt &>/dev/null; then
            sudo apt install -y python3-wxgtk4.0
        elif command -v dnf &>/dev/null; then
            sudo dnf install -y python3-wxpython4
        elif command -v pacman &>/dev/null; then
            sudo pacman -S --noconfirm python-wxpython
        elif command -v brew &>/dev/null; then
            brew install wxpython
        else
            echo "ERROR: Could not install wxPython automatically."
            echo "Install it manually: https://wxpython.org/pages/downloads/"
            exit 1
        fi
    }
fi

# Clone or update
if [ -d "$INSTALL_DIR/.git" ]; then
    echo "Updating existing installation..."
    cd "$INSTALL_DIR"
    git pull --ff-only
else
    echo "Downloading..."
    git clone --depth 1 "$REPO" "$INSTALL_DIR"
fi

# Linux: add dialout group and desktop entry
if [ "$(uname)" = "Linux" ]; then
    if ! groups | grep -q dialout; then
        echo "Adding you to the dialout group (needed for serial port access)..."
        sudo usermod -aG dialout "$USER" 2>/dev/null || true
        echo "NOTE: Log out and back in for serial port access to take effect."
    fi

    mkdir -p "$HOME/.local/share/applications"
    mkdir -p "$HOME/.local/share/icons/hicolor/128x128/apps"

    cat > "$HOME/.local/share/applications/flintwave-kdh-flasher.desktop" << EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=FlintWave KDH Flasher
GenericName=Radio Firmware Flasher
Comment=Flash firmware to BTECH, Baofeng, and other KDH bootloader radios
Icon=flintwave-kdh-flasher
Exec=$PYTHON $INSTALL_DIR/flash_firmware_gui.py
Terminal=false
Categories=Utility;HamRadio
Keywords=Hamradio;Firmware;Flasher;Baofeng;BTECH;Radio;FlintWave
StartupNotify=true
EOF

    if [ -f "$INSTALL_DIR/icon_128.png" ]; then
        cp "$INSTALL_DIR/icon_128.png" \
           "$HOME/.local/share/icons/hicolor/128x128/apps/flintwave-kdh-flasher.png"
    fi

    update-desktop-database "$HOME/.local/share/applications/" 2>/dev/null || true
    gtk-update-icon-cache "$HOME/.local/share/icons/hicolor/" 2>/dev/null || true
fi

echo
echo "==================================="
echo "  Installation complete!"
echo "==================================="
echo
echo "Run the GUI:  $PYTHON $INSTALL_DIR/flash_firmware_gui.py"
echo "Run the CLI:  $PYTHON $INSTALL_DIR/flash_firmware.py --help"
if [ "$(uname)" = "Linux" ]; then
    echo "App launcher: search for 'FlintWave' or 'KDH'"
fi
echo
