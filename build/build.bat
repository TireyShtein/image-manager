@echo off
setlocal EnableDelayedExpansion


:: ── GPU BACKEND ARGUMENT PARSING ─────────────────────────────
::
::  Usage:  build.bat [--gpu cpu|directml|cuda]
::  Default: cpu
::
::  cpu        — onnxruntime (CPU only, smallest build)
::  directml   — onnxruntime-directml (any Windows DX12 GPU: NVIDIA / AMD / Intel)
::  cuda       — onnxruntime-gpu (NVIDIA + CUDA 12 + cuDNN required system-wide)
::
::  Output folder is labelled by backend:
::    dist\ImageManager\           (cpu)
::    dist\ImageManager-directml\  (directml)
::    dist\ImageManager-cuda\      (cuda)

set "GPU_BACKEND=cpu"
set "DIST_SUFFIX="
set "_PARSE_NEXT="
for %%A in (%*) do (
    if defined _PARSE_NEXT (
        set "GPU_BACKEND=%%A"
        set "_PARSE_NEXT="
    ) else if /I "%%A"=="--gpu" (
        set "_PARSE_NEXT=1"
    )
)
if /I "!GPU_BACKEND!"=="directml" set "DIST_SUFFIX=-directml"
if /I "!GPU_BACKEND!"=="cuda"     set "DIST_SUFFIX=-cuda"


:: ============================================================
::  build\build.bat — Build ImageManager distributable with PyInstaller
::
::  Output: dist\ImageManager!DIST_SUFFIX!\ImageManager.exe  (+ all DLLs)
::
::  Requires the .venv virtual environment to already exist.
::  If PyInstaller is missing it will be installed automatically.
:: ============================================================

echo.
echo ============================================================
echo   ImageManager ^| PyInstaller build  [GPU: !GPU_BACKEND!]
echo ============================================================
echo.


:: ── SECTION 1: Change to the repo root ───────────────────────
::
:: %~dp0 expands to the directory containing this .bat file,
:: i.e. <repo>\build\.  Appending ".." steps up to the repo root
:: where .venv\, main.py, requirements.txt, and dist\ all live.
::
:: /d also switches the drive letter in case the bat was launched
:: from a different drive (e.g. running D:\...\build.bat while
:: the current drive is C:\).

cd /d "%~dp0.."

echo [INFO]  Working directory: %CD%


:: ── SECTION 2: Locate the virtual environment ────────────────
::
:: All Python tools (pip, pyinstaller) are called through the
:: venv's Scripts folder so the correct Python version and all
:: installed packages are used — not whatever python.exe is on
:: the system PATH.

set "VENV_DIR=.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"
set "VENV_PIP=%VENV_DIR%\Scripts\pip.exe"

if not exist "%VENV_PYTHON%" (
    echo [ERROR] Virtual environment not found at: %VENV_DIR%\
    echo.
    echo         Create it first:
    echo           python -m venv .venv
    echo           .venv\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

echo [OK]   Virtual environment found: %VENV_DIR%\


:: ── SECTION 3: Install correct onnxruntime variant ───────────
::
:: onnxruntime, onnxruntime-gpu, and onnxruntime-directml are
:: mutually exclusive.  --upgrade ensures the right variant is
:: active even if a different one was previously installed.

echo.
echo [INFO]  Installing onnxruntime variant for backend: !GPU_BACKEND!...

if /I "!GPU_BACKEND!"=="directml" (
    "%VENV_PYTHON%" -m pip install onnxruntime-directml --upgrade
    if errorlevel 1 (
        echo [ERROR] Failed to install onnxruntime-directml.
        pause
        exit /b 1
    )
    echo [OK]   onnxruntime-directml installed.
) else if /I "!GPU_BACKEND!"=="cuda" (
    echo [NOTE]  CUDA build requires CUDA Toolkit 12.x + cuDNN installed system-wide.
    echo         Without them the app will silently fall back to CPU at runtime.
    "%VENV_PYTHON%" -m pip install "onnxruntime-gpu[cuda,cudnn]" --upgrade
    if errorlevel 1 (
        echo [ERROR] Failed to install onnxruntime-gpu.
        pause
        exit /b 1
    )
    echo [OK]   onnxruntime-gpu installed.
) else (
    "%VENV_PYTHON%" -m pip install onnxruntime --upgrade
    if errorlevel 1 (
        echo [ERROR] Failed to install onnxruntime.
        pause
        exit /b 1
    )
    echo [OK]   onnxruntime ^(CPU^) installed.
)


:: ── SECTION 4: Ensure PyInstaller is installed ───────────────
::
:: We try to import PyInstaller from the venv.  If that fails we
:: install it via pip before proceeding.  This makes the script
:: self-contained — a fresh clone only needs the venv + requirements.

"%VENV_PYTHON%" -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo [INFO]  PyInstaller not found — installing into venv...
    "%VENV_PIP%" install pyinstaller
    if errorlevel 1 (
        echo [ERROR] pip install pyinstaller failed.
        pause
        exit /b 1
    )
    echo [OK]   PyInstaller installed.
) else (
    for /f "delims=" %%v in ('"%VENV_PYTHON%" -c "import PyInstaller; print(PyInstaller.__version__)"') do set "PI_VER=%%v"
    echo [OK]   PyInstaller !PI_VER! already installed.
)


