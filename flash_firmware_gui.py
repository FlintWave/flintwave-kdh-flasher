#!/usr/bin/env python3
"""
GUI frontend for the KDH bootloader firmware flasher.
Supports BTECH, Baofeng, Radtel, and other KDH-based radios.
Cross-platform: works on Linux, macOS, and Windows.

This is a thin launcher. The implementation lives in:
  gui_main.py    — FlasherFrame class and main() entry point
  gui_dialogs.py — PortFinderDialog, About dialog, Test report dialog
  gui_themes.py  — Theme palettes, GTK CSS, theme application logic
  gui_ports.py   — Cable detection and serial port enumeration
"""

from gui_main import main, FlasherFrame  # noqa: F401

# Canonical version — kept here so tests and build tooling can find it
# by reading this file.  gui_main.py and gui_dialogs.py import their own
# copy; keep them in sync when bumping.
VERSION = "26.05.6"

# Single theme: "frappe" — defined in gui_themes.FRAPPE_PALETTE.

if __name__ == "__main__":
    main()
