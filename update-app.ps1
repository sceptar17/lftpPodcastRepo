$ErrorActionPreference = "Stop"

if (-not (Test-Path ".git")) {
    throw "Run this script from the lftp-knowledge folder."
}

git pull --ff-only
if ($LASTEXITCODE -ne 0) { throw "Git update failed. Local files were not overwritten." }

python -m pip install -e ".[dev,local-transcription]"
if ($LASTEXITCODE -ne 0) { throw "Dependency update failed." }

python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Tests failed. Review the output before restarting the app." }

Write-Host "Update complete. Start the app with:" -ForegroundColor Green
Write-Host "python -m uvicorn lftp_kb.web:app --host 127.0.0.1 --port 8080"
