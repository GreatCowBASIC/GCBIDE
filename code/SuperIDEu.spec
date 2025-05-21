# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['SuperIDEu.py'],
    pathex=[],
    binaries=[],
    datas=[('.\\app_icon.ico', '.'), ('.\\asm.png', '.'), ('.\\GCB.tmLanguage.json', '.'), ('.\\GCstudio.png', '.'), ('.\\help.png', '.'), ('.\\hex.png', '.'), ('.\\hexflash.png', '.'), ('.\\license.txt', '.'), ('.\\requirements.txt', '.'), ('.\\tasks.json', '.'), ('.\\gcb-icons\\24_24_icons\\asm.png', 'gcb-icons\\24_24_icons'), ('.\\gcb-icons\\24_24_icons\\demos.png', 'gcb-icons\\24_24_icons'), ('.\\gcb-icons\\24_24_icons\\flash.png', 'gcb-icons\\24_24_icons'), ('.\\gcb-icons\\24_24_icons\\help.png', 'gcb-icons\\24_24_icons'), ('.\\gcb-icons\\24_24_icons\\hex.png', 'gcb-icons\\24_24_icons'), ('.\\gcb-icons\\24_24_icons\\hexflash.png', 'gcb-icons\\24_24_icons'), ('.\\gcb-icons\\24_24_icons\\pps.png', 'gcb-icons\\24_24_icons'), ('.\\gcb-icons\\32_32_icons\\asm.png', 'gcb-icons\\32_32_icons'), ('.\\gcb-icons\\32_32_icons\\demos.png', 'gcb-icons\\32_32_icons'), ('.\\gcb-icons\\32_32_icons\\flash.png', 'gcb-icons\\32_32_icons'), ('.\\gcb-icons\\32_32_icons\\help.png', 'gcb-icons\\32_32_icons'), ('.\\gcb-icons\\32_32_icons\\hex.png', 'gcb-icons\\32_32_icons'), ('.\\gcb-icons\\32_32_icons\\hexflash.png', 'gcb-icons\\32_32_icons'), ('.\\gcb-icons\\32_32_icons\\pps.png', 'gcb-icons\\32_32_icons'), ('.\\gcb-icons\\64_64_icons\\asm.png', 'gcb-icons\\64_64_icons'), ('.\\gcb-icons\\64_64_icons\\demos.png', 'gcb-icons\\64_64_icons'), ('.\\gcb-icons\\64_64_icons\\flash.png', 'gcb-icons\\64_64_icons'), ('.\\gcb-icons\\64_64_icons\\help.png', 'gcb-icons\\64_64_icons'), ('.\\gcb-icons\\64_64_icons\\hex.png', 'gcb-icons\\64_64_icons'), ('.\\gcb-icons\\64_64_icons\\hexflash.png', 'gcb-icons\\64_64_icons'), ('.\\gcb-icons\\64_64_icons\\pps.png', 'gcb-icons\\64_64_icons')],
    hiddenimports=[],
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
    name='SuperIDEu',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['app_icon.ico'],
)
