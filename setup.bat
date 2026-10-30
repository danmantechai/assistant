@echo off
rem One-time setup for MARY: Python environment, packages, voice models, Ollama and the language model.
rem Safe to run again: it skips whatever is already done.
setlocal
cd /d "%~dp0"

echo [1/4] Python environment
if not exist ".venv\Scripts\python.exe" (
    py -3.12 -m venv .venv
    if errorlevel 1 (
        echo   Couldn't create it. Install Python 3.12 from https://www.python.org/downloads/ and run this again.
        goto :fail
    )
)
".venv\Scripts\python.exe" -m pip install --quiet --upgrade pip
".venv\Scripts\python.exe" -m pip install --quiet -r requirements.txt
if errorlevel 1 (
    echo   Installing the packages failed; see the messages above.
    goto :fail
)
echo   ok

echo [2/4] Voice models (about 1 GB the first time)
".venv\Scripts\python.exe" -m mary.audio.download
if errorlevel 1 goto :fail

echo [3/4] Ollama, the local language-model server
if not exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" (
    where ollama >nul 2>nul
    if errorlevel 1 (
        winget install --id Ollama.Ollama -e --accept-source-agreements
        if errorlevel 1 (
            echo   Couldn't install Ollama. Get it from https://ollama.com/download and run this again.
            goto :fail
        )
    )
)
echo   ok

echo [4/4] The language model (about 2 GB the first time)
".venv\Scripts\python.exe" -m mary.llm.server
if errorlevel 1 goto :fail

echo.
echo Done. Start MARY with run.bat (voice; press Ctrl+Space and speak), or "run.bat --text" to type.
echo To have her start whenever you sign in:  .venv\Scripts\python.exe -m mary.autostart enable
pause
exit /b 0

:fail
echo.
echo Setup stopped. Fix the problem above and run setup.bat again; finished steps are skipped.
pause
exit /b 1