:: ── SECTION 5: Clean previous build artefacts ────────────────
::
:: PyInstaller writes two directories:
::   build\ImageManager\                 — intermediate work files (.toc, .pkg, .exe stubs)
::                                         Safe to delete; PyInstaller regenerates them.
::   dist\ImageManager!DIST_SUFFIX!\     — the final distributable folder
::                                         Cleaned so stale DLLs from old runs don't linger.
::
:: We only remove the ImageManager sub-folders, not the entire build\ or
:: dist\ trees — our spec/hook files in build\ are left untouched.

echo.
echo [INFO]  Cleaning previous build artefacts...

if exist "build\ImageManager" (
    rmdir /s /q "build\ImageManager"
    echo [OK]   Removed build\ImageManager\
)
if exist "dist\ImageManager!DIST_SUFFIX!" (
    rmdir /s /q "dist\ImageManager!DIST_SUFFIX!"
    echo [OK]   Removed dist\ImageManager!DIST_SUFFIX!\
)


:: ── SECTION 6: Run PyInstaller ───────────────────────────────
::
:: Flags used:
::   build\ImageManager.spec  — spec file inside the build\ folder
::   --distpath dist          — put the distributable folder under dist\
::   --workpath build         — put intermediate files under build\ImageManager\
::   --noconfirm              — overwrite dist\ImageManager\ without asking
::
:: After a successful build, rename dist\ImageManager\ to dist\ImageManager-<suffix>\
:: if a GPU backend was selected.

echo.
echo [INFO]  Running PyInstaller...
echo.

"%VENV_PYTHON%" -m PyInstaller build\ImageManager.spec ^
    --distpath dist ^
    --workpath build ^
    --noconfirm

if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller exited with an error.
    echo         Scroll up to find the first ERROR or CRITICAL line.
    echo.
    pause
    exit /b 1
)

if not "!DIST_SUFFIX!"=="" (
    if exist "dist\ImageManager" (
        move "dist\ImageManager" "dist\ImageManager!DIST_SUFFIX!"
        echo [OK]   Renamed dist\ImageManager\ to dist\ImageManager!DIST_SUFFIX!\
    )
)


:: ── SECTION 7: Verify output ─────────────────────────────────
::
:: A quick sanity-check: confirm that ImageManager.exe actually appeared.

if not exist "dist\ImageManager!DIST_SUFFIX!\ImageManager.exe" (
    echo.
    echo [ERROR] dist\ImageManager!DIST_SUFFIX!\ImageManager.exe was not created.
    echo         PyInstaller reported success but the exe is missing.
    echo         Check the build log above for warnings.
    echo.
    pause
    exit /b 1
)


:: ── SECTION 8: Done ──────────────────────────────────────────

echo.
echo ============================================================
echo   Build complete!  [GPU backend: !GPU_BACKEND!]
echo.
echo   Distributable folder:
echo     dist\ImageManager!DIST_SUFFIX!\
echo.
echo   Run the app:
echo     dist\ImageManager!DIST_SUFFIX!\ImageManager.exe
echo.
echo   To distribute: copy the entire dist\ImageManager!DIST_SUFFIX!\ folder.
echo   The WD14 ONNX model will download on first launch
echo   (~400 MB) to %%USERPROFILE%%\.cache\huggingface\
if /I "!GPU_BACKEND!"=="cuda" (
    echo.
    echo   [CUDA] End users must have CUDA Toolkit 12.x + cuDNN installed.
    echo          Without them, tagging will silently fall back to CPU.
)
echo ============================================================
echo.
pause
