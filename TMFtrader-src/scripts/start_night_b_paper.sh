#!/bin/bash
# 啟動 night_b_exec(chips 夜盤變體 B / MXF 小台 / 30m)PAPER 前推進程。
# 2026-08-26 user 拍板由紙上 tape(chips_night_snapshot)升級真 tick:
# 訊號夜 18:36-19:30 進 1 口、過夜(週五=過週末)、trade_date 當日 13:30 強平、
# -2% 引擎硬停全程有效(補上紙上版「夜盤停損未模擬」的洞)。
# cron 18:32 TST(訊號 18:30 剛寫完);每日重啟 — 前一晚部位已於當日 13:30 平,重啟時必 flat。
# 精準 kill:PIDFILE + /proc/environ 確認 STRATEGY_OWNER=night_b_exec,絕不 pkill -f。
set -u
cd /home/xx/TMFtrader-src
# 市場休市日不啟動。週末已由 cron Mon-Fri 排除(週五啟動的行程自己活過週末)。
if grep -qx "$(TZ=Asia/Taipei date +%F)" scripts/market_holidays.txt 2>/dev/null; then
  mkdir -p data/logs
  echo "[$(date '+%F %T')] $(TZ=Asia/Taipei date +%F) 市場休市 → 跳過 night_b 啟動" >> data/logs/holiday_skip.log
  exit 0
fi
find . -name '*.pyc' -not -path './.venv/*' -delete 2>/dev/null || true
export TZ=Asia/Taipei
PY=.venv/bin/python3
PIDFILE=/tmp/TMFtrader_night_b_paper.pid

if [ -f "$PIDFILE" ]; then
  OLD=$(cat "$PIDFILE" 2>/dev/null || true)
  if [ -n "${OLD:-}" ] && kill -0 "$OLD" 2>/dev/null; then
    if tr '\0' ' ' < "/proc/$OLD/environ" 2>/dev/null | grep -q 'STRATEGY_OWNER=night_b_exec'; then
      kill "$OLD" 2>/dev/null || true
      sleep 3
    fi
  fi
fi

export TRADING_MODE=paper
export INSTRUMENTS=MXF
export TIMEFRAME=30
export STRATEGY_TYPE=night_b_exec
export STRATEGY_OWNER=night_b_exec
export RISK_PROFILE=fixed1_paper
export RECORD_TICKS=0
export RECORD_DECISIONS=1

LOG="data/logs/night_b_paper_$(date +%Y%m%d).log"
mkdir -p data/logs
nohup "$PY" scripts/start_paper.py --mode paper >> "$LOG" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PIDFILE"
echo "[$(date '+%F %T')] night_b_exec PAPER start_paper.py PID=$NEW_PID | TF=30 owner=night_b_exec INSTRUMENTS=MXF RISK=fixed1_paper 夜18:36-19:30進1口/-2%硬停全程/隔日13:30強平 headless(TG-only)" >> "$LOG"
