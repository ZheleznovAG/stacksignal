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
$logDir = Join-Path $root "data\logs"
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
