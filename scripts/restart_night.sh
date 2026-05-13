#!/bin/bash

# Restart 互斥 lock（防止 watchdog 同時間 restart 撞期）
_LOCK=/tmp/ultratrader_restart_in_progress
echo "$$ $(date +%s)" > "$_LOCK"
trap 'rm -f "$_LOCK"' EXIT

cd /home/xx/ultra-trader-src
export TZ=Asia/Taipei
source .venv/bin/activate
pkill -f 'paper_night_orb.py' 2>/dev/null || true
sleep 3
LOG="data/logs/ultratrader_$(date +%Y%m%d).log"
mkdir -p data/logs
nohup python3.12 scripts/paper_night_orb.py --threshold 0.40 >> "$LOG" 2>&1 &
NEW_PID=$!
echo "[$(date '+%F %T')] Night ORB (paper_night_orb.py) PID=$NEW_PID" >> "$LOG"

# TG notify（失敗靜默、不影響重啟流程）
if [ -f .env ]; then
  TG_TOKEN=$(grep '^TG_BOT_TOKEN=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")
  TG_CHAT=$(grep '^TG_CHAT_ID=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")
  [ -n "$TG_TOKEN" ] && [ -n "$TG_CHAT" ] && \
    curl -sS --max-time 10 -o /dev/null \
      "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${TG_CHAT}" \
      --data-urlencode "text=🌙 [Cron] 夜盤 paper_night_orb 排程重啟 PID=$NEW_PID
時間: $(date '+%F %H:%M:%S')" 2>/dev/null || true
fi
