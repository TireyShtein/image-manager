# ImageManager.spec
# -*- mode: python ; coding: utf-8 -*-
#
# PyInstaller spec file for ImageManager.
#
# A .spec file is plain Python executed by PyInstaller.  You have full access
# to the Python stdlib here.  PyInstaller injects four special classes into the
# namespace: Analysis, PYZ, EXE, COLLECT — described in detail below.
#
# PyInstaller also injects SPECPATH — the absolute path to the directory that
# contains this spec file (i.e. the build\ folder).  We use it to build
# absolute paths so the spec works regardless of which directory PyInstaller
# is invoked from.
#
# Build with:  build\build.bat
# Output:      dist\ImageManager\ImageManager.exe  (+ all DLLs / data files)

import glob
import os
import sys
from PyInstaller.utils.hooks import collect_all, collect_data_files

# SPECPATH is injected by PyInstaller — it is the directory of this .spec file,
# i.e. <repo>\build\.  ROOT is one level up: the repo root where main.py lives.
ROOT = os.path.dirname(SPECPATH)   # <repo>\


# ══════════════════════════════════════════════════════════════════════════════
# STEP 1 — Locate the Python runtime DLL
# ══════════════════════════════════════════════════════════════════════════════
#
# The frozen exe still needs the Python runtime DLL (python314.dll, python313.dll,
# etc.) at startup to bootstrap the embedded interpreter.  PyInstaller usually
# copies it automatically, but on some Windows setups the DLL lives only inside
# the Python installation directory which is NOT on the system PATH.  The bundled
# exe would then crash with "python3XX.dll not found" even though everything else
# is present.
#
# We search a list of known locations and, if found, add the DLL to
# `extra_binaries` so it is copied right next to ImageManager.exe in the dist
# folder — the safest place for Windows to find it at load time.

python_dll_path = None

search_dirs = [
    sys.exec_prefix,                                    # e.g. C:\Python314
    os.path.dirname(sys.executable),                    # wherever python.exe is
    os.path.join(sys.exec_prefix, 'DLLs'),              # Python's own DLLs sub-folder
    os.path.join(sys.exec_prefix, 'Library', 'bin'),    # conda layout
]

for directory in search_dirs:
    hits = glob.glob(os.path.join(directory, 'python3*.dll'))
    if hits:
        # Take the first match — there will only ever be one per installation.
        python_dll_path = hits[0]
        print(f'[spec] Found Python DLL: {python_dll_path}')
        break

if not python_dll_path:
    print('[spec] WARNING: Python runtime DLL not found in known locations.')
    print('[spec]          The bundle may still work if the DLL is on the system PATH.')

# Binaries entries are tuples: (source_file, destination_directory_inside_bundle)
# '.' as destination means the file lands in the root of dist\ImageManager\,
# right next to ImageManager.exe — the first place Windows looks for DLLs.
extra_binaries = []
if python_dll_path:
    extra_binaries.append((python_dll_path, '.'))


# ══════════════════════════════════════════════════════════════════════════════
# STEP 2 — Collect full package contents for complex dependencies
# ══════════════════════════════════════════════════════════════════════════════
#
# `collect_all(package)` walks the installed package directory and returns a
# 3-tuple:  (datas, binaries, hiddenimports)
#
#   datas         — non-Python resource files (JSON, CSS, model metadata, …)
#   binaries      — compiled extension modules and DLLs (.pyd, .dll, .so)
#   hiddenimports — submodule names that static analysis might miss
#
# We must use this for packages that:
#   • ship native DLLs  (PyQt6 → Qt6Core.dll, onnxruntime → onnxruntime_providers_shared.dll)
#   • rely on plugin discovery at runtime  (Qt platform plugins, image format plugins)
#   • use dynamic imports that the analyser cannot trace  (onnxruntime execution providers)

# PyQt6 — the entire Qt framework: DLLs, platform plugins, image plugins, styles.
# Without collect_all the app would open then immediately crash because Qt cannot
# find its 'platforms/qwindows.dll' plugin.
qt_datas, qt_bins, qt_hidden = collect_all('PyQt6')

# onnxruntime — ONNX execution providers (CPU, DirectML, …) are loaded via
# ctypes at runtime; static import tracing never sees them.
onnx_datas, onnx_bins, onnx_hidden = collect_all('onnxruntime')

