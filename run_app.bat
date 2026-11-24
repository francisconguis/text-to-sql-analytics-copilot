@echo off
setlocal
cd /d "%~dp0"

echo ==================================================
echo   Text-to-SQL Business Analytics Copilot
echo ==================================================

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo ERROR: Failed to create virtual environment. Make sure Python is installed and on PATH.
        pause
        exit /b 1
    )
)

if not exist ".venv\Scripts\streamlit.exe" (
    echo [2/3] Installing dependencies from requirements.txt...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo ERROR: Failed to install dependencies.
        pause
        exit /b 1
    )
)

echo [3/3] Starting Streamlit app...
".venv\Scripts\streamlit.exe" run app.py

if errorlevel 1 (
    echo ERROR: Streamlit exited with an error.
    pause
)
endlocal
