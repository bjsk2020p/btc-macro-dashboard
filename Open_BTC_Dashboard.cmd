@echo off
rem ------------------------------------------------------------------
rem  BTC Macro Dashboard - one-click launcher.
rem  Double-click this file. It reuses or starts the local data server
rem  (hidden, bound to 127.0.0.1) and opens the dashboard in your
rem  default browser. Works from any folder, including paths with spaces.
rem ------------------------------------------------------------------
setlocal EnableExtensions
title BTC Macro Dashboard
set "APP_DIR=%~dp0"
set "PY_CMD="

rem Prefer the Python launcher, then python on PATH. "--version" also rejects
rem the Microsoft Store placeholder, which exits with an error.
py -3 --version >nul 2>&1 && set "PY_CMD=py -3"
if not defined PY_CMD python --version >nul 2>&1 && set "PY_CMD=python"
if not defined PY_CMD goto :nopython

echo Starting BTC Macro Dashboard...
%PY_CMD% "%APP_DIR%serve.py" --launch %*
if errorlevel 1 goto :failed
exit /b 0

:nopython
echo.
echo  Python 3.9 or newer is required but was not found.
echo  Install it from https://www.python.org/downloads/windows/
echo  (tick "Add python.exe to PATH" during setup), then double-click
echo  Open_BTC_Dashboard.cmd again.
echo.
pause
exit /b 1

:failed
echo.
echo  The dashboard could not be started. See the message above and the
echo  log file: "%APP_DIR%.runtime\serve.log"
echo.
pause
exit /b 1
