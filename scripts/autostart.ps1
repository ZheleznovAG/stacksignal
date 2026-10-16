<#
  Starts StackSignal when you log in to Windows and keeps them healthy.

    autostart.ps1 -Install    register a per-user scheduled task (no administrator rights needed)
    autostart.ps1 -Remove     remove it
    autostart.ps1 -Status     show whether it is registered and what the supervisor last logged

  The task runs scripts\supervisor.ps1 hidden at logon. Your computer still has to be on for the
  daily 08:00 collection; if it was asleep, the run happens when it wakes (within 6 hours).
#>
param(
    [switch]$Install,
    [switch]$Remove,
    [switch]$Status
)

$ErrorActionPreference = "Stop"
$taskName = "StackSignal supervisor"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$script = Join-Path $root "scripts\start-hidden.vbs"

if ($Install) {
    $action = New-ScheduledTaskAction -Execute "wscript.exe" `
        -Argument "//B //Nologo `"$script`"" -WorkingDirectory $root
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
        -Description "Keeps StackSignal running (see scripts\autostart.ps1)" -Force | Out-Null
    Write-Host "Registered '$taskName': it starts at your next Windows logon. To start it now: Start-ScheduledTask -TaskName '$taskName'"
} elseif ($Remove) {
    if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "Removed '$taskName'. Running processes are left alone."
    } else {
        Write-Host "'$taskName' is not registered."
    }
} else {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) { Write-Host "Registered: $taskName ($($task.State))" } else { Write-Host "Not registered. Run: scripts\autostart.ps1 -Install" }
    $log = Join-Path $root "data\logs\supervisor.log"
    if (Test-Path $log) { Write-Host "Last supervisor log lines:"; Get-Content $log -Tail 8 }
}
