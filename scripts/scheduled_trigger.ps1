# Windows Task Scheduler 入口（PowerShell → WSL2 → run_weekly_review.sh）
#
# Task Scheduler 每週六 09:00 (Asia/Taipei) 觸發。本腳本：
#   1. 確認 WSL2 Ubuntu 在跑（沒在跑就喚起）
#   2. 在 WSL2 內執行 run_weekly_review.sh
#   3. 把整段 log 寫回 Windows 端 logs/
#   4. 失敗時透過 Telegram 通知
#
# Task Scheduler 設定建議：
#   - Trigger: Weekly, Saturday 09:00 (start now), local time
#   - Action: Start a program
#       Program/script: powershell.exe
#       Add arguments:  -NoProfile -ExecutionPolicy Bypass -File "C:\Users\xx\Desktop\vps永豐微台指\scripts\scheduled_trigger.ps1"
#   - Conditions: ✅ Wake the computer to run this task
#   - Settings:   ✅ Run task as soon as possible after a scheduled start is missed
#                 ✅ If the task fails, restart every 10 minutes, up to 3 times
#                 ✅ Stop task if runs longer than 30 minutes

$ErrorActionPreference = "Stop"
$ProjectRoot = "C:\Users\xx\Desktop\vps永豐微台指"
$LogDir      = Join-Path $ProjectRoot "scripts\logs"
$null = New-Item -ItemType Directory -Force -Path $LogDir
$Stamp       = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile     = Join-Path $LogDir "scheduled_$Stamp.log"

function Write-Log($msg) {
  $line = "{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
  Add-Content -Path $LogFile -Value $line -Encoding utf8
  Write-Output $line
}

function Send-TG($text) {
  $envFile = Join-Path $ProjectRoot "ultra-trader-src\.env"
  if (-not (Test-Path $envFile)) { return }
  $token = (Get-Content $envFile | Where-Object { $_ -match "^(TG_BOT_TOKEN|TELEGRAM_BOT_TOKEN)=" } | Select-Object -First 1) -replace "^[^=]+=", ""
  $chat  = (Get-Content $envFile | Where-Object { $_ -match "^(TG_CHAT_ID|TELEGRAM_CHAT_ID)=" }   | Select-Object -First 1) -replace "^[^=]+=", ""
  if (-not ($token -and $chat)) { return }
  try {
    $body = @{ chat_id = $chat; text = $text; parse_mode = "Markdown" }
    Invoke-RestMethod -Uri "https://api.telegram.org/bot$token/sendMessage" -Method Post -Body $body -TimeoutSec 10 | Out-Null
  } catch {
    Write-Log "TG send failed: $_"
  }
}

Write-Log "================================================================"
Write-Log "scheduled_trigger 啟動"
Write-Log "================================================================"

# ---- 預檢 WSL2 ----
$wslList = wsl.exe --list --running 2>&1
Write-Log "WSL running: $wslList"

# WSL2 內專案路徑（從 /mnt/c/... 經 ~/vps_trader symlink 進去）
$wslCmd = "bash -lc 'cd ~/vps_trader && bash scripts/run_weekly_review.sh 2>&1'"

try {
  Write-Log "呼叫 wsl.exe 跑 run_weekly_review.sh"
  $output = wsl.exe -d Ubuntu -- bash -lc "cd ~/vps_trader && bash scripts/run_weekly_review.sh 2>&1"
  $exit = $LASTEXITCODE
  Add-Content -Path $LogFile -Value $output -Encoding utf8
  Write-Log "wsl exit code: $exit"

  if ($exit -ne 0) {
    Send-TG "⚠️ *週度覆盤 Task Scheduler 異常*`nexit=$exit`nlog: ``$LogFile``"
    Write-Log "FAILED (exit=$exit)"
    exit $exit
  }
  Write-Log "DONE"
} catch {
  $msg = $_.Exception.Message
  Write-Log "ERROR: $msg"
  Send-TG "🛑 *週度覆盤 Task Scheduler 例外*``$msg``"
  exit 1
}
