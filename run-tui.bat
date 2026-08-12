@echo off
setlocal EnableExtensions

cd /d "%~dp0"

echo ==========================================
echo Starting Kairo TUI (Kernel + TUI)...
echo ==========================================

python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Python 3.11 or newer is required.
    exit /b 1
)
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if errorlevel 1 (
    echo [ERROR] Python 3.11 or newer is required.
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment in .venv...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        exit /b 1
    )
)

echo Installing/updating Kernel and TUI dependencies...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    echo [ERROR] Failed to upgrade pip.
    exit /b 1
)
".venv\Scripts\python.exe" -m pip install --editable "." --editable "frontends\tui"
if errorlevel 1 (
    echo [ERROR] Failed to install kairo-kernel and kairo-tui.
    exit /b 1
)

echo Verifying the installed TUI...
".venv\Scripts\python.exe" -m kairo_tui --headless-smoke
if errorlevel 1 (
    echo [ERROR] TUI smoke check failed.
    exit /b 1
)

echo Launching Kairo TUI...
".venv\Scripts\kairo-tui.exe" %*
exit /b %errorlevel%
