#!/usr/bin/env bash
# scripts/watchdog_alert.sh — Linux 告警型看門狗（cron 每 5 分鐘觸發）
#
# 設計原則（2026-05-26）：
#   - 只「偵測 + 推 TG」，絕不自動重啟、絕不呼叫任何 HTTP / Shioaji API。
#   - 零 fetch_contracts、零 quota 風險（取代舊 watchdog.py：那支是 Windows 版、
#     夜盤又去 poll 不存在的 :8889 HTTP server → 每 120s 觸發重啟迴圈 → quota 爆表）。
#   - 純讀本機 log + pgrep + curl TG。失敗一律靜默，不影響系統。
#
# 偵測三件事：
#   1. 日盤引擎 start.py（24h 跑）是否還活著
#   2. 夜盤 night_orb.py 是否在其交易夜窗口（14:55–翌日05:10）內還活著
#   3. start.py 的 [Heartbeat] 是否在交易時段內 >12 分鐘沒更新（凍結偵測）
#      （night_orb 的 tick 中斷在 21:00–04:30 已由它自己推 TG，這裡不重複）
#
# 同一告警 30 分鐘內不重複推（/tmp 冷卻檔）。
set -u
export TZ=Asia/Taipei

PROJ=/home/xx/TMFtrader-src
LOG_DIR="$PROJ/data/logs"
WLOG="$LOG_DIR/watchdog_alert.log"
ENG_LOG="$LOG_DIR/TMFtrader_$(date +%Y%m%d).log"
COOLDOWN=1800        # 同一告警 30 分鐘冷卻
HEARTBEAT_MAX=720    # 12 分鐘無 heartbeat 視為凍結

mkdir -p "$LOG_DIR"

HHMM=$((10#$(date +%H%M)))   # 0905 → 905（避免 08 被當八進位）
DOW=$(date +%u)              # 1=Mon … 7=Sun
EPOCH=$(date +%s)

wlog(){ echo "[$(date '+%F %T')] $*" >> "$WLOG"; }

# ── TG（讀 .env、與 restart_*.sh 同套路；失敗靜默）
TG_TOKEN=$(grep '^TG_BOT_TOKEN=' "$PROJ/.env" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
TG_CHAT=$(grep '^TG_CHAT_ID='  "$PROJ/.env" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
send_tg(){
  [ -n "$TG_TOKEN" ] && [ -n "$TG_CHAT" ] && \
    curl -sS --max-time 10 -o /dev/null \
      "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${TG_CHAT}" \
      --data-urlencode "text=$1" 2>/dev/null || true
}

# ── 冷卻控制：同 key 在 COOLDOWN 秒內只推一次
alert(){
  local key="$1" msg="$2"
  local sf="/tmp/TMFtrader_wd_alert_${key}"
  local last=0
  [ -f "$sf" ] && last=$(cat "$sf" 2>/dev/null || echo 0)
  if [ $((EPOCH - last)) -ge $COOLDOWN ]; then
    echo "$EPOCH" > "$sf"
    wlog "ALERT[$key] $msg"
    send_tg "🐶 [Watchdog] $msg"
  else
    wlog "skip[$key]（冷卻中）$msg"
  fi
}

# ── 1. start.py：24h 應一直活著（grace：08:28–08:36 日盤重啟窗）
if [ $HHMM -ge 828 ] && [ $HHMM -le 836 ]; then
  wlog "日盤重啟窗、跳過 start.py 檢查"
elif pgrep -f 'scripts/start.py' >/dev/null; then
  wlog "start.py OK"
else
  alert "day_proc" "🚨 日盤引擎 start.py 不在了（pgrep 找不到）@ $(date '+%m/%d %H:%M')"
fi

# ── 2. night_orb.py：只在交易夜窗口檢查（grace：14:54–14:58 夜盤重啟窗）
night_expected=0
[ $HHMM -ge 1455 ] && [ $DOW -ge 1 ] && [ $DOW -le 5 ] && night_expected=1   # 週一~五傍晚開
[ $HHMM -le 510 ]  && [ $DOW -ge 2 ] && [ $DOW -le 6 ] && night_expected=1   # 翌日凌晨（週二~六）
if [ $HHMM -ge 1454 ] && [ $HHMM -le 1458 ]; then
  night_expected=0
  wlog "夜盤重啟窗、跳過 night_orb 檢查"
fi
# grace：cron 05:10 TST 殺 night_orb（10 21 * * 0-4 UTC）→ watchdog 同分鐘跑會誤報、給 ±2 分緩衝
if [ $HHMM -ge 508 ] && [ $HHMM -le 512 ]; then
  night_expected=0
  wlog "夜盤 05:10 收盤窗、跳過 night_orb 檢查"
fi
# [PAUSED-NIGHT-ORB 2026-05-30] live 夜盤暫停中、跳過存活檢查（恢復：刪 $PROJ/.night_orb_paused）
if [ -f "$PROJ/.night_orb_paused" ]; then night_expected=0; wlog "night_orb 已暫停（marker）、跳過檢查"; fi
if [ $night_expected -eq 1 ]; then
  if pgrep -f 'night_orb.py' >/dev/null; then
    wlog "night_orb.py OK"
  else
    alert "night_proc" "🚨 夜盤 night_orb.py 不在了（pgrep 找不到）@ $(date '+%m/%d %H:%M')"
  fi
else
  wlog "非夜盤窗口、跳過 night_orb 檢查"
fi

# ── 3. heartbeat 凍結偵測（只在交易時段：日 08:45–13:45、夜 15:00–翌05:00）
trading=0
[ $HHMM -ge 845 ] && [ $HHMM -le 1345 ] && trading=1
{ [ $HHMM -ge 1500 ] || [ $HHMM -le 500 ]; } && trading=1
if [ $trading -eq 1 ] && [ -f "$ENG_LOG" ]; then
  last_hb=$(grep -a 'Heartbeat' "$ENG_LOG" \
            | grep -aoE '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}' \
            | tail -1)
  if [ -n "$last_hb" ]; then
    last_epoch=$(date -d "$last_hb" +%s 2>/dev/null || echo 0)
    age=$((EPOCH - last_epoch))
    if [ $age -gt $HEARTBEAT_MAX ]; then
      alert "heartbeat" "⚠️ 引擎 heartbeat 已 $((age/60)) 分鐘沒更新（疑似凍結）最後:$last_hb"
    else
      wlog "heartbeat OK (${age}s 前)"
    fi
  else
    wlog "今日 log 尚無 [Heartbeat]"
  fi
else
  wlog "非交易時段或無 log、跳過 heartbeat 檢查"
fi
