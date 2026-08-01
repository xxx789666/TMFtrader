#!/bin/bash
# 啟動 A_struct(日內早盤波浪 0-1-2 做多 + 日線 EMA250 牛熊濾網 / 引擎真 tick paper / MXF 小台 / 5m)。
# lab 交接 2026-06-22:盤中即時策略——08:45–09:45 用 5m K 即時跑因果 zigzag、浪2 確認當根收盤市價做多 1 口,
# 結構停利=浪2+1.618×L1 / 結構停損=浪2×(1−0.15%)(引擎 tick 級硬停硬利)、13:45 收盤強平、不過夜。
# 牛熊前置濾讀 data/taiex_daily.csv(cron 14:30 抓);固定 1 口靠 RISK_PROFILE=fixed1_paper。headless、事件走 TG。
# 與 live/其他 paper 隔離(自己的 data/paper/astruct/)。須在 08:45 進場窗前啟動+暖身 → cron pre-open ~08:25。
# ⚠️ forward 候選非確認 edge(standalone Sharpe~0.47/MaxDD−22%、2020+ 無 OOS)→ paper-only、不放大、sleeve 優先。
# ⚠️ 連線:占第 5 條永豐連線(night_v7 已暫停才有空位;若 night_v7 復活需先讓一條)。
# 精準 kill:PIDFILE + /proc/environ 確認 STRATEGY_OWNER=astruct,絕不 pkill -f。
set -u
cd /home/xx/TMFtrader-src
# 市場休市日不啟動(無行情→假連線中斷迴圈+TG洗版;2026-06-19 端午事故)。週末已由 cron Mon-Fri 排除。
if grep -qx "$(TZ=Asia/Taipei date +%F)" scripts/market_holidays.txt 2>/dev/null; then
  mkdir -p data/logs
  echo "[$(date '+%F %T')] $(TZ=Asia/Taipei date +%F) 市場休市 → 跳過 astruct 啟動" >> data/logs/holiday_skip.log
  exit 0
fi
# 清 stale bytecode(rsync 保留 mtime → Python 沿用舊 .pyc 跑舊碼,2026-06-05 事故根因)。
find . -name '*.pyc' -not -path './.venv/*' -delete 2>/dev/null || true
export TZ=Asia/Taipei
PY=.venv/bin/python3
PIDFILE=/tmp/TMFtrader_astruct_paper.pid

if [ -f "$PIDFILE" ]; then
  OLD=$(cat "$PIDFILE" 2>/dev/null || true)
  if [ -n "${OLD:-}" ] && kill -0 "$OLD" 2>/dev/null; then
    if tr '\0' ' ' < "/proc/$OLD/environ" 2>/dev/null | grep -q 'STRATEGY_OWNER=astruct'; then
      kill "$OLD" 2>/dev/null || true
      sleep 3
    fi
  fi
fi

export TRADING_MODE=paper
export INSTRUMENTS=MXF       # 小台(引擎自動對齊 pv50;preset 仍鎖 1 口)
export TIMEFRAME=5           # 5m K → 早盤波浪偵測剛需
export STRATEGY_TYPE=astruct
export STRATEGY_OWNER=astruct
export RISK_PROFILE=fixed1_paper
export ASTRUCT_EMA_SPAN=250
export RECORD_TICKS=0
export RECORD_DECISIONS=1   # 錄決策帶(snapshot/OR/訊號)→ 盤後與 lab 同日 1m/5m 回放逐根對齊(驗即時偵測一致)

LOG="data/logs/astruct_paper_$(date +%Y%m%d).log"
mkdir -p data/logs
nohup "$PY" scripts/start_paper.py --mode paper >> "$LOG" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PIDFILE"
echo "[$(date '+%F %T')] astruct PAPER start_paper.py PID=$NEW_PID | TF=5 owner=astruct INSTRUMENTS=MXF(小台) RISK=fixed1_paper EMA250濾 08:45-09:45偵測浪0-1-2/浪2確認進1口/結構SL-TP/13:45強平 headless(TG-only)" >> "$LOG"

if [ -f .env ]; then
  TG_TOKEN=$(grep '^TG_BOT_TOKEN=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")
  TG_CHAT=$(grep '^TG_CHAT_ID=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")
  [ -n "$TG_TOKEN" ] && [ -n "$TG_CHAT" ] && \
    curl -sS --max-time 10 -o /dev/null \
      "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${TG_CHAT}" \
      --data-urlencode "text=🌅 [Cron] A_struct PAPER 啟動 PID=$NEW_PID (MXF/小台/TF5/早盤波浪+EMA250濾)
時間: $(date '+%F %H:%M:%S')" 2>/dev/null || true
fi
