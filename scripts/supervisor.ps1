<#
  Keeps StackSignal running without launching or contacting AI Gateway.
  supervisor.ps1 -Once performs a single check; otherwise checks every five minutes.
#>
param(
    [switch]$Once,
    [int]$IntervalSeconds = 300
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$stateDir = Join-Path $root "data"
$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    $stateDir = & $venvPython -c "import sys; sys.path.insert(0, sys.argv[1]); from app.config import DATA_DIR; print(DATA_DIR)" (Join-Path $root "backend")
    if ($LASTEXITCODE -ne 0) { throw "Could not resolve StackSignal's data directory" }
    $stateDir = "$stateDir".Trim()
}
$logDir = Join-Path $stateDir "logs"
$logFile = Join-Path $logDir "supervisor.log"
$appPort = 8000
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-Log([string]$message) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $message
    Add-Content -Path $logFile -Value $line
    if (-not $env:SUPERVISOR_QUIET) { Write-Host $line }
    if ((Get-Item $logFile).Length -gt 1MB) { Move-Item -Force $logFile "$logFile.old" }
}

function Test-Port([int]$port) {
    return [bool](Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue)
}

function Start-App {
    $python = Join-Path $root ".venv\Scripts\python.exe"
    if (-not (Test-Path $python)) { Write-Log "no .venv found - run run.ps1 once first"; return }
    Start-Process -FilePath $python -ArgumentList "-m", "uvicorn", "app.main:app", "--app-dir", "backend", "--host", "127.0.0.1", "--port", "$appPort" `
        -WorkingDirectory $root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir "app.out.log") -RedirectStandardError (Join-Path $logDir "app.err.log")
    Start-Sleep -Seconds 8
    if (Test-Port $appPort) { Write-Log "StackSignal started" } else { Write-Log "StackSignal did not come up" }
}

do {
    try {
        if (-not (Test-Port $appPort)) { Write-Log "StackSignal is down"; Start-App }
    } catch {
        Write-Log ("check failed: " + $_.Exception.Message)
    }
    if (-not $Once) { Start-Sleep -Seconds $IntervalSeconds }
} while (-not $Once)
