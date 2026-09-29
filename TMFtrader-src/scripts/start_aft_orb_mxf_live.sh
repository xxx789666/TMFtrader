#!/bin/bash
# aft_orb LIVE on MXF(小台 50元/點)。2026-06-08 起 paper→live 第 4 支。傍晚 ORB 30m、force_close 23:30。
# 與 night_v3 靠單池鎖交班(aft_orb 抱倉→放鎖後 night_v3 接手,engine 已修 _traded 重置)。
# point_value=50 / max_loss_twd=20000 由 engine 依 MXF spec 自動對齊。
# paper→live 轉換:同時殺掉舊 paper PID(/tmp/TMFtrader_aft_orb_paper.pid)+ live PID,再起 live。
set -u
cd /home/xx/TMFtrader-src
find . -name '*.pyc' -not -path './.venv/*' -delete 2>/dev/null || true
export TZ=Asia/Taipei
PY=.venv/bin/python3
PIDFILE=/tmp/TMFtrader_aft_orb_live.pid
PAPERPID=/tmp/TMFtrader_aft_orb_paper.pid

# 殺掉本 owner 的舊進程(paper 或 live 都殺,避免 paper+live 並存雙連線);精準比對 owner。
for pf in "$PAPERPID" "$PIDFILE"; do
  if [ -f "$pf" ]; then
    OLD=$(cat "$pf" 2>/dev/null || true)
    if [ -n "${OLD:-}" ] && kill -0 "$OLD" 2>/dev/null; then
      if tr '\0' ' ' < "/proc/$OLD/environ" 2>/dev/null | grep -q 'STRATEGY_OWNER=aft_orb'; then
        kill "$OLD" 2>/dev/null || true
        sleep 2
      fi
    fi
  fi
done

export TRADING_MODE=live
export INSTRUMENTS=MXF
export TIMEFRAME=30
export STRATEGY_TYPE=aft_orb
export STRATEGY_OWNER=aft_orb
export RECORD_TICKS=0

LOG="data/logs/aft_orb_mxf_live_$(date +%Y%m%d).log"
mkdir -p data/logs
nohup "$PY" scripts/start_paper.py --mode live >> "$LOG" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PIDFILE"
echo "[$(date '+%F %T')] aft_orb LIVE MXF PID=$NEW_PID | TF=30 owner=aft_orb 傍晚窗15:00-23:30 force_close23:30 INSTRUMENTS=MXF(pv50/maxloss20000自動) headless 真實下單" >> "$LOG"

if [ -f .env ]; then
  TG_TOKEN=$(grep '^TG_BOT_TOKEN=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")
  TG_CHAT=$(grep '^TG_CHAT_ID=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")
  [ -n "$TG_TOKEN" ] && [ -n "$TG_CHAT" ] && \
    curl -sS --max-time 10 -o /dev/null \
      "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${TG_CHAT}" \
      --data-urlencode "text=🔄 [Cron] aft_orb LIVE 重啟 PID=$NEW_PID (MXF/小台/TF30/傍晚) 第4支轉live
時間: $(date '+%F %H:%M:%S')" 2>/dev/null || true
fi
