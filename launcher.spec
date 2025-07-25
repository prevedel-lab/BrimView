# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, copy_metadata

a = Analysis(
    ['src\\launcher.py'],
    pathex=[],
    binaries=[],
    datas=[
        ('./src/index.py', './src/.'),
        ('./src/BrimView.png', './src/.'),
        *copy_metadata('pandas'),  # ✅ fix for version detection
        ],
    hiddenimports=[
        'brimfile',
        'bls_panel_app_widgets',
        'pandas',
        ],
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
    name='launcher',
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
    name='launcher',
)
