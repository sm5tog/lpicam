# -*- mode: python ; coding: utf-8 -*-
# Build: python -m PyInstaller lpicam.spec --noconfirm
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

a = Analysis(
    ['lpicam.py'],
    pathex=[],
    binaries=[],
    datas=collect_data_files('customtkinter'),
    hiddenimports=collect_submodules('pygrabber') + collect_submodules('comtypes'),
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
    a.binaries,
    a.datas,
    [],
    name='LPICam',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
