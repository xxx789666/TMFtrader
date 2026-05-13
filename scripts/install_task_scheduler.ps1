# One-shot installer for the TMF weekly review Task Scheduler entry.
# Runs as the current user (no admin needed for Limited RunLevel).
#
# Usage (in any PowerShell, admin not required):
#   powershell.exe -ExecutionPolicy Bypass -File "<this-path>"
#
# Uninstall:
#   Unregister-ScheduledTask -TaskName "TMF_Weekly_Review" -Confirm:$false

$ErrorActionPreference = "Stop"

$TaskName    = "TMF_Weekly_Review"
$Script      = Join-Path $PSScriptRoot "scheduled_trigger.ps1"
$Description = "TMF weekly review: WSL2 + Hermes + NIM -> Telegram"

if (-not (Test-Path -LiteralPath $Script)) {
  Write-Error ("scheduled_trigger.ps1 not found at: " + $Script)
  exit 1
}

# Trigger: every Saturday 09:00 local time
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At 9:00am

# Action: PowerShell -> scheduled_trigger.ps1
$action = New-ScheduledTaskAction `
  -Execute "powershell.exe" `
  -Argument ("-NoProfile -ExecutionPolicy Bypass -File `"" + $Script + "`"")

# Settings: missed-run catchup, retry-on-failure, wake-to-run, 30-min cap
$settings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable `
  -RestartInterval (New-TimeSpan -Minutes 10) `
  -RestartCount 3 `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
  -WakeToRun:$true `
  -DontStopIfGoingOnBatteries

# Principal: current user, Limited (no UAC prompt)
$principal = New-ScheduledTaskPrincipal `
  -UserId ($env:USERDOMAIN + "\" + $env:USERNAME) `
  -LogonType Interactive `
  -RunLevel Limited

# Idempotent: remove existing first
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
  Write-Host "Existing task found, removing..."
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
  -TaskName $TaskName `
  -Description $Description `
  -Trigger $trigger `
  -Action $action `
  -Settings $settings `
  -Principal $principal | Out-Null

$info = Get-ScheduledTaskInfo -TaskName $TaskName

Write-Host ""
Write-Host ("[OK] Task '" + $TaskName + "' registered.")
Write-Host ("     NextRunTime: " + $info.NextRunTime)
Write-Host ""
Write-Host "Test immediately (does not wait until Saturday):"
Write-Host ("   Start-ScheduledTask -TaskName '" + $TaskName + "'")
Write-Host ""
Write-Host "View status:"
Write-Host ("   Get-ScheduledTaskInfo -TaskName '" + $TaskName + "'")
Write-Host ""
Write-Host "View logs (after a run):"
Write-Host ("   Get-Content '" + (Join-Path $PSScriptRoot "logs\scheduled_*.log") + "' -Tail 50")