# Pillow — image codec plugins (JPEG, PNG, WebP, …) are C extensions loaded
# by the PIL plugin registry; they must all be present in the bundle.
pil_datas, pil_bins, pil_hidden = collect_all('PIL')

# huggingface_hub — ships JSON configs and certificate bundles used when
# downloading the WD14 model on first launch; missing data → SSL/decode errors.
hf_datas, hf_bins, hf_hidden = collect_all('huggingface_hub')


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3 — Analysis
# ══════════════════════════════════════════════════════════════════════════════
#
# Analysis is the *discovery* phase.  PyInstaller starts at main.py and follows
# every import recursively, building a complete dependency graph.  The results
# are stored in `a` and referenced by the later stages.
#
# Key parameters explained:
#
#   scripts       — entry-point Python files.  We use an absolute path built
#                   from ROOT so the spec works from any working directory.
#
#   pathex        — extra directories prepended to sys.path during analysis.
#                   ROOT ensures the `src/` package is found from the repo root.
#
#   binaries      — list of (src, dest) tuples for DLLs/extensions to copy.
#                   Merged from our manual search + collect_all results above.
#
#   datas         — list of (src, dest) tuples for non-Python resource files.
#                   Same merge strategy as binaries.
#
#   hiddenimports — module names to force-include even if not seen by analysis.
#                   Needed for:
#                     • PyQt6.sip        (C extension, not a .py, easy to miss)
#                     • onnxruntime CAPI (loaded via ctypes, invisible to tracer)
#                     • send2trash.plat_win (selected by platform at runtime)
#
#   hookspath     — extra directories containing hook-<pkg>.py files.  Hooks
#                   are recipes that tell PyInstaller how to handle tricky
#                   packages.  Empty here because collect_all already covers us.
#
#   runtime_hooks — .py scripts injected into the frozen app and run *before*
#                   main.py.  runtime_hook_onnxruntime.py registers the
#                   onnxruntime/capi/ subdirectory via os.add_dll_directory()
#                   so Windows can find onnxruntime.dll at import time.
#                   We use SPECPATH to build the absolute path so it resolves
#                   correctly regardless of the working directory at build time.
#
#   excludes      — modules to strip from the bundle even if analysis finds them.
#                   Removing unused stdlib/framework code shrinks the dist folder.

a = Analysis(
    [os.path.join(ROOT, 'main.py')],
    pathex=[ROOT],
    binaries=(
        extra_binaries
        + qt_bins
        + onnx_bins
        + pil_bins
        + hf_bins
    ),
    datas=(
        qt_datas
        + onnx_datas
        + pil_datas
        + hf_datas
    ),
    hiddenimports=(
        qt_hidden
        + onnx_hidden
        + pil_hidden
        + hf_hidden
        + [
            # PyQt6 core modules — sometimes missed by the tracer when PyQt6
            # is imported at the top level without explicit submodule imports.
            'PyQt6.QtCore',
            'PyQt6.QtGui',
            'PyQt6.QtWidgets',
            'PyQt6.QtNetwork',
            'PyQt6.sip',
            # onnxruntime C-API binding loaded by ctypes, not a Python import
            'onnxruntime.capi._pybind_state',
            # send2trash chooses its backend at runtime based on the platform
            'send2trash',
            'send2trash.plat_win',
        ]
    ),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[os.path.join(SPECPATH, 'runtime_hook_onnxruntime.py')],
    excludes=[
        # Large stdlib / third-party modules not used by this app.
        # Removing them reduces dist size without affecting functionality.
        'tkinter',
        '_tkinter',
        'matplotlib',
        'IPython',
        'jupyter',
        'pytest',
        'xmlrpc',
        'pydoc',
        'doctest',
        'difflib',
        'ftplib',
        'imaplib',
        'poplib',
        'smtplib',
        'telnetlib',
    ],
    noarchive=False,    # keep modules compressed in the PYZ archive
    optimize=0,         # 0 = keep asserts and docstrings (safer for debugging)
)


