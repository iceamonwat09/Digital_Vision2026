@echo off
REM =====================================================================
REM  setup_venv.bat - create the project venv and install server packages
REM
REM  WHY THIS FILE EXISTS
REM    Copying a multi-line command block from the guide into cmd.exe often
REM    loses the line breaks, so every line runs glued together as one
REM    command ("The system cannot find the path specified."). Run this
REM    script instead - it does every step in the right order, and stops
REM    with a clear message if something is missing.
REM
REM  USAGE (from the project root, Command Prompt as Administrator):
REM      deploy\setup_venv.bat
REM
REM  If Python 3.9 is NOT at C:\Python39, pass its full path:
REM      deploy\setup_venv.bat "D:\Python39\python.exe"
REM =====================================================================
setlocal

REM ---- project root = the folder that contains this script's parent -----
set "ROOT=%~dp0.."
pushd "%ROOT%" || (echo [FAIL] cannot enter project root & exit /b 1)
set "ROOT=%CD%"

echo.
echo ======================================================================
echo   VisionIQ - server venv setup
echo   Project root : %ROOT%
echo ======================================================================

REM ---- 1) locate the base Python -------------------------------------
set "PY_EXE=%~1"
if "%PY_EXE%"=="" set "PY_EXE=C:\Python39\python.exe"

if not exist "%PY_EXE%" (
    echo.
    echo [FAIL] Python not found at: %PY_EXE%
    echo.
    echo    Fix one of these ways:
    echo      1^) install Python 3.9.13 x64 to C:\Python39  ^(guide STEP 2^), or
    echo      2^) re-run this script with the real path, e.g.
    echo         deploy\setup_venv.bat "D:\Python39\python.exe"
    echo.
    echo    To find an existing install, try:  where python
    popd
    exit /b 1
)

echo.
echo [1/5] Base Python
"%PY_EXE%" --version
if errorlevel 1 (
    echo [FAIL] cannot run %PY_EXE%
    popd
    exit /b 1
)

REM ---- 2) create the venv --------------------------------------------
echo.
echo [2/5] Creating virtual environment at %ROOT%\.venv
if exist "%ROOT%\.venv\Scripts\python.exe" (
    echo       .venv already exists - reusing it
) else (
    "%PY_EXE%" -m venv "%ROOT%\.venv"
    if errorlevel 1 (
        echo [FAIL] venv creation failed.
        echo        Check that you have write permission on %ROOT%
        popd
        exit /b 1
    )
    echo       created
)

set "VPY=%ROOT%\.venv\Scripts\python.exe"
if not exist "%VPY%" (
    echo [FAIL] %VPY% is missing after venv creation
    popd
    exit /b 1
)

REM ---- 3) upgrade pip -------------------------------------------------
echo.
echo [3/5] Upgrading pip / setuptools / wheel
"%VPY%" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 (
    echo [FAIL] pip upgrade failed - check the network / proxy settings
    popd
    exit /b 1
)

REM ---- 4) install requirements ---------------------------------------
echo.
echo [4/5] Installing packages from deploy\requirements-server.txt
echo       ** this downloads torch and can take 10-20 minutes **
"%VPY%" -m pip install -r "%ROOT%\deploy\requirements-server.txt"
if errorlevel 1 (
    echo.
    echo [FAIL] package installation failed.
    echo        It is safe to re-run this script - pip skips what is done.
    popd
    exit /b 1
)

REM ---- 5) readiness check --------------------------------------------
echo.
echo [5/5] Running the readiness check
echo.
"%VPY%" "%ROOT%\deploy\check_server.py"
set "RC=%ERRORLEVEL%"

echo.
echo ======================================================================
if "%RC%"=="0" (
    echo   DONE - no FAIL items. Next: guide STEP 6 onwards.
) else (
    echo   Packages are installed, but the readiness check found FAIL items
    echo   above ^(usually: database not set up yet, or no admin user^).
    echo   Work through them, then re-run:
    echo       .venv\Scripts\python.exe deploy\check_server.py
)
echo.
echo   The interpreter IIS must point to ^(web.config processPath^):
echo       %VPY%
echo ======================================================================
echo.

popd
exit /b %RC%
