$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $python -c "import mrfinancer.api, playwright, openpyxl, pypdf, socksio" 2>$null
if ($LASTEXITCODE -ne 0) {
    & $python -m pip install -e .
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $PSScriptRoot '.loan-browser'
& $python -c "from pathlib import Path; from playwright.sync_api import sync_playwright; pw = sync_playwright().start(); present = Path(pw.chromium.executable_path).is_file(); pw.stop(); raise SystemExit(0 if present else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Installing the headless browser for live sources.'
    & $python -m playwright install chromium
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'Chromium setup failed. Direct source lists can still run. Retry browser setup from the README.'
    }
}
Write-Host 'Open http://127.0.0.1:8765 in your browser.'
& $python -m uvicorn mrfinancer.api:app --host 127.0.0.1 --port 8765
exit $LASTEXITCODE

