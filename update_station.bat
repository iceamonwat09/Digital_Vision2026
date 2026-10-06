@echo off
REM ============================================================
REM  update_station.bat - pull the latest code onto this station
REM  Place this file in the repo folder (next to app.py).
REM  Safe: never stashes, resets or deletes your files.
REM ============================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"

set "BRANCH=claude/laughing-fermat-i9aise"

echo.
echo ==== Update station : %BRANCH% ====
echo Folder: %CD%
echo.
echo Close the running app.py window first (Ctrl+C) - Flask reads code only at start.
echo.

where git >nul 2>&1
if errorlevel 1 (
    echo [ERROR] git not found. Install Git for Windows or add it to PATH.
    goto fail
)

git rev-parse --is-inside-work-tree >nul 2>&1
if errorlevel 1 (
    echo [ERROR] This folder is not a git repository: %CD%
    echo         Put this .bat next to app.py inside the repo.
    goto fail
)

REM ---- 1) stop if tracked files were edited on this machine ----
set "DIRTY=0"
for /f %%i in ('git status --porcelain --untracked-files=no ^| find /c /v ""') do set "DIRTY=%%i"
if not "!DIRTY!"=="0" (
    echo [STOP] !DIRTY! tracked file^(s^) were changed on this machine:
    git status --short --untracked-files=no
    echo.
    echo Nothing was touched. Decide yourself, for example:
    echo   keep them  : git stash      ^(restore later: git stash pop^)
    echo   drop them  : git checkout -- ^<file^>
    goto fail
)

for /f %%i in ('git rev-parse HEAD') do set "OLD=%%i"
for /f %%b in ('git rev-parse --abbrev-ref HEAD') do set "CUR=%%b"
echo Current branch : !CUR!
echo Current commit : !OLD:~0,7!
echo.

REM ---- 2) fetch with retry (2s, 4s, 8s, 16s) ----
set "TRY=0"
set "WAIT=2"
:fetch
git fetch origin %BRANCH%
if not errorlevel 1 goto fetched
set /a TRY+=1
if !TRY! geq 5 (
    echo [ERROR] git fetch failed 5 times - check network / VPN / proxy.
    goto fail
)
echo Fetch failed, retry !TRY!/4 in !WAIT!s ...
timeout /t !WAIT! /nobreak >nul
set /a WAIT*=2
goto fetch
:fetched

REM ---- 3) switch to the branch if needed ----
if /i not "!CUR!"=="%BRANCH%" (
    git show-ref --verify --quiet refs/heads/%BRANCH%
    if errorlevel 1 (
        git checkout -b %BRANCH% --track origin/%BRANCH%
    ) else (
        git checkout %BRANCH%
    )
    if errorlevel 1 (
        echo [ERROR] Could not switch to %BRANCH%.
        goto fail
    )
)

REM ---- 4) fast-forward only (never creates merge commits) ----
git pull --ff-only origin %BRANCH%
if errorlevel 1 (
    echo.
    echo [ERROR] Pull refused: this machine has local commits that are not on GitHub.
    echo         Nothing was changed. Send this output to the developer.
    goto fail
)

for /f %%i in ('git rev-parse HEAD') do set "NEW=%%i"
echo.
if "!OLD!"=="!NEW!" (
    echo Already up to date ^(!NEW:~0,7!^).
) else (
    echo ==== New commits ====
    git log --oneline !OLD!..!NEW!
    echo.
    git diff --name-only !OLD! !NEW! | findstr /i /c:"requirements.txt" >nul
    if not errorlevel 1 (
        echo [ACTION] requirements.txt changed - run:
        echo          py -3.9 -m pip install -r requirements.txt
        echo.
    )
    git diff --name-only !OLD! !NEW! | findstr /i /r "n8n_.*\.json" >nul
    if not errorlevel 1 (
        echo [ACTION] An N8N workflow file changed - re-import it in N8N:
        git diff --name-only !OLD! !NEW! | findstr /i /r "n8n_.*\.json"
        echo.
    )
)

echo ==== Version that must appear in the web footer ====
findstr /b /c:"CONFIG_VERSION" config.py
echo.

choice /c YN /m "Start app now (py -3.9 app.py)"
if errorlevel 2 goto done
py -3.9 app.py
goto done

:fail
echo.
echo ==== Update NOT completed ====
pause
exit /b 1

:done
echo.
pause
exit /b 0
