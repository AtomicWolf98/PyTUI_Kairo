@echo off
setlocal EnableExtensions
call "%~dp0run-tui.bat" %*
exit /b %errorlevel%
