@echo off
setlocal
title Live From The Path Knowledge Repository
cd /d "%~dp0"

echo Updating and checking LFTP Knowledge...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0update-app.ps1"
if errorlevel 1 goto :failed

if not exist "%~dp0.venv\Scripts\python.exe" (
  echo The project virtual environment is missing.
  echo Expected: %~dp0.venv\Scripts\python.exe
  goto :failed
)

set "LFTP_REPOSITORY_ROOT=%~dp0"
echo.
echo Starting LFTP Knowledge at http://127.0.0.1:8080
echo Leave this window open. Press Ctrl+C here when you want to stop the app.
start "" powershell.exe -NoProfile -WindowStyle Hidden -Command "Start-Sleep -Seconds 2; Start-Process 'http://127.0.0.1:8080'"
"%~dp0.venv\Scripts\python.exe" -m uvicorn lftp_kb.web:app --host 127.0.0.1 --port 8080
goto :end

:failed
echo.
echo LFTP Knowledge could not start. The error above explains what needs attention.
pause

:end
endlocal
