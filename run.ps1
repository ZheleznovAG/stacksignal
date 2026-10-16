# Starts StackSignal. Dependencies are installed on the first run and again only when
# backend\requirements.txt or backend\constraints.txt change (or with -Update), always at the exact
# versions in constraints.txt -- so starting the app never changes the environment or needs PyPI.
param([switch]$Update)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

if (-not (Test-Path "$root\.venv")) {
    Write-Host "Creating virtual environment..."
    python -m venv "$root\.venv"
}

$venvPython = "$root\.venv\Scripts\python.exe"

$requirements = "$root\backend\requirements.txt"
$constraints = "$root\backend\constraints.txt"
$stamp = "$root\.venv\.deps-stamp"
$wanted = (Get-FileHash $requirements).Hash + (Get-FileHash $constraints).Hash
$installed = if (Test-Path $stamp) { (Get-Content $stamp -Raw).Trim() } else { "" }
if ($Update -or $installed -ne $wanted) {
    Write-Host "Installing dependencies (pinned by backend\constraints.txt)..."
    & $venvPython -m pip install --quiet -r $requirements -c $constraints
    if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
    Set-Content -Path $stamp -Value $wanted -Encoding ascii
}

if (-not (Test-Path "$root\.env")) {
    Copy-Item "$root\.env.example" "$root\.env"
    Write-Host "Created .env from .env.example. Add AI_API_KEY (from your ai-gateway) and GITHUB_TOKEN there for full functionality; both are optional."
}

Write-Host "Starting StackSignal on http://localhost:8000 ..."
# Bound to 127.0.0.1, not 0.0.0.0: this app has no authentication, so it must not be
# reachable from other devices on the network by default. See README's Security section
# before changing this.
& $venvPython -m uvicorn app.main:app --app-dir "$root\backend" --host 127.0.0.1 --port 8000
