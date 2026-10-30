@echo off
rem Launch MARY. Extra arguments are passed through, e.g.:  run.bat --text --debug
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo MARY isn't set up yet. See README.md, "Setup".
    pause
    exit /b 1
)
".venv\Scripts\python.exe" -m mary %*
