$ErrorActionPreference = "Stop"
$ProjectRoot = $PSScriptRoot
Set-Location $ProjectRoot

if (-not (Test-Path ".git")) {
    throw "The launcher could not find the lftp-knowledge Git repository."
}

git pull --ff-only
if ($LASTEXITCODE -ne 0) { throw "Git update failed. Local files were not overwritten." }

$ProjectPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $ProjectPython)) {
    throw "The project virtual environment is missing. Expected: $ProjectPython"
}

& $ProjectPython -m pip install -e ".[dev,local-transcription]"
if ($LASTEXITCODE -ne 0) { throw "Dependency update failed." }

& $ProjectPython -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Tests failed. Review the output before restarting the app." }

Write-Host "Update and safety checks complete." -ForegroundColor Green
