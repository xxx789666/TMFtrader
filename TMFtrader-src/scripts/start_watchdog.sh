#!/bin/bash

# Restart 互斥 lock（防止 watchdog 同時間 restart 撞期）
_LOCK=/tmp/TMFtrader_restart_in_progress
echo "$$ $(date +%s)" > "$_LOCK"
trap 'rm -f "$_LOCK"' EXIT

cd /home/xx/TMFtrader-src
export TZ=Asia/Taipei
source .venv/bin/activate
pkill -f 'watchdog.py' 2>/dev/null || true
sleep 2
mkdir -p data/logs
nohup python3.12 scripts/watchdog.py --night >> data/logs/watchdog.log 2>&1 &
echo "[$(date '+%F %T')] Watchdog PID=$!" >> data/logs/watchdog.log
