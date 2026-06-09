#!/bin/bash
# 啟動 maxpain_exec（maxpain_v2 訊號的引擎真 tick paper 執行載具 / MXF 小台 / 30m / 完整 2 口含加碼）。
# 2026-06-09:訊號由 maxpain_daily.py(cron 18:40、官網 OI)算、寫 data/maxpain_v2/next_signal.json;
# 這支執行:t+1 開盤窗進第1口、+1% 引擎 scale-in 加第2口、-2% 引擎硬停、結算日 13:30 強平。
# 多日持倉跨重啟靠引擎 _strategy_state(ed/S1/scaled)。固定第1口靠 RISK_PROFILE=fixed1_paper。
# 與 live/其他 paper 隔離(per-owner 鎖 data/paper/maxpain_exec/、不擋 chips_exec)。headless、事件走 TG。
# 精準 kill:PIDFILE + /proc/environ 確認 STRATEGY_OWNER=maxpain_exec,絕不 pkill -f。
set -u
cd /home/xx/TMFtrader-src
find . -name '*.pyc' -not -path './.venv/*' -delete 2>/dev/null || true
export TZ=Asia/Taipei
PY=.venv/bin/python3
PIDFILE=/tmp/TMFtrader_maxpain_exec_paper.pid

if [ -f "$PIDFILE" ]; then
  OLD=$(cat "$PIDFILE" 2>/dev/null || true)
  if [ -n "${OLD:-}" ] && kill -0 "$OLD" 2>/dev/null; then
    if tr '\0' ' ' < "/proc/$OLD/environ" 2>/dev/null | grep -q 'STRATEGY_OWNER=maxpain_exec'; then
      kill "$OLD" 2>/dev/null || true
      sleep 3
    fi
  fi
fi

export TRADING_MODE=paper
export INSTRUMENTS=MXF
export TIMEFRAME=30
export STRATEGY_TYPE=maxpain_exec
export STRATEGY_OWNER=maxpain_exec
export RISK_PROFILE=fixed1_paper
export RECORD_TICKS=0
export RECORD_DECISIONS=1

LOG="data/logs/maxpain_exec_paper_$(date +%Y%m%d).log"
mkdir -p data/logs
nohup "$PY" scripts/start_paper.py --mode paper >> "$LOG" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PIDFILE"
echo "[$(date '+%F %T')] maxpain_exec PAPER start_paper.py PID=$NEW_PID | TF=30 owner=maxpain_exec INSTRUMENTS=MXF(小台) RISK=fixed1_paper 完整2口:t+1進/+1%加碼/-2%硬停/結算日13:30強平 headless(TG-only)" >> "$LOG"
