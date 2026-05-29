#!/bin/bash

# Restart 互斥 lock（防止 watchdog 同時間 restart 撞期）
_LOCK=/tmp/TMFtrader_restart_in_progress
echo "$$ $(date +%s)" > "$_LOCK"
trap 'rm -f "$_LOCK"' EXIT

cd /home/xx/TMFtrader-src
export TZ=Asia/Taipei
source .venv/bin/activate
pkill -f 'night_orb.py' 2>/dev/null || true
sleep 3
LOG="data/logs/TMFtrader_$(date +%Y%m%d).log"
mkdir -p data/logs
nohup python3.12 scripts/night_orb.py --threshold 0.40 >> "$LOG" 2>&1 &
NEW_PID=$!
echo "[$(date '+%F %T')] Night ORB (night_orb.py) PID=$NEW_PID" >> "$LOG"

# TG notify（失敗靜默、不影響重啟流程）
if [ -f .env ]; then
  TG_TOKEN=$(grep '^TG_BOT_TOKEN=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")
  TG_CHAT=$(grep '^TG_CHAT_ID=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")
  # 讀實際 trading mode、決定 TG 訊息標籤（避免「paper」字樣誤導）
  MODE=$(grep '^TRADING_MODE=' .env | cut -d= -f2- | tr -d '"' | tr -d "'" | tr '[:upper:]' '[:lower:]')
  if [ "$MODE" = "live" ]; then
    NIGHT_LABEL="夜盤 ORB LIVE 排程重啟"
  else
    NIGHT_LABEL="夜盤 ORB Paper 排程重啟"
  fi
  [ -n "$TG_TOKEN" ] && [ -n "$TG_CHAT" ] && \
    curl -sS --max-time 10 -o /dev/null \
      "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${TG_CHAT}" \
      --data-urlencode "text=🌙 [Cron] $NIGHT_LABEL PID=$NEW_PID
時間: $(date '+%F %H:%M:%S')" 2>/dev/null || true
fi
