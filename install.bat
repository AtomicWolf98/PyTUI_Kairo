@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

echo ==========================================
echo Kairo one-click installer for Windows
echo ==========================================
echo.

set "PYTHON_CMD="
python --version >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=python"
if not defined PYTHON_CMD (
    py -3 --version >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=py -3"
)

if not defined PYTHON_CMD (
    echo [ERROR] Python 3.10+ was not found.
    echo Install Python from https://www.python.org/downloads/ and enable "Add python.exe to PATH".
    pause
    exit /b 1
)

echo [1/5] Using Python:
%PYTHON_CMD% --version

echo.
echo [2/5] Creating virtual environment...
if not exist ".venv" (
    %PYTHON_CMD% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create .venv.
        pause
        exit /b 1
    )
) else (
    echo .venv already exists.
)

echo.
echo [3/5] Installing Kairo and dependencies...
call ".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    echo [ERROR] Failed to upgrade pip.
    pause
    exit /b 1
)

call ".venv\Scripts\python.exe" -m pip install -e .
if errorlevel 1 (
    echo [ERROR] Failed to install Kairo.
    pause
    exit /b 1
)

if not exist "config.json" (
    echo.
    echo [4/5] Creating config.json from config.example.json...
    copy /Y "config.example.json" "config.json" >nul
) else (
    echo.
    echo [4/5] config.json already exists.
)

if not exist "skills" mkdir "skills"

echo.
echo [5/5] Creating user command shim...
set "KAIRO_BIN=%LOCALAPPDATA%\Kairo\bin"
if not exist "%KAIRO_BIN%" mkdir "%KAIRO_BIN%"

(
    echo @echo off
    echo "%CD%\.venv\Scripts\kairo.exe" %%*
) > "%KAIRO_BIN%\kairo.bat"

echo Created: "%KAIRO_BIN%\kairo.bat"

echo.
echo Updating user PATH...
set "PATH_CHECK=;%PATH%;"
echo !PATH_CHECK! | find /I ";%KAIRO_BIN%;" >nul
if errorlevel 1 (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$bin = $env:LOCALAPPDATA + '\Kairo\bin'; $userPath = [Environment]::GetEnvironmentVariable('Path', 'User'); $parts = @(); if ($userPath) { $parts = $userPath -split ';' | Where-Object { $_ } }; if ($parts -notcontains $bin) { [Environment]::SetEnvironmentVariable('Path', (($parts + $bin) -join ';'), 'User') }"
    if errorlevel 1 (
        echo [WARNING] Failed to update user PATH automatically.
        echo Add this directory to your user PATH manually:
        echo   %KAIRO_BIN%
    ) else (
        echo User PATH updated.
    )
    set "PATH=%PATH%;%KAIRO_BIN%"
) else (
    echo Current PATH already contains Kairo bin.
)

echo.
echo ==========================================
echo Kairo installation completed.
echo ==========================================
echo.
echo Open a NEW PowerShell window and run:
echo   kairo
echo.
echo Optional WebUI:
echo   kairo --web
echo.
echo If the current terminal cannot find kairo yet, run:
echo   "%KAIRO_BIN%\kairo.bat"
echo.
pause
