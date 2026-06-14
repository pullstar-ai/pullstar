@echo off
REM PullStar.bat — Windows double-click launcher for the PullStar browser UI
REM Double-click this file to start PullStar. Your browser will open automatically.

cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
    echo Error: .venv not found — run scripts\install.sh first
    pause
    exit /b 1
)

if not exist "model_provider.json" (
    echo Error: model_provider.json not found — run scripts\install.sh first
    pause
    exit /b 1
)

echo Starting PullStar...
echo Your browser will open shortly at http://localhost:7860
echo Close this window to stop PullStar.
echo.

call .venv\Scripts\activate.bat
python app.py
pause
