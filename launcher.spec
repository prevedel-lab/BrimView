# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import copy_metadata

datas = [('src/index.py', 'src/.'), ('src/BrimView.png', 'src/.')]
datas += copy_metadata('pandas')


a = Analysis(
    ['src/launcher.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=['brimfile', 'brimview_widgets', 'pandas', 's3fs'],
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
    name='BrimView',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

# app = BUNDLE(exe,
#          name='BrimView.app',
#          icon='src/BrimView.png',
#          bundle_identifier=None)
