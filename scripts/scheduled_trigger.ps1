# Windows Task Scheduler entry point (PowerShell -> WSL2 -> run_weekly_review.sh)
# ASCII-only to avoid PowerShell 5.1 codepage parse errors on Chinese paths.
#
# Triggered every Saturday 09:00. This script:
#   1. Confirms WSL2 Ubuntu is reachable
#   2. Runs run_weekly_review.sh inside WSL Ubuntu (via ~/vps_trader symlink)
#   3. Captures all output into ./logs/scheduled_*.log
#   4. On failure: Telegram alert via Invoke-RestMethod

$ErrorActionPreference = "Stop"

# Resolve project paths from $PSScriptRoot (no hardcoded Chinese path)
$ProjectRoot = Split-Path -Parent $PSScriptRoot   # ...\vps<Chinese>
$LogDir      = Join-Path $PSScriptRoot "logs"
$null        = New-Item -ItemType Directory -Force -Path $LogDir
$Stamp       = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile     = Join-Path $LogDir ("scheduled_" + $Stamp + ".log")
$EnvFile     = Join-Path $ProjectRoot (Join-Path "ultra-trader-src" ".env")

function Write-Log($msg) {
  $line = "{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
  Add-Content -LiteralPath $LogFile -Value $line -Encoding utf8
  Write-Output $line
}

function Send-TG($text) {
  if (-not (Test-Path -LiteralPath $EnvFile)) { return }
  $token = (Get-Content -LiteralPath $EnvFile -Encoding utf8 | Where-Object { $_ -match "^(TG_BOT_TOKEN|TELEGRAM_BOT_TOKEN)=" } | Select-Object -First 1) -replace "^[^=]+=", ""
  $chat  = (Get-Content -LiteralPath $EnvFile -Encoding utf8 | Where-Object { $_ -match "^(TG_CHAT_ID|TELEGRAM_CHAT_ID)=" }   | Select-Object -First 1) -replace "^[^=]+=", ""
  if (-not ($token -and $chat)) { return }
  try {
    $body = @{ chat_id = $chat; text = $text }
    Invoke-RestMethod -Uri ("https://api.telegram.org/bot" + $token + "/sendMessage") -Method Post -Body $body -TimeoutSec 10 | Out-Null
  } catch {
    Write-Log ("TG send failed: " + $_)
  }
}

Write-Log "================================================================"
Write-Log "scheduled_trigger starting"
Write-Log "================================================================"

$wslList = wsl.exe --list --running 2>&1 | Out-String
Write-Log ("WSL running list: " + ($wslList -replace "[\r\n]+", " "))

try {
  Write-Log "Invoking wsl.exe -> ~/vps_trader/scripts/run_weekly_review.sh"
  # WSL2 sees the project via ~/vps_trader symlink (created during setup_wsl_local.sh)
  $cmd    = 'cd ~/vps_trader && bash scripts/run_weekly_review.sh 2>&1'
  $output = wsl.exe -d Ubuntu -- bash -lc $cmd
  $exit   = $LASTEXITCODE
  Add-Content -LiteralPath $LogFile -Value $output -Encoding utf8
  Write-Log ("wsl exit code: " + $exit)

  if ($exit -ne 0) {
    Send-TG ("[ALERT] TMF weekly review scheduled task exit=" + $exit + " log: " + $LogFile)
    Write-Log ("FAILED (exit=" + $exit + ")")
    exit $exit
  }
  Write-Log "DONE"
} catch {
  $msg = $_.Exception.Message
  Write-Log ("ERROR: " + $msg)
  Send-TG ("[ALERT] TMF weekly review scheduled task exception: " + $msg)
  exit 1
}
