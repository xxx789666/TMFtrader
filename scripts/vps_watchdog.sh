#!/usr/bin/env bash
# VPS Linux-native watchdog（取代 watchdog.py、避開 Windows-only creationflags 等 code）
#
# 守兩個 process：
#   1. scripts/start.py（24h 跑、需檢查 8888 port 在）
#   2. scripts/paper_night_orb.py（只在 14:55–05:10 夜盤時段檢查）
#
# 用 cron 每分鐘跑一次：
#   * * * * * /home/xx/ultra-trader-src/scripts/vps_watchdog.sh
#
# 失敗時：寫 vps_watchdog.log + 推 TG + 重啟對應 process

set +e
export TZ=Asia/Taipei

PROJECT=$(cd "$(dirname "${BASH_SOURCE[0]}")"/.. && pwd)
LOG=$PROJECT/data/logs/vps_watchdog.log
mkdir -p "$(dirname "$LOG")"

# 讀 TG creds
[ -f "$PROJECT/.env" ] && {
  TG_TOKEN=$(grep '^TG_BOT_TOKEN=' "$PROJECT/.env" | cut -d= -f2- | tr -d '"' | tr -d "'")
  TG_CHAT=$(grep '^TG_CHAT_ID=' "$PROJECT/.env" | cut -d= -f2- | tr -d '"' | tr -d "'")
  [ -z "$TG_TOKEN" ] && TG_TOKEN=$(grep '^TELEGRAM_BOT_TOKEN=' "$PROJECT/.env" | cut -d= -f2-)
  [ -z "$TG_CHAT" ]  && TG_CHAT=$(grep '^TELEGRAM_CHAT_ID='  "$PROJECT/.env" | cut -d= -f2-)
}

HOUR=$(date +%H)
MIN=$(date +%M)
DAY=$(date +%u)   # 1=Mon ... 7=Sun

log()    { echo "[$(date '+%F %T')] $*" >> "$LOG"; }
notify() {
  [ -n "$TG_TOKEN" ] && [ -n "$TG_CHAT" ] && \
    curl -sS --max-time 10 -o /dev/null \
      "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${TG_CHAT}" \
      --data-urlencode "text=$1" 2>/dev/null || true
}

# ─── 週末完全不動 ───────────────────────────────────────
if [ "$DAY" -ge 6 ]; then
  exit 0
fi

# ─── 收盤前 grace window（不重啟、避免最後幾分鐘留持倉）───
# 日盤 13:25–13:45 / 夜盤 04:45–05:10
case "$HOUR:$MIN" in
  13:2[5-9]|13:3[0-9]|13:4[0-5]) exit 0 ;;
  04:4[5-9]|04:5[0-9]|05:0[0-9]|05:10) exit 0 ;;
esac

# ─── 日盤檢查：start.py 在跑 + 8888 port 開 ───────────
DAY_PID=$(pgrep -f 'scripts/start.py' | head -1)
if [ -z "$DAY_PID" ]; then
  log "✗ start.py NOT running, restarting..."
  bash "$PROJECT/scripts/restart_day.sh" >/dev/null 2>&1
  sleep 5
  NEW_PID=$(pgrep -f 'scripts/start.py' | head -1)
  if [ -n "$NEW_PID" ]; then
    log "  ✓ restarted, new PID=$NEW_PID"
    notify "🔧 [VPS Watchdog] start.py 重啟成功 PID=$NEW_PID"
  else
    log "  ✗ restart FAILED"
    notify "🚨 [VPS Watchdog] start.py 重啟失敗 — 需人工檢查"
  fi
else
  # 額外查 8888 port（process 還在但卡死、port 沒 listen）
  # ss -tln 輸出：「LISTEN ... 127.0.0.1:8888 ...」LISTEN 在第一欄
  # 用 'LISTEN' + 含 ':8888' 雙條件、避免單向 regex 漏判
  if ! ss -tln 2>/dev/null | awk '$1=="LISTEN" && $4 ~ /:8888$/ {found=1} END{exit !found}'; then
    log "⚠ start.py PID=$DAY_PID running but 8888 not listening (10 min grace)..."
    # 給 process 10 分鐘啟動時間（剛重啟過要等 FastAPI bind）
    ETIME=$(ps -p $DAY_PID -o etimes= 2>/dev/null | tr -d ' ')
    if [ -n "$ETIME" ] && [ "$ETIME" -gt 600 ]; then
      log "  → process 已跑 ${ETIME}s 仍無 8888、強制重啟"
      kill $DAY_PID 2>/dev/null
      sleep 3
      bash "$PROJECT/scripts/restart_day.sh" >/dev/null 2>&1
      notify "🔧 [VPS Watchdog] start.py 8888 死、強制重啟"
    fi
  fi
fi

# ─── 夜盤檢查：只在 15:00–04:00 window ────────────────
# 15:00 起到 04:59 隔日
IS_NIGHT_WINDOW=0
if [ "$HOUR" -ge 15 ] || [ "$HOUR" -le 4 ]; then
  IS_NIGHT_WINDOW=1
fi

if [ "$IS_NIGHT_WINDOW" -eq 1 ]; then
  NIGHT_PID=$(pgrep -f 'paper_night_orb.py' | head -1)
  if [ -z "$NIGHT_PID" ]; then
    log "✗ paper_night_orb.py NOT running in night window, restarting..."
    bash "$PROJECT/scripts/restart_night.sh" >/dev/null 2>&1
    sleep 5
    NEW_PID=$(pgrep -f 'paper_night_orb.py' | head -1)
    if [ -n "$NEW_PID" ]; then
      log "  ✓ restarted, new PID=$NEW_PID"
      notify "🔧 [VPS Watchdog] paper_night_orb 重啟成功 PID=$NEW_PID"
    else
      log "  ✗ restart FAILED"
      notify "🚨 [VPS Watchdog] paper_night_orb 重啟失敗"
    fi
  fi
fi
