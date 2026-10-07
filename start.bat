@echo off
setlocal
cd /d "%~dp0"
title Binance Order Book Scalper

set "PY=python"
where py >nul 2>nul
if %errorlevel%==0 set "PY=py -3"

if not exist ".venv\Scripts\python.exe" (
  echo First run: setting up a private Python environment in .venv ...
  %PY% -m venv .venv
  if errorlevel 1 goto nopython
)

echo Checking packages ...
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 goto piperror

if not exist ".env" (
  copy ".env.example" ".env" >nul
  echo Created .env from .env.example - the bot starts in PAPER mode.
)

".venv\Scripts\python.exe" run.py
echo.
echo The bot has stopped.
pause
exit /b 0

:nopython
echo.
echo Python 3.11 or newer is needed. Install it from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" in the installer, then run start.bat again.
pause
exit /b 1

:piperror
echo.
echo Installing the Python packages failed. Check your internet connection and try again.
pause
exit /b 1
