@echo off
setlocal EnableExtensions

cd /d "%~dp0"

echo ==========================================
echo Kairo 0.3.3 installer for Windows
echo ==========================================
echo.

set "KAIRO_OWNER_ID=kairo-agent-managed-v1"
if not defined KAIRO_INSTALL_ROOT set "KAIRO_INSTALL_ROOT=%LOCALAPPDATA%\Kairo"
set "KAIRO_OWNER_FILE=%KAIRO_INSTALL_ROOT%\install-owner.ini"
set "KAIRO_VENV=%KAIRO_INSTALL_ROOT%\venv"
set "KAIRO_BIN=%KAIRO_INSTALL_ROOT%\bin"

if not exist "agent\web\static\index.html" (
    echo [ERROR] Packaged WebUI assets are missing.
    echo Run "npm --prefix web run build" before installing this checkout.
    goto :failure
)

set "PYTHON_CMD="
python --version >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=python"
if not defined PYTHON_CMD (
    py -3 --version >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=py -3"
)

if not defined PYTHON_CMD (
    echo [ERROR] Python 3.10 or newer was not found.
    goto :failure
)

%PYTHON_CMD% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo [ERROR] Python 3.10 or newer is required.
    goto :failure
)

echo [1/6] Installation root: "%KAIRO_INSTALL_ROOT%"
echo [2/6] Validating installation ownership...

if exist "%KAIRO_INSTALL_ROOT%" (
    if not exist "%KAIRO_OWNER_FILE%" (
        echo [ERROR] The target directory already exists but is not owned by Kairo:
        echo   "%KAIRO_INSTALL_ROOT%"
        echo Move it aside or choose a new KAIRO_INSTALL_ROOT. No files were changed.
        goto :failure
    )
    findstr /X /C:"installation_id=%KAIRO_OWNER_ID%" "%KAIRO_OWNER_FILE%" >nul
    if errorlevel 1 (
        echo [ERROR] The installation ownership manifest is invalid.
        echo No files were changed.
        goto :failure
    )
) else (
    mkdir "%KAIRO_INSTALL_ROOT%"
    if errorlevel 1 (
        echo [ERROR] Could not create the installation root.
        goto :failure
    )
    >"%KAIRO_OWNER_FILE%" echo schema=1
    >>"%KAIRO_OWNER_FILE%" echo product=kairo-agent
    >>"%KAIRO_OWNER_FILE%" echo installation_id=%KAIRO_OWNER_ID%
)

echo [3/6] Creating the managed virtual environment...
if not exist "%KAIRO_VENV%\Scripts\python.exe" (
    %PYTHON_CMD% -m venv "%KAIRO_VENV%"
    if errorlevel 1 (
        echo [ERROR] Failed to create the managed virtual environment.
        goto :failure
    )
) else (
    echo Managed virtual environment already exists.
)

echo [4/6] Installing Kairo into the managed environment...
"%KAIRO_VENV%\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    echo [ERROR] Failed to upgrade pip.
    goto :failure
)
"%KAIRO_VENV%\Scripts\python.exe" -m pip install --upgrade "%CD%"
if errorlevel 1 (
    echo [ERROR] Failed to install Kairo.
    goto :failure
)

echo [5/6] Creating the owned command shim...
if not exist "%KAIRO_BIN%" mkdir "%KAIRO_BIN%"
if errorlevel 1 (
    echo [ERROR] Failed to create the command directory.
    goto :failure
)
>"%KAIRO_BIN%\kairo.bat" echo @echo off
>>"%KAIRO_BIN%\kairo.bat" echo "%KAIRO_VENV%\Scripts\kairo.exe" %%*

echo Existing commands named "kairo" outside the managed directory are left untouched:
for /f "delims=" %%P in ('where kairo 2^>nul') do (
    if /I not "%%~fP"=="%KAIRO_BIN%\kairo.bat" echo   %%~fP
)

if /I "%KAIRO_SKIP_PATH%"=="1" (
    echo User PATH update skipped because KAIRO_SKIP_PATH=1.
) else (
    powershell -NoProfile -Command "$bin = [IO.Path]::GetFullPath($env:KAIRO_INSTALL_ROOT + '\bin'); $userPath = [Environment]::GetEnvironmentVariable('Path', 'User'); $parts = @($userPath -split ';' | Where-Object { $_ -and ([IO.Path]::GetFullPath($_) -ne $bin) }); [Environment]::SetEnvironmentVariable('Path', ((@($bin) + $parts) -join ';'), 'User')"
    if errorlevel 1 (
        echo [WARNING] Failed to update the user PATH. Add this directory manually:
        echo   "%KAIRO_BIN%"
    )
)

echo [6/6] Verifying the installed command...
"%KAIRO_BIN%\kairo.bat" --help | findstr /C:"--web" >nul
if errorlevel 1 (
    echo [ERROR] The installed command did not report WebUI support.
    goto :failure
)
"%KAIRO_VENV%\Scripts\python.exe" -c "from importlib.metadata import version; raise SystemExit(0 if version('kairo-agent') == '0.3.3' else 1)"
if errorlevel 1 (
    echo [ERROR] The installed package version is not 0.3.3.
    goto :failure
)

echo.
echo Installed and verified Kairo 0.3.3.
echo Open a new terminal and run: kairo
exit /b 0

:failure
echo.
echo Kairo installation failed.
if /I not "%KAIRO_NONINTERACTIVE%"=="1" pause
exit /b 1