# ══════════════════════════════════════════════════════════════════════════════
# STEP 3b — Strip conflicting MSVC runtime DLLs from the bundle
# ══════════════════════════════════════════════════════════════════════════════
#
# PyInstaller copies msvcp140.dll / vcruntime140.dll etc. from the Python
# installation into the bundle.  These are the versions Python itself was
# compiled with — NOT necessarily the versions onnxruntime was compiled with.
#
# When onnxruntime_pybind11_state.pyd loads, it calls LoadLibrary on these
# DLLs.  Windows finds the bundled copies first (they are in _internal\ which
# PyInstaller adds to the DLL search path).  If the bundled version does not
# match what onnxruntime expects, DllMain fails → the infamous
# "A dynamic link library (DLL) initialization routine failed" error.
#
# Fix: remove those DLLs from a.binaries so they are NOT bundled.  Windows
# then falls through to the system-installed copies from the Visual C++
# Redistributable, which are the correct versions for onnxruntime.
#
# Prerequisite: the end-user machine must have the Visual C++ Redistributable
# 2015-2022 installed (x64).  This is already a near-universal requirement on
# modern Windows; if it is ever missing the installer can bundle it separately.

_MSVC_REDIST = {
    'msvcp140.dll',
    'msvcp140_1.dll',
    'msvcp140_2.dll',
    'vcruntime140.dll',
    'vcruntime140_1.dll',
}

# a.binaries is a list of (name, source_path, type) TOC entries.
# We keep every entry whose filename (the first element) is NOT in our set.
a.binaries = [
    b for b in a.binaries
    if os.path.basename(b[0]).lower() not in _MSVC_REDIST
]


# ══════════════════════════════════════════════════════════════════════════════
# STEP 4 — PYZ  (Python ZIP archive)
# ══════════════════════════════════════════════════════════════════════════════
#
# PYZ takes all *pure-Python* modules found by Analysis (a.pure) and packs
# them into a single compressed .pyz archive file.  At runtime the frozen
# interpreter extracts modules from this archive instead of reading .py files.
#
# This is separate from native DLLs / .pyd extensions, which cannot be stored
# in a zip and are placed as loose files by COLLECT.

pyz = PYZ(a.pure)


# ══════════════════════════════════════════════════════════════════════════════
# STEP 5 — EXE  (the launcher binary)
# ══════════════════════════════════════════════════════════════════════════════
#
# EXE produces ImageManager.exe.  In onedir mode (our mode) it is a small C
# stub — the PyInstaller bootloader — that:
#   1. Sets sys.path to point at the dist folder
#   2. Unpacks / maps the PYZ archive
#   3. Calls main.py's main() function
#
# Key parameters:
#
#   exclude_binaries=True  — binaries are NOT embedded inside the exe; they stay
#                            as loose files and are assembled by COLLECT below.
#                            This is the "onedir" (folder) distribution mode.
#                            The alternative (onefile) embeds everything but is
#                            slower to start because it must unpack to a temp dir.
#
#   console=False          — suppresses the black console window.  Our app is a
#                            pure Qt GUI; a console would just flash and annoy users.
#
#   upx=False              — UPX compression is disabled.  UPX can shrink DLLs ~30%
#                            but regularly triggers antivirus false positives and can
#                            corrupt certain Qt DLLs.  Not worth the risk.
#
#   debug=False            — disables bootloader debug output.  Set True temporarily
#                            if the exe crashes at startup to see import errors.

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='ImageManager',
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
    # icon=os.path.join(ROOT, 'assets', 'icon.ico'),   # ← uncomment to embed an app icon
)


# ══════════════════════════════════════════════════════════════════════════════
# STEP 6 — COLLECT  (assemble dist\ImageManager\)
# ══════════════════════════════════════════════════════════════════════════════
#
# COLLECT gathers everything — the exe, all binaries, all datas — into the
# final output folder.  This is what you distribute to end users.
#
# Resulting layout (approx):
#
#   dist\ImageManager\
#     ImageManager.exe          ← the launcher (STEP 5)
#     python314.dll             ← Python runtime (STEP 1)
#     Qt6Core.dll               ┐
#     Qt6Gui.dll                │ Qt framework DLLs
#     Qt6Widgets.dll            ┘
#     PyQt6\Qt6\plugins\        ← Qt plugins (platforms, imageformats, styles)
#     onnxruntime\              ← ONNX provider DLLs
#     PIL\                      ← Pillow codec extensions
#     huggingface_hub\          ← HF config/cert data
#     base_library.zip          ← minimal frozen stdlib for the bootloader
#     ImageManager.pkg          ← the PYZ archive (all pure-Python modules)
#
# `name='ImageManager'` sets the subfolder name inside dist\.

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='ImageManager',
)
