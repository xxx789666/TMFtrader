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

# Telegram credential lookup chain (fixed 2026-09-12).
# BUG HISTORY: this used to be a single hardcoded path,
#   $ProjectRoot\TMFtrader-src\.env
# which does NOT exist on this machine. Send-TG bailed out on its very first
# line, so three consecutive weekly-review failures (2026-08-29 / 09-05 / 09-12,
# all "NIM 410 model end of life") produced zero alerts. It was only found when
# the task_result_sentinel scheduled task flagged LastTaskResult=1.
# Order matters: first file that yields BOTH token and chat id wins.
$EnvCandidates = @(
  (Join-Path $PSScriptRoot ".env.sync"),
  (Join-Path $ProjectRoot (Join-Path "TMFtrader-src" ".env")),
  "C:\Users\xx\Desktop\tmf-strategy-lab-main\tmf-strategy-lab-main\.env"
)

function Write-Log($msg) {
  $line = "{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
  Add-Content -LiteralPath $LogFile -Value $line -Encoding utf8
  Write-Output $line
}

function Get-TGCreds {
  foreach ($f in $EnvCandidates) {
    if (-not (Test-Path -LiteralPath $f)) { continue }
    $lines = Get-Content -LiteralPath $f -Encoding utf8
    $token = ($lines | Where-Object { $_ -match "^(TG_BOT_TOKEN|TELEGRAM_BOT_TOKEN)=" } | Select-Object -First 1) -replace "^[^=]+=", ""
    $chat  = ($lines | Where-Object { $_ -match "^(TG_CHAT_ID|TELEGRAM_CHAT_ID)=" }     | Select-Object -First 1) -replace "^[^=]+=", ""
    $token = $token.Trim().Trim('"').Trim("'")
    $chat  = $chat.Trim().Trim('"').Trim("'")
    if ($token -and $chat) { return @{ token = $token; chat = $chat; src = $f } }
  }
  return $null
}

function Send-TG($text) {
  $c = Get-TGCreds
  if (-not $c) {
    # Fail loud in the log. The task still exits non-zero, so task_result_sentinel
    # will surface it even though this alert path is dead.
    Write-Log "TG ALERT NOT SENT: no TG_BOT_TOKEN/TG_CHAT_ID found in any of:"
    foreach ($f in $EnvCandidates) { Write-Log ("  - " + $f) }
    return
  }
  try {
    $body = @{ chat_id = $c.chat; text = $text }
    Invoke-RestMethod -Uri ("https://api.telegram.org/bot" + $c.token + "/sendMessage") -Method Post -Body $body -TimeoutSec 10 | Out-Null
    Write-Log ("TG alert sent (creds from " + $c.src + ")")
  } catch {
    Write-Log ("TG send failed: " + $_)
  }
}

Write-Log "================================================================"
Write-Log "scheduled_trigger starting"
Write-Log "================================================================"

$wslList = wsl.exe --list --running 2>&1 | Out-String
Write-Log ("WSL running list: " + ($wslList -replace "[\r\n]+", " "))

# Step 0.5 (2026-07-28): deterministic audit digest BEFORE Hermes runs.
# Produces TMFtrader-src\data\hermes_digest\digest_latest.md (freshness + tape-progress
# + consistency material). Non-fatal: review still runs without it.
try {
  Write-Log "Running hermes_weekly_digest.py (pre-digest)"
  $Py     = "C:\Users\xx\AppData\Local\Programs\Python\Python312\python.exe"
  $Digest = Join-Path $PSScriptRoot "hermes_weekly_digest.py"
  $dout   = & $Py $Digest 2>&1 | Out-String
  Add-Content -LiteralPath $LogFile -Value $dout -Encoding utf8
  Write-Log "pre-digest done"
} catch {
  Write-Log ("pre-digest FAILED (non-fatal): " + $_.Exception.Message)
}

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
