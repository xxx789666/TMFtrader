#!/bin/bash
# 啟動 wave_exec_c(chips x 波浪 fade 政策 C「只逆向」/ MXF 小台 / 30m)PAPER 前推進程。
# 2026-08-26 user 拍板三政策全真 tick:A=chips_exec、B=wave_exec、C=這支。
# 訊號由 wave_fade_daily.py(cron 08:05)寫 data/wave_fade_c/next_signal.json(side=decC);
# 引擎策略類共用 WaveExecStrategy,由 WAVE_SIGNAL_DIR 切訊號目錄。
# 精準 kill:PIDFILE + /proc/environ 確認 STRATEGY_OWNER=wave_exec_c,絕不 pkill -f。
set -u
cd /home/xx/TMFtrader-src
# 市場休市日不啟動(2026-06-19 端午事故)。週末已由 cron Mon-Fri 排除。
if grep -qx "$(TZ=Asia/Taipei date +%F)" scripts/market_holidays.txt 2>/dev/null; then
  mkdir -p data/logs
  echo "[$(date '+%F %T')] $(TZ=Asia/Taipei date +%F) 市場休市 → 跳過 wave_exec_c 啟動" >> data/logs/holiday_skip.log
  exit 0
fi
# 清 stale bytecode(rsync 保留 mtime → 舊 .pyc 跑舊碼,2026-06-05 事故根因)。
find . -name '*.pyc' -not -path './.venv/*' -delete 2>/dev/null || true
export TZ=Asia/Taipei
PY=.venv/bin/python3
PIDFILE=/tmp/TMFtrader_wave_exec_c_paper.pid

if [ -f "$PIDFILE" ]; then
  OLD=$(cat "$PIDFILE" 2>/dev/null || true)
  if [ -n "${OLD:-}" ] && kill -0 "$OLD" 2>/dev/null; then
    if tr '\0' ' ' < "/proc/$OLD/environ" 2>/dev/null | grep -q 'STRATEGY_OWNER=wave_exec_c'; then
      kill "$OLD" 2>/dev/null || true
      sleep 3
    fi
  fi
fi

export TRADING_MODE=paper
export INSTRUMENTS=MXF
export TIMEFRAME=30
export STRATEGY_TYPE=wave_exec_c
export STRATEGY_OWNER=wave_exec_c
export WAVE_SIGNAL_DIR=data/wave_fade_c
export RISK_PROFILE=fixed1_paper
export RECORD_TICKS=0
export RECORD_DECISIONS=1

LOG="data/logs/wave_exec_c_paper_$(date +%Y%m%d).log"
mkdir -p data/logs
nohup "$PY" scripts/start_paper.py --mode paper >> "$LOG" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PIDFILE"
echo "[$(date '+%F %T')] wave_exec_c PAPER start_paper.py PID=$NEW_PID | TF=30 owner=wave_exec_c INSTRUMENTS=MXF RISK=fixed1_paper 政策C只逆向 08:45窗進1口/-2%硬停/13:30強平 headless(TG-only)" >> "$LOG"
