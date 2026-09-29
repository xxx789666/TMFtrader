#!/bin/bash
# night_v3 LIVE on MXF(小台 50元/點)。2026-06-08 起由 TMF 轉 MXF。夜盤 60m、04:55 盤末強平。
# point_value=50 / max_loss_twd=20000 由 engine 依 MXF spec 自動對齊(launcher 只設 INSTRUMENTS=MXF)。
# 沿用同一 PID 檔 → cutover 換手 + watchdog 不用改。精準 kill:PIDFILE + /proc/environ。
set -u
cd /home/xx/TMFtrader-src
find . -name '*.pyc' -not -path './.venv/*' -delete 2>/dev/null || true
export TZ=Asia/Taipei
PY=.venv/bin/python3
PIDFILE=/tmp/TMFtrader_night_v3_live.pid

if [ -f "$PIDFILE" ]; then
  OLD=$(cat "$PIDFILE" 2>/dev/null || true)
  if [ -n "${OLD:-}" ] && kill -0 "$OLD" 2>/dev/null; then
    if tr '\0' ' ' < "/proc/$OLD/environ" 2>/dev/null | grep -q 'STRATEGY_OWNER=night_v3'; then
      kill "$OLD" 2>/dev/null || true
      sleep 3
    fi
  fi
fi

export TRADING_MODE=live
export INSTRUMENTS=MXF
export TIMEFRAME=60
export STRATEGY_TYPE=night_v3
export STRATEGY_OWNER=night_v3
export RECORD_TICKS=0

LOG="data/logs/night_v3_mxf_live_$(date +%Y%m%d).log"
mkdir -p data/logs
nohup "$PY" scripts/start_paper.py --mode live >> "$LOG" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PIDFILE"
echo "[$(date '+%F %T')] night_v3 LIVE MXF PID=$NEW_PID | TF=60 owner=night_v3 INSTRUMENTS=MXF(pv50/maxloss20000自動) headless 真實下單" >> "$LOG"

if [ -f .env ]; then
  TG_TOKEN=$(grep '^TG_BOT_TOKEN=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")
  TG_CHAT=$(grep '^TG_CHAT_ID=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")
  [ -n "$TG_TOKEN" ] && [ -n "$TG_CHAT" ] && \
    curl -sS --max-time 10 -o /dev/null \
      "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${TG_CHAT}" \
      --data-urlencode "text=🔄 [Cron] night_v3 LIVE 重啟 PID=$NEW_PID (MXF/小台/TF60/夜盤)
時間: $(date '+%F %H:%M:%S')" 2>/dev/null || true
fi
