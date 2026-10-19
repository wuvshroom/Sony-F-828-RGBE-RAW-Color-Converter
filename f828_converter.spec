# -*- mode: python ; coding: utf-8 -*-
# Build:  pyinstaller f828_converter.spec
# Bundles the two Lightroom camera profiles (for the DNG export's "Sony look")
# and, when they are installed, the optional codecs imagecodecs and OpenEXR.
# The converter loads those by name, so PyInstaller cannot find them on its own;
# a codec that is not installed just stays unavailable.
#
# Licensing: the project is PolyForm Noncommercial, so nothing GPL may go into
# the .exe. That rules out PyQt6 (the UI uses PySide6, LGPL) and pillow-heif,
# whose binary wheels are GPLv2 because they contain x265. The release .exe
# therefore has no HEIF export; people running from source can install
# pillow-heif themselves.
#
# Numba (BSD) and llvmlite are picked up through pyinstaller-hooks-contrib's hooks.
# The .exe compiles its kernels on first launch and caches them in the user's
# cache folder (%LOCALAPPDATA%\numba), so later launches start straight away.
from PyInstaller.utils.hooks import collect_submodules, collect_delvewheel_libs_directory

datas = [('lightroom-profile/Sony DSC-F828 IDC Real.dcp', 'profiles'),
         ('lightroom-profile/Sony DSC-F828 IDC Standard.dcp', 'profiles')]
binaries = []
hiddenimports = []
for pkg, extra in (('imagecodecs', []), ('OpenEXR', ['Imath'])):
    try:
        __import__(pkg)
    except Exception:
        continue
    hiddenimports += [pkg] + collect_submodules(pkg) + extra
    if pkg != 'OpenEXR':                 # a single module, not a package
        datas, binaries = collect_delvewheel_libs_directory(pkg, datas=datas, binaries=binaries)

a = Analysis(
    ['f828_converter.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PyQt5', 'PyQt6', 'sip', 'pillow_heif'],   # GPL: must never end up in the .exe
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
    name='f828_converter',
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
)
