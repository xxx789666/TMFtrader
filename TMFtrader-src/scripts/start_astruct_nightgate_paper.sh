#!/bin/bash
# 啟動 A_struct 夜盤閘門法(引擎真 tick paper / MXF 小台 / 1m)。2026-06-23 取代逆勢 bug 版 astruct。
# 偵測/閘門/破壞位/人工 Discord 審核在本機 lab 管線(Astruct_daily.py + Discord + Astruct_bridge.py 推決定);
# 這支只執行:讀 data/astruct_nightgate/next_signal.json → 08:45 即時開盤判閘門 → 過則 first_wave_anchored
# (浪0=08:45低錨)真 tick 進 1 口 → 結構 TP/SL 引擎 tick 硬停 → 13:45 強平、不過夜。固定 1 口=fixed1_paper。
# 須在 08:45 進場窗前啟動+暖身 → cron pre-open ~08:25。headless、事件走 TG。
# ⚠️ forward 候選非確認 edge、人工為主、無 OOS → paper-only、不放大。占第 5 條永豐連線(與 night_v7 互斥)。
# 精準 kill:PIDFILE + /proc/environ 確認 STRATEGY_OWNER=astruct_nightgate,絕不 pkill -f。
set -u
cd /home/xx/TMFtrader-src
if grep -qx "$(TZ=Asia/Taipei date +%F)" scripts/market_holidays.txt 2>/dev/null; then
  mkdir -p data/logs
  echo "[$(date '+%F %T')] $(TZ=Asia/Taipei date +%F) 市場休市 → 跳過 astruct_nightgate 啟動" >> data/logs/holiday_skip.log
  exit 0
fi
find . -name '*.pyc' -not -path './.venv/*' -delete 2>/dev/null || true
export TZ=Asia/Taipei
PY=.venv/bin/python3
PIDFILE=/tmp/TMFtrader_astruct_nightgate_paper.pid

if [ -f "$PIDFILE" ]; then
  OLD=$(cat "$PIDFILE" 2>/dev/null || true)
  if [ -n "${OLD:-}" ] && kill -0 "$OLD" 2>/dev/null; then
    if tr '\0' ' ' < "/proc/$OLD/environ" 2>/dev/null | grep -q 'STRATEGY_OWNER=astruct_nightgate'; then
      kill "$OLD" 2>/dev/null || true
      sleep 3
    fi
  fi
fi

export TRADING_MODE=paper
export INSTRUMENTS=MXF       # 小台(引擎自動對齊 pv50;preset 仍鎖 1 口)
export TIMEFRAME=1           # 1m K → 對齊 lab 1分K 日內偵測
export STRATEGY_TYPE=astruct_nightgate
export STRATEGY_OWNER=astruct_nightgate
export RISK_PROFILE=fixed1_paper
export RECORD_TICKS=0
export RECORD_DECISIONS=1
# 根治 2026-06-30:astruct 是唯一需要「全程連續真 tick」做日內 0-1-2 偵測的引擎。
# Solace tick 偶發不送(今天 08:25 訂閱整天沒 tick → 偵測瞎掉、漏掉合格訊號)時,
# 全域停用的 KbarPoller 沒有 fallback → 整天瞎眼。只對本引擎開回 KbarPoller:
# Solace 斷時改用 REST snapshot 補真價,1分K 偵測不再瞎。其他引擎維持停用(省流量)。
export ENABLE_KBAR_POLLER=true

LOG="data/logs/astruct_nightgate_paper_$(date +%Y%m%d).log"
mkdir -p data/logs data/astruct_nightgate
nohup "$PY" scripts/start_paper.py --mode paper >> "$LOG" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PIDFILE"
echo "[$(date '+%F %T')] astruct_nightgate PAPER PID=$NEW_PID | TF=1 owner=astruct_nightgate MXF(小台) RISK=fixed1_paper 夜盤閘門+08:45低錨0-1-2 真tick/13:45強平 headless(TG-only)" >> "$LOG"

if [ -f .env ]; then
  TG_TOKEN=$(grep '^TG_BOT_TOKEN=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")
  TG_CHAT=$(grep '^TG_CHAT_ID=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")
  [ -n "$TG_TOKEN" ] && [ -n "$TG_CHAT" ] && \
    curl -sS --max-time 10 -o /dev/null \
      "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${TG_CHAT}" \
      --data-urlencode "text=🌙 [Cron] A_struct 夜盤閘門 PAPER 啟動 PID=$NEW_PID (MXF/TF1/真tick)
時間: $(date '+%F %H:%M:%S')" 2>/dev/null || true
fi
