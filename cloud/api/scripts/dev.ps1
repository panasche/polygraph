# Start the Polygraph Cloud API on http://127.0.0.1:8000 with development settings:
# SQLite at cloud/api/.data/dev.db, local file storage, inline queue, dev-login on,
# CORS for the Next.js app on http://localhost:3000.
#
#   .\scripts\dev.ps1            # start
#   .\scripts\dev.ps1 --reload   # restart on code changes
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Error "No virtualenv found. Create one first: python -m venv .venv; .venv\Scripts\pip install -e ..\..\packages\pytest-polygraph --no-deps; .venv\Scripts\pip install -e .[test]"
    exit 1
}

New-Item -ItemType Directory -Force (Join-Path $root ".data") | Out-Null

$env:ENVIRONMENT = "development"
if (-not $env:PUBLIC_BASE_URL) { $env:PUBLIC_BASE_URL = "http://localhost:3000" }
$env:DATABASE_URL = "sqlite:///./.data/dev.db"
$env:STORAGE_BACKEND = "local"
$env:LOCAL_STORAGE_DIR = "./.data"
$env:QUEUE_BACKEND = "inline"
if (-not $env:PII_SCANNER) { $env:PII_SCANNER = "regex" }
if (-not $env:LEADERBOARD_PROJECT) { $env:LEADERBOARD_PROJECT = "polygraph-dev/benchmark" }
if (-not $env:LOG_LEVEL) { $env:LOG_LEVEL = "INFO" }

Write-Host "Polygraph Cloud API (development) on http://127.0.0.1:8000  docs: http://127.0.0.1:8000/api/docs"
& $py -m uvicorn polygraph_cloud.app:create_app --factory --host 127.0.0.1 --port 8000 --no-access-log @args
