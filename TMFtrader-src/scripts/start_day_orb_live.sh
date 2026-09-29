#!/bin/bash
# 【STAGED — 6/5 cutover 用,尚未加 cron;被呼叫才會跑】
# day_orb LIVE(headless、真實下單)。日盤 30m。與 breakout_v7/night_v3 live 共用同一 TMF 帳戶,
# 靠 position_lock(mode=live)互斥(同時只 1 支持倉)、engine owner-aware 同步(不領養別支倉)。
# headless、事件走 TG。精準 kill:PIDFILE + /proc/environ 確認 STRATEGY_OWNER=day_orb,絕不 pkill -f。
set -u
cd /home/xx/TMFtrader-src
# 清 stale bytecode:rsync 保留來源 mtime 時 Python 會沿用舊 .pyc 跑到舊碼
# (2026-06-05 night_v3 跑成 or_bars=6 事故根因)。每次重啟先清、保證載入當前 .py。
find . -name '*.pyc' -not -path './.venv/*' -delete 2>/dev/null || true
export TZ=Asia/Taipei
PY=.venv/bin/python3
PIDFILE=/tmp/TMFtrader_day_orb_live.pid

if [ -f "$PIDFILE" ]; then
  OLD=$(cat "$PIDFILE" 2>/dev/null || true)
  if [ -n "${OLD:-}" ] && kill -0 "$OLD" 2>/dev/null; then
    if tr '\0' ' ' < "/proc/$OLD/environ" 2>/dev/null | grep -q 'STRATEGY_OWNER=day_orb'; then
      kill "$OLD" 2>/dev/null || true
      sleep 3
    fi
  fi
fi

export TRADING_MODE=live
export INSTRUMENTS=TMF
export TIMEFRAME=30
export STRATEGY_TYPE=day_orb
export STRATEGY_OWNER=day_orb
export RECORD_TICKS=0

LOG="data/logs/day_orb_live_$(date +%Y%m%d).log"
mkdir -p data/logs
nohup "$PY" scripts/start_paper.py --mode live >> "$LOG" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PIDFILE"
echo "[$(date '+%F %T')] day_orb LIVE start_paper.py --mode live PID=$NEW_PID | TF=30 owner=day_orb headless 真實下單" >> "$LOG"

# 推 TG:排程重啟通知(沿用 restart_day.sh 套路;失敗靜默)
if [ -f .env ]; then
  TG_TOKEN=$(grep '^TG_BOT_TOKEN=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")
  TG_CHAT=$(grep '^TG_CHAT_ID=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")
  [ -n "$TG_TOKEN" ] && [ -n "$TG_CHAT" ] && \
    curl -sS --max-time 10 -o /dev/null \
      "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${TG_CHAT}" \
      --data-urlencode "text=🔄 [Cron] day_orb LIVE 排程重啟 PID=$NEW_PID (TF30/日盤)
時間: $(date '+%F %H:%M:%S')" 2>/dev/null || true
fi
