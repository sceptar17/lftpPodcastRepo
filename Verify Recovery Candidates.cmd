@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Python environment not found at .venv\Scripts\python.exe
  echo Open Start LFTP Knowledge.cmd once to finish setup, then run this again.
  pause
  exit /b 1
)

echo Preparing the focused recovery verifier...
".venv\Scripts\python.exe" -m pip install --quiet "yt-dlp>=2025.8"
if errorlevel 1 goto :failed

echo.
echo Comparing only the unresolved recovery candidates. No master audio will be changed.
".venv\Scripts\python.exe" scripts\verify_recovery_candidates.py
if errorlevel 1 goto :failed

echo.
start "" "catalog\finalization\recovery-verification\RECOVERY_VERIFICATION.md"
pause
exit /b 0

:failed
echo.
echo Verification did not complete. Read the message above; completed evidence remains saved.
pause
exit /b 1
