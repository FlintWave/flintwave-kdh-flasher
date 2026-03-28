# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for FlintWave KDH Flasher
# Build with: pyinstaller build.spec

import sys
import os

# On Linux, find system wxPython if not in pip
extra_paths = []
if sys.platform.startswith('linux'):
    for p in ['/usr/lib/python3/dist-packages', '/usr/lib/python3.12/dist-packages']:
        if os.path.isdir(p):
            extra_paths.append(p)

a = Analysis(
    ['flash_firmware_gui.py'],
    pathex=extra_paths,
    binaries=[],
    datas=[
        ('radios.json', '.'),
        ('icon_128.png', '.'),
        ('USAGE.md', '.'),
        ('LICENSE', '.'),
    ],
    hiddenimports=[
        'flash_firmware',
        'firmware_download',
        'updater',
        'serial',
        'serial.tools',
        'serial.tools.list_ports',
        'requests',
        'wx',
        'wx.adv',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='FlintWave-KDH-Flasher',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon='icon.ico' if sys.platform == 'win32' else ('icon_128.png' if sys.platform != 'darwin' else None),
)

if sys.platform == 'darwin':
    app = BUNDLE(
        exe,
        name='FlintWave KDH Flasher.app',
        icon=None,
        bundle_identifier='com.flintwave.kdh-flasher',
    )
