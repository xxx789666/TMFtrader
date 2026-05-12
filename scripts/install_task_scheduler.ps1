# 一鍵建立 Windows Task Scheduler 排程（週六 09:00 觸發 scheduled_trigger.ps1）
#
# 用法（在【系統管理員權限】PowerShell 內執行）：
#   powershell.exe -ExecutionPolicy Bypass -File "C:\Users\xx\Desktop\vps永豐微台指\scripts\install_task_scheduler.ps1"
#
# 解除：
#   Unregister-ScheduledTask -TaskName "TMF_Weekly_Review" -Confirm:$false

$ErrorActionPreference = "Stop"

$TaskName  = "TMF_Weekly_Review"
$Script    = "C:\Users\xx\Desktop\vps永豐微台指\scripts\scheduled_trigger.ps1"
$Description = "TMF 永豐微台指週度 LLM 覆盤；WSL2 + Hermes Agent + NIM"

if (-not (Test-Path $Script)) {
  Write-Error "找不到 scheduled_trigger.ps1：$Script"
  exit 1
}

# Trigger：每週六 09:00 (local)
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Saturday -At 9:00am

# Action：呼叫 PowerShell 跑 scheduled_trigger.ps1
$action = New-ScheduledTaskAction `
  -Execute "powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Script`""

# Settings：missed run 補跑、failure retry、wake to run、30 分鐘上限
$settings = New-ScheduledTaskSettingsSet `
  -StartWhenAvailable `
  -RestartInterval (New-TimeSpan -Minutes 10) `
  -RestartCount 3 `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
  -WakeToRun:$true `
  -DontStopIfGoingOnBatteries

# Principal：用目前使用者身分跑，UAC 不彈窗
$principal = New-ScheduledTaskPrincipal `
  -UserId "$env:USERDOMAIN\$env:USERNAME" `
  -LogonType Interactive `
  -RunLevel Limited

# 若已存在 → 移除重建
if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
  Write-Host "已存在同名工作，先移除..."
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
  -TaskName $TaskName `
  -Description $Description `
  -Trigger $trigger `
  -Action $action `
  -Settings $settings `
  -Principal $principal

Write-Host ""
Write-Host "✅ 已建立 Task Scheduler 工作 '$TaskName'"
Write-Host "   下次執行：" (Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo).NextRunTime
Write-Host ""
Write-Host "立即測試（不等週六）："
Write-Host "   Start-ScheduledTask -TaskName '$TaskName'"
Write-Host ""
Write-Host "看狀態："
Write-Host "   Get-ScheduledTaskInfo -TaskName '$TaskName'"
Write-Host ""
Write-Host "看 log："
Write-Host "   Get-Content 'C:\Users\xx\Desktop\vps永豐微台指\scripts\logs\scheduled_*.log' -Tail 50"
