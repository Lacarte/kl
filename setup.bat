@echo off
title Keylogger Setup
cd /d "%~dp0"

echo ============================================
echo   Keylogger - Environment Setup
echo ============================================
echo.

:: Create virtual environment
if not exist "venv" (
    echo [1/3] Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo ERROR: Failed to create venv. Make sure Python is installed and in PATH.
        pause
        exit /b 1
    )
    echo       Done.
) else (
    echo [1/3] Virtual environment already exists. Skipping.
)

:: Activate and install dependencies
echo [2/3] Installing dependencies...
call venv\Scripts\activate.bat
pip install -r requirements.txt --quiet
if errorlevel 1 (
    echo ERROR: Failed to install dependencies.
    pause
    exit /b 1
)
echo       Done.

:: Create logs directory
echo [3/3] Creating logs directory...
if not exist "logs" mkdir logs
echo       Done.

echo.
echo ============================================
echo   Setup complete!
echo.
echo   runner.bat  - Start the keylogger
echo   watch.bat   - Browse logs by date
echo ============================================
echo.
pause
