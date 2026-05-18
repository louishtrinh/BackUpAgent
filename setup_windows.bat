@echo off
setlocal EnableDelayedExpansion

echo =====================================================
echo  Flying Probe Backup Agent - Windows Setup
echo =====================================================
echo.

REM ── 1. Check Python ──────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not on PATH.
    echo        Download from https://www.python.org/downloads/
    echo        Make sure to tick "Add Python to PATH" during install.
    pause
    exit /b 1
)
echo [OK] Python found.

REM ── 2. Check Git ─────────────────────────────────────
git --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Git is not installed or not on PATH.
    echo        Download from https://git-scm.com/download/win
    echo        Use default options during install.
    pause
    exit /b 1
)
echo [OK] Git found.

REM ── 3. Install Python dependencies ───────────────────
echo.
echo Installing Python dependencies...
pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo [ERROR] pip install failed.
    pause
    exit /b 1
)
echo [OK] Dependencies installed.

REM ── 4. Remind user to edit config.json ───────────────
echo.
echo ---------------------------------------------------------
echo  IMPORTANT: Edit config.json before running the agent!
echo.
echo  Things to set:
echo    repo_path          - where git will store the backup
echo                         (e.g. C:\FlyingProbeBackup)
echo    watch_paths        - folder(s) containing your programs
echo                         (e.g. C:\FlyingProbePrograms)
echo    github_remote_url  - your GitHub repo URL
echo    file_extensions    - file types to track
echo ---------------------------------------------------------
echo.

REM ── 5. Offer to add to Windows startup ───────────────
set /p STARTUP="Add agent to Windows startup so it runs automatically? (Y/N): "
if /i "!STARTUP!"=="Y" (
    set SCRIPT_DIR=%~dp0
    set STARTUP_CMD=pythonw "!SCRIPT_DIR!backup_agent.py"
    reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" ^
        /v "FlyingProbeBackupAgent" ^
        /t REG_SZ ^
        /d "!STARTUP_CMD!" ^
        /f >nul
    echo [OK] Added to startup registry. Agent will start on next login.
    echo      To remove: run remove_startup.bat
) else (
    echo [SKIP] Startup registration skipped.
    echo        To start manually, run: start_agent.bat
)

REM ── 6. Create helper scripts ──────────────────────────
echo.
echo Creating helper scripts...

REM start_agent.bat
(
    echo @echo off
    echo cd /d "%~dp0"
    echo echo Starting Flying Probe Backup Agent...
    echo python backup_agent.py
    echo pause
) > "%~dp0start_agent.bat"

REM start_agent_hidden.bat  (runs in background with no window)
(
    echo @echo off
    echo cd /d "%~dp0"
    echo start "" /min pythonw backup_agent.py
    echo echo Agent started in background.
) > "%~dp0start_agent_hidden.bat"

REM remove_startup.bat
(
    echo @echo off
    echo reg delete "HKCU\Software\Microsoft\Windows\CurrentVersion\Run" /v "FlyingProbeBackupAgent" /f
    echo echo Removed from startup.
    echo pause
) > "%~dp0remove_startup.bat"

echo [OK] Created: start_agent.bat, start_agent_hidden.bat, remove_startup.bat
echo.
echo Setup complete!
echo.
echo Next steps:
echo   1. Edit config.json with your folder paths and GitHub URL
echo   2. On GitHub: create a new PRIVATE repo and copy its URL into config.json
echo   3. Run start_agent.bat to test, then close and it will auto-start next login
echo.
pause
