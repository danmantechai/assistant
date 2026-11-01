@echo off
rem MARY's settings page, in your browser (http://127.0.0.1:8765). While MARY runs, you can also open it from
rem her tray icon (Settings...). Close this window when you're done.
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo MARY isn't set up yet. Run setup.bat first.
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m mary.settings %*
