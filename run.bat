@echo off
setlocal enabledelayedexpansion

echo ==========================================
echo Starting Kairo Setup ^& Launch...
echo ==========================================

:: Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Please install Python 3.10+ and try again.
    pause
    exit /b 1
)

:: Create virtual environment if it doesn't exist
if not exist .venv (
    echo Creating virtual environment in .venv...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

:: Activate virtual environment
call .venv\Scripts\activate
if errorlevel 1 (
    echo [ERROR] Failed to activate virtual environment.
    pause
    exit /b 1
)

:: Install / Update dependencies from pyproject.toml
echo Installing/updating dependencies from pyproject.toml...
python -m pip install --upgrade pip >nul
pip install -e .
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies. Please check pyproject.toml and your network.
    pause
    exit /b 1
)

:: Check configuration file
if not exist config.json (
    echo [WARNING] config.json not found! Creating it from config.example.json...
    copy /Y config.example.json config.json >nul
    echo Please edit config.json to set your API key and settings.
)

:: Create skills directory if it doesn't exist
if not exist skills (
    mkdir skills
)

echo.
echo Launching Kairo...
echo.

python kairo.py %*

pause
