@echo off
setlocal
cd /d "%~dp0"
title Airtable Migration Control Plane

rem Usage: start.bat            (port 8765)
rem        start.bat 8800       (another port)
if not "%~1"=="" set "PORT=%~1"
if not defined PORT set "PORT=8765"

rem --- find a working Python (py launcher first, then python, then python3)
set "PY="
for %%C in ("py -3" python python3) do (
  if not defined PY (
    %%~C --version >nul 2>&1 && set "PY=%%~C"
  )
)
if not defined PY (
  echo Python was not found. Install Python 3 or ask IT, then run this again.
  echo.
  pause
  exit /b 1
)

rem --- first run: create config.json from the example and let the user edit it
if not exist config.json (
  if not exist config.example.json (
    echo config.example.json is missing. Keep start.bat in the same folder as server.py.
    pause
    exit /b 1
  )
  copy config.example.json config.json >nul
  echo Created config.json. Paste your Airtable token into it, save, and close Notepad.
  notepad config.json
) else (
  findstr /c:"REPLACE" config.json >nul 2>&1 && (
    echo config.json still has the placeholder token. Paste your token, save, and close Notepad.
    notepad config.json
  )
)

echo Starting on http://127.0.0.1:%PORT%  (close this window or press Ctrl+C to stop)
rem open the browser after a short delay so the server is up first
start "" cmd /c "ping -n 3 127.0.0.1 >nul & start http://127.0.0.1:%PORT%"

%PY% server.py
echo.
echo The server has stopped. Scroll up for any error message.
pause
