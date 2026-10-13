@echo off
rem Map Website - double-click to run the site on this computer.
rem Starts the server, opens the browser, and stops when you close this window.
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo Python was not found. Install it from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during setup, then try again.
  pause
  exit /b 1
)

rem Open the browser two seconds from now, once the server is up.
start "" /min powershell -NoProfile -WindowStyle Hidden -Command "Start-Sleep 2; Start-Process 'http://localhost:8000/'"

python server\app.py

echo.
echo The server has stopped. If it said the port is already in use,
echo the site is already running in another window.
pause
