#!/bin/bash
# 啟動 aft_orb（傍晚 ORB / DayORBStrategy 套 15:00–23:30 窗 / 30m）PAPER 前推進程。
# 2026-06-05 起 forward-test 第 4 候選(只 paper、不上 live);與 live 三支隔離(自己的 data/paper/aft_orb/)。
# 傍晚段:OR 從 15:00 起、force_close 23:30。需在 15:00 前啟動才能乾淨建 OR(cron 14:52)。
# headless、事件走 TG。精準 kill:PIDFILE + /proc/environ 確認 STRATEGY_OWNER=aft_orb,絕不 pkill -f。
set -u
cd /home/xx/TMFtrader-src
# 清 stale bytecode:rsync 保留來源 mtime 時 Python 會沿用舊 .pyc 跑到舊碼
# (2026-06-05 night_v3 跑成 or_bars=6 事故根因)。每次重啟先清、保證載入當前 .py。
find . -name '*.pyc' -not -path './.venv/*' -delete 2>/dev/null || true
export TZ=Asia/Taipei
PY=.venv/bin/python3
PIDFILE=/tmp/TMFtrader_aft_orb_paper.pid

if [ -f "$PIDFILE" ]; then
  OLD=$(cat "$PIDFILE" 2>/dev/null || true)
  if [ -n "${OLD:-}" ] && kill -0 "$OLD" 2>/dev/null; then
    if tr '\0' ' ' < "/proc/$OLD/environ" 2>/dev/null | grep -q 'STRATEGY_OWNER=aft_orb'; then
      kill "$OLD" 2>/dev/null || true
      sleep 3
    fi
  fi
fi

export TRADING_MODE=paper
export INSTRUMENTS=TMF
export TIMEFRAME=30
export STRATEGY_TYPE=aft_orb
export STRATEGY_OWNER=aft_orb
export RECORD_TICKS=0

LOG="data/logs/aft_orb_paper_$(date +%Y%m%d).log"
mkdir -p data/logs
nohup "$PY" scripts/start_paper.py --mode paper >> "$LOG" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PIDFILE"
echo "[$(date '+%F %T')] aft_orb PAPER start_paper.py PID=$NEW_PID | TF=30 owner=aft_orb 傍晚窗15:00-23:30 force_close23:30 headless(TG-only)" >> "$LOG"
