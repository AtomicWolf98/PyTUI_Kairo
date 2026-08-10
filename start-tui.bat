@echo off
setlocal EnableExtensions

cd /d "%~dp0"

if not defined KAIRO_INSTALL_ROOT set "KAIRO_INSTALL_ROOT=%LOCALAPPDATA%\Kairo"
set "KAIRO_TUI=%KAIRO_INSTALL_ROOT%\bin\kairo-tui.bat"

if not exist "%KAIRO_TUI%" (
    echo [ERROR] Kairo is not installed. Run install.bat first.
    echo   Missing: "%KAIRO_TUI%"
    exit /b 1
)

call "%KAIRO_TUI%" %*
exit /b %ERRORLEVEL%
