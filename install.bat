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

echo [1/6] Using Python:
%PYTHON_CMD% --version

echo.
echo [2/6] Removing old Kairo installs and stale commands...
set "KAIRO_BIN=%LOCALAPPDATA%\Kairo\bin"

if exist "%KAIRO_BIN%\kairo.bat" del /F /Q "%KAIRO_BIN%\kairo.bat" >nul 2>&1
if exist "%KAIRO_BIN%\kairo.cmd" del /F /Q "%KAIRO_BIN%\kairo.cmd" >nul 2>&1
if exist "%KAIRO_BIN%\kairo.exe" del /F /Q "%KAIRO_BIN%\kairo.exe" >nul 2>&1

%PYTHON_CMD% -m pip uninstall -y kairo-agent kairo pyTUI >nul 2>&1
python -m pip uninstall -y kairo-agent kairo pyTUI >nul 2>&1
py -3 -m pip uninstall -y kairo-agent kairo pyTUI >nul 2>&1
if exist ".venv\Scripts\python.exe" (
    call ".venv\Scripts\python.exe" -m pip uninstall -y kairo-agent kairo pyTUI >nul 2>&1
)

for /f "delims=" %%P in ('where kairo 2^>nul') do (
    set "FOUND_KAIRO=%%~fP"
    if /I not "!FOUND_KAIRO!"=="%CD%\kairo" if /I not "!FOUND_KAIRO!"=="%CD%\kairo.py" (
        echo !FOUND_KAIRO! | find /I "\Scripts\kairo" >nul
        if not errorlevel 1 (
            echo Removing stale command: !FOUND_KAIRO!
            del /F /Q "!FOUND_KAIRO!" >nul 2>&1
        )
    )
)

echo Old Kairo cleanup completed.

echo.
echo [3/6] Creating virtual environment...
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
echo [4/6] Installing Kairo and dependencies...
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
    echo [5/6] Creating config.json from config.example.json...
    copy /Y "config.example.json" "config.json" >nul
) else (
    echo.
    echo [5/6] config.json already exists.
)

if not exist "skills" mkdir "skills"

echo.
echo [6/6] Creating user command shim...
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
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$bin = $env:LOCALAPPDATA + '\Kairo\bin'; $userPath = [Environment]::GetEnvironmentVariable('Path', 'User'); $parts = @(); if ($userPath) { $parts = $userPath -split ';' | Where-Object { $_ -and ($_ -ne $bin) -and ($_ -ne 'System.Object[]') } }; $newParts = @($bin) + @($parts); [Environment]::SetEnvironmentVariable('Path', ($newParts -join ';'), 'User')"
    if errorlevel 1 (
        echo [WARNING] Failed to update user PATH automatically.
        echo Add this directory to your user PATH manually:
        echo   %KAIRO_BIN%
    ) else (
        echo User PATH updated.
    )
    set "PATH=%KAIRO_BIN%;%PATH%"
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$bin = $env:LOCALAPPDATA + '\Kairo\bin'; $userPath = [Environment]::GetEnvironmentVariable('Path', 'User'); $parts = @(); if ($userPath) { $parts = $userPath -split ';' | Where-Object { $_ -and ($_ -ne $bin) -and ($_ -ne 'System.Object[]') } }; $newParts = @($bin) + @($parts); [Environment]::SetEnvironmentVariable('Path', ($newParts -join ';'), 'User')"
    echo Current PATH contains Kairo bin; user PATH was refreshed with Kairo first.
)

echo.
echo Verifying installed command...
"%KAIRO_BIN%\kairo.bat" --help | findstr /C:"--web" >nul
if errorlevel 1 (
    echo [WARNING] Kairo installed, but the shim did not report --web.
    echo Try running:
    echo   "%KAIRO_BIN%\kairo.bat" --web
) else (
    echo Verified: installed kairo command supports --web.
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
