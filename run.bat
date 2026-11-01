@echo off
rem Launch MARY. Extra arguments are passed through, e.g.:  run.bat --text --debug
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo MARY isn't set up yet. See README.md, "Setup".
    pause
    exit /b 1
)
rem The settings page's "Restart MARY" ends her with code 3: start her again.
set MARY_LAUNCHER=run.bat
:start
".venv\Scripts\python.exe" -m mary %*
if %errorlevel%==3 (
    set MARY_RESTARTED=1
    goto start
)
