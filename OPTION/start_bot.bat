@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo   Tisa iPhone Telegram Bot - LOCAL
echo ========================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [1/2] Creating Python virtual environment...
    py -3 -m venv .venv
    if errorlevel 1 (
        echo.
        echo ERROR: Could not create the virtual environment.
        echo Make sure Python 3.10+ is installed and the "py" launcher is available.
        pause
        exit /b 1
    )
    echo.
    echo [2/2] Installing dependencies for the first run...
    call ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo ERROR: Dependency installation failed.
        echo Check your internet connection and run this file again.
        pause
        exit /b 1
    )
) else (
    echo Virtual environment already exists.
)

echo.
echo Starting bot... Press Ctrl+C to stop it.
echo.
call ".venv\Scripts\python.exe" bot.py

if errorlevel 1 (
    echo.
    echo Bot stopped with an error. See the log above.
    pause
)
endlocal
