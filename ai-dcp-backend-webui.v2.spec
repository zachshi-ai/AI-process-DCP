# -*- mode: python ; coding: utf-8 -*-

import os

ROOT = os.path.abspath(os.getcwd())
CONFIG_DIR = os.path.join(ROOT, "backend", "config")

a = Analysis(
    [os.path.join(ROOT, "backend", "desktop_entry_webui.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[(CONFIG_DIR, "config")],
    hiddenimports=["main_webui"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ai-dcp-backend-webui",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="ai-dcp-backend-webui",
)

