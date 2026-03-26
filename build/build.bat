@echo off
setlocal EnableDelayedExpansion

:: ============================================================
::  build\build.bat — Build ImageManager distributable with PyInstaller
::
::  Output: dist\ImageManager\ImageManager.exe  (+ all DLLs)
::
::  Usage:  Double-click  OR  run from anywhere — the script
::          changes to the repo root automatically via %~dp0.
::  Requires the .venv virtual environment to already exist.
::  If PyInstaller is missing it will be installed automatically.
:: ============================================================

echo.
echo ============================================================
echo   ImageManager ^| PyInstaller build
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


:: ── SECTION 3: Ensure PyInstaller is installed ───────────────
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


:: ── SECTION 4: Clean previous build artefacts ────────────────
::
:: PyInstaller writes two directories:
::   build\ImageManager\   — intermediate work files (.toc, .pkg, .exe stubs)
::                           Safe to delete; PyInstaller regenerates them.
::   dist\ImageManager\    — the final distributable folder
::                           Cleaned so stale DLLs from old runs don't linger.
::
:: We only remove the ImageManager sub-folders, not the entire build\ or
:: dist\ trees — our spec/hook files in build\ are left untouched.

echo.
echo [INFO]  Cleaning previous build artefacts...

if exist "build\ImageManager" (
    rmdir /s /q "build\ImageManager"
    echo [OK]   Removed build\ImageManager\
)
if exist "dist\ImageManager" (
    rmdir /s /q "dist\ImageManager"
    echo [OK]   Removed dist\ImageManager\
)


:: ── SECTION 5: Run PyInstaller ───────────────────────────────
::
:: Flags used:
::   build\ImageManager.spec  — spec file inside the build\ folder
::   --distpath dist          — put the distributable folder under dist\
::   --workpath build         — put intermediate files under build\ImageManager\
::                              (a subfolder, won't touch our spec/hook files)
::   --noconfirm              — overwrite dist\ImageManager\ without asking
::
:: We call python -m PyInstaller rather than pyinstaller.exe directly —
:: it is more reliable when the venv Scripts folder is not on PATH.
::
:: PyInstaller exits with code 0 on success, non-zero on failure.

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


:: ── SECTION 6: Verify output ─────────────────────────────────
::
:: A quick sanity-check: confirm that ImageManager.exe actually appeared.
:: If it is missing something went wrong silently.

if not exist "dist\ImageManager\ImageManager.exe" (
    echo.
    echo [ERROR] dist\ImageManager\ImageManager.exe was not created.
    echo         PyInstaller reported success but the exe is missing.
    echo         Check the build log above for warnings.
    echo.
    pause
    exit /b 1
)


:: ── SECTION 7: Done ──────────────────────────────────────────

echo.
echo ============================================================
echo   Build complete!
echo.
echo   Distributable folder:
echo     dist\ImageManager\
echo.
echo   Run the app:
echo     dist\ImageManager\ImageManager.exe
echo.
echo   To distribute: copy the entire dist\ImageManager\ folder.
echo   The WD14 ONNX model will download on first launch
echo   (~400 MB) to %%USERPROFILE%%\.cache\huggingface\
echo ============================================================
echo.
pause
