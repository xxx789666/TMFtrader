#!/bin/bash
# 啟動 chips_exec（chips_combo 訊號的引擎真 tick paper 執行載具 / MXF 小台 / 30m）PAPER 前推進程。
# 2026-06-09 起取代 aft_orb 那條永豐連線:訊號由 chips_combo_daily.py(cron 18:30)算、寫
# data/chips_combo/next_signal.json;這支只在日盤開盤窗讀 side → 1 口真 tick 進場、-2% 引擎硬停、
# 13:44 收盤強平。固定 1 口靠 RISK_PROFILE=fixed1_paper(max_contracts=1)。headless、事件走 TG。
# 與 live 兩支隔離(自己的 data/paper/chips_exec/)。需在 09:00 進場窗前啟動(cron pre-open ~08:40)。
# 精準 kill:PIDFILE + /proc/environ 確認 STRATEGY_OWNER=chips_exec,絕不 pkill -f。
set -u
cd /home/xx/TMFtrader-src
# 清 stale bytecode(rsync 保留 mtime → Python 沿用舊 .pyc 跑舊碼,2026-06-05 事故根因)。
find . -name '*.pyc' -not -path './.venv/*' -delete 2>/dev/null || true
export TZ=Asia/Taipei
PY=.venv/bin/python3
PIDFILE=/tmp/TMFtrader_chips_exec_paper.pid

if [ -f "$PIDFILE" ]; then
  OLD=$(cat "$PIDFILE" 2>/dev/null || true)
  if [ -n "${OLD:-}" ] && kill -0 "$OLD" 2>/dev/null; then
    if tr '\0' ' ' < "/proc/$OLD/environ" 2>/dev/null | grep -q 'STRATEGY_OWNER=chips_exec'; then
      kill "$OLD" 2>/dev/null || true
      sleep 3
    fi
  fi
fi

export TRADING_MODE=paper
export INSTRUMENTS=MXF       # 2026-06-09 由 TMF 微台改小台 MXF(引擎自動對齊 pv50;preset 仍鎖 1 口)
export TIMEFRAME=30
export STRATEGY_TYPE=chips_exec
export STRATEGY_OWNER=chips_exec
export RISK_PROFILE=fixed1_paper
export RECORD_TICKS=0
export RECORD_DECISIONS=1   # 錄決策帶(snapshot/訊號)→ 日後 decision-tape 回測

LOG="data/logs/chips_exec_paper_$(date +%Y%m%d).log"
mkdir -p data/logs
nohup "$PY" scripts/start_paper.py --mode paper >> "$LOG" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PIDFILE"
echo "[$(date '+%F %T')] chips_exec PAPER start_paper.py PID=$NEW_PID | TF=30 owner=chips_exec INSTRUMENTS=MXF(小台) RISK=fixed1_paper 日盤開盤窗進1口/-2%硬停/13:44強平 headless(TG-only)" >> "$LOG"
