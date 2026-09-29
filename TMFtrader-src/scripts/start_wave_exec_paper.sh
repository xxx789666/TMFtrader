#!/bin/bash
# 啟動 wave_exec(chips_combo × 波浪 fade 濾網 訊號的引擎真 tick paper 執行載具 / MXF 小台 / 30m)。
# 2026-06-18:決策由 wave_fade_daily.py(cron ~07:00、shioaji 唯讀數據金鑰抓 MXFR1 + dir_full 波浪、
# 政策B 同向跳)算、寫 data/wave_fade/next_signal.json;這支只在日盤開盤窗讀 side → 1 口真 tick 進場、
# -2% 引擎硬停、13:44 收盤強平。固定 1 口靠 RISK_PROFILE=fixed1_paper。headless、事件走 TG。
# 與 live/其他 paper 隔離(自己的 data/paper/wave_exec/)。需在 08:45 進場窗前啟動(cron pre-open ~08:22)。
# ⚠️ forward 候選非確認 edge → paper-only、不放大;占用第 5 條(最後一條)永豐連線。
# 精準 kill:PIDFILE + /proc/environ 確認 STRATEGY_OWNER=wave_exec,絕不 pkill -f。
set -u
cd /home/xx/TMFtrader-src
# 市場休市日不啟動(無行情→假連線中斷迴圈+TG洗版;2026-06-19 端午事故)。週末已由 cron Mon-Fri 排除。
if grep -qx "$(TZ=Asia/Taipei date +%F)" scripts/market_holidays.txt 2>/dev/null; then
  mkdir -p data/logs
  echo "[$(date '+%F %T')] $(TZ=Asia/Taipei date +%F) 市場休市 → 跳過 wave_exec 啟動" >> data/logs/holiday_skip.log
  exit 0
fi
# 清 stale bytecode(rsync 保留 mtime → Python 沿用舊 .pyc 跑舊碼,2026-06-05 事故根因)。
find . -name '*.pyc' -not -path './.venv/*' -delete 2>/dev/null || true
export TZ=Asia/Taipei
PY=.venv/bin/python3
PIDFILE=/tmp/TMFtrader_wave_exec_paper.pid

if [ -f "$PIDFILE" ]; then
  OLD=$(cat "$PIDFILE" 2>/dev/null || true)
  if [ -n "${OLD:-}" ] && kill -0 "$OLD" 2>/dev/null; then
    if tr '\0' ' ' < "/proc/$OLD/environ" 2>/dev/null | grep -q 'STRATEGY_OWNER=wave_exec'; then
      kill "$OLD" 2>/dev/null || true
      sleep 3
    fi
  fi
fi

export TRADING_MODE=paper
export INSTRUMENTS=MXF       # 小台(引擎自動對齊 pv50;preset 仍鎖 1 口)
export TIMEFRAME=30
export STRATEGY_TYPE=wave_exec
export STRATEGY_OWNER=wave_exec
export RISK_PROFILE=fixed1_paper
export RECORD_TICKS=0
export RECORD_DECISIONS=1   # 錄決策帶(snapshot/訊號)→ 日後 decision-tape 回測

LOG="data/logs/wave_exec_paper_$(date +%Y%m%d).log"
mkdir -p data/logs
nohup "$PY" scripts/start_paper.py --mode paper >> "$LOG" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PIDFILE"
echo "[$(date '+%F %T')] wave_exec PAPER start_paper.py PID=$NEW_PID | TF=30 owner=wave_exec INSTRUMENTS=MXF(小台) RISK=fixed1_paper 日盤開盤窗進1口/-2%硬停/13:44強平 (chips×波浪fade 政策B) headless(TG-only)" >> "$LOG"
