#!/bin/bash
# 【STAGED — 6/5 cutover 用,尚未加 cron;被呼叫才會跑】
# breakout_v7 LIVE(headless、真實下單)。取代現行 live breakout(start.py)。日盤 5m。
#
# 三支 live(breakout_v7/day_orb/night_v3)共用同一個 TMF 帳戶,靠 core/position_lock.py
# (mode=live → data/active_position.json)做「同時只 1 支持倉」互斥;engine 啟動同步亦 owner-aware
# (只領養本 owner 或無主孤兒的倉,不碰別支的單 → 不平錯單,commit 7ad4fb3)。
# headless:3 支 live 不能各開 dashboard(uvicorn port 衝突);進出場/止損/盤末強平等事件走 TG。
# 精準 kill:PIDFILE + /proc/environ 確認 STRATEGY_OWNER=breakout_v7,**絕不 pkill -f 'start_paper.py'**
# (否則會誤殺 day_orb/night_v3 的 live 進程)。
set -u
cd /home/xx/TMFtrader-src
export TZ=Asia/Taipei
PY=.venv/bin/python3
PIDFILE=/tmp/TMFtrader_breakout_v7_live.pid

if [ -f "$PIDFILE" ]; then
  OLD=$(cat "$PIDFILE" 2>/dev/null || true)
  if [ -n "${OLD:-}" ] && kill -0 "$OLD" 2>/dev/null; then
    if tr '\0' ' ' < "/proc/$OLD/environ" 2>/dev/null | grep -q 'STRATEGY_OWNER=breakout_v7'; then
      kill "$OLD" 2>/dev/null || true
      sleep 3
    fi
  fi
fi

export TRADING_MODE=live
export INSTRUMENTS=TMF
export TIMEFRAME=5
export STRATEGY_TYPE=breakout_v7
export STRATEGY_OWNER=breakout_v7
export RECORD_TICKS=1   # 3 支 live 只由 v7 錄 tick(取代原 breakout 的錄製);day_orb/night_v3=0 避免同檔競寫

LOG="data/logs/breakout_v7_live_$(date +%Y%m%d).log"
mkdir -p data/logs
nohup "$PY" scripts/start_paper.py --mode live >> "$LOG" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PIDFILE"
echo "[$(date '+%F %T')] breakout_v7 LIVE start_paper.py --mode live PID=$NEW_PID | TF=5 owner=breakout_v7 headless 真實下單" >> "$LOG"
