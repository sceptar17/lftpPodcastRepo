@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo Python environment not found at .venv\Scripts\python.exe
  echo Open Start LFTP Knowledge.cmd once to finish setup, then run this again.
  pause
  exit /b 1
)

echo Preparing the public-media inventory tool...
".venv\Scripts\python.exe" -m pip install --quiet "yt-dlp>=2025.8"
if errorlevel 1 goto :failed

echo.
echo Researching Wayback, Arquivo.pt, YouTube, and Vimeo...
".venv\Scripts\python.exe" scripts\research_legacy_sources.py
if errorlevel 1 goto :failed

echo.
echo Finished. Opening the candidate ledger.
start "" "catalog\finalization\research\media-candidates.csv"
echo Detailed results are in catalog\finalization\research\research-result.json
pause
exit /b 0

:failed
echo.
echo The research pass did not complete. Its successful partial results and errors,
echo if any, are under catalog\finalization\research.
pause
exit /b 1
