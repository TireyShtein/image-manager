# runtime_hook_onnxruntime.py
# PyInstaller runtime hook — executed BEFORE main.py
#
# Problem: On Windows (Python 3.8+), DLL search is restricted to the .exe
# directory, System32, and directories added via os.add_dll_directory().
# When PyInstaller bundles onnxruntime, its native DLLs (onnxruntime.dll,
# onnxruntime_providers_shared.dll) land in onnxruntime/capi/ inside the
# bundle — a subdirectory that Windows does NOT search automatically.
# The .pyd extension loads, but its LoadLibrary("onnxruntime.dll") call
# fails with "DLL initialization routine failed".
#
# Fix: Register the onnxruntime/capi/ directory so Windows can find the DLLs.

import os
import sys

if sys.platform == "win32" and hasattr(os, "add_dll_directory"):
    # In a frozen PyInstaller app, sys._MEIPASS is the bundle's root directory
    # (e.g. dist\ImageManager\).  In onedir mode this is the folder containing
    # the .exe; in onefile mode it is the temp extraction directory.
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))

    onnx_capi_dir = os.path.join(base, "onnxruntime", "capi")
    if os.path.isdir(onnx_capi_dir):
        os.add_dll_directory(onnx_capi_dir)

    # Also add the bundle root itself — some PyInstaller versions place
    # dependency DLLs (e.g. numpy's libopenblas) at the top level.
    if os.path.isdir(base):
        os.add_dll_directory(base)
