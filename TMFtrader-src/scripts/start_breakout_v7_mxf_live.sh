#!/bin/bash
# breakout_v7 LIVE on MXF(小台 50元/點)。2026-06-08 起 4 支由 TMF 轉 MXF。
# point_value=50 / max_loss_twd=20000 由 engine 依 instrument_config MXF spec 自動對齊(launcher 只設 INSTRUMENTS=MXF)。
# 沿用同一 PID 檔 /tmp/TMFtrader_breakout_v7_live.pid → cutover 換手乾淨 + watchdog 不用改。
# headless、事件走 TG。精準 kill:PIDFILE + /proc/environ 確認 owner,絕不 pkill -f。
set -u
cd /home/xx/TMFtrader-src
# 市場休市日不啟動(無行情→假連線中斷迴圈+TG洗版;2026-06-19 端午事故)。週末已由 cron Mon-Fri 排除。
if grep -qx "$(TZ=Asia/Taipei date +%F)" scripts/market_holidays.txt 2>/dev/null; then
  mkdir -p data/logs
  echo "[$(date '+%F %T')] $(TZ=Asia/Taipei date +%F) 市場休市 → 跳過 breakout_v7 啟動" >> data/logs/holiday_skip.log
  exit 0
fi
find . -name '*.pyc' -not -path './.venv/*' -delete 2>/dev/null || true
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
export INSTRUMENTS=MXF
export TIMEFRAME=5
export STRATEGY_TYPE=breakout_v7
export STRATEGY_OWNER=breakout_v7
export RECORD_TICKS=1
export RECORD_DECISIONS=1   # 錄決策帶(snapshot/OR/訊號)→ 日後回測逐筆對齊

LOG="data/logs/breakout_v7_mxf_live_$(date +%Y%m%d).log"
mkdir -p data/logs
nohup "$PY" scripts/start_paper.py --mode live >> "$LOG" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PIDFILE"
echo "[$(date '+%F %T')] breakout_v7 LIVE MXF PID=$NEW_PID | TF=5 owner=breakout_v7 INSTRUMENTS=MXF(pv50/maxloss20000自動) headless 真實下單" >> "$LOG"

if [ -f .env ]; then
  TG_TOKEN=$(grep '^TG_BOT_TOKEN=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")
  TG_CHAT=$(grep '^TG_CHAT_ID=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")
  [ -n "$TG_TOKEN" ] && [ -n "$TG_CHAT" ] && \
    curl -sS --max-time 10 -o /dev/null \
      "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${TG_CHAT}" \
      --data-urlencode "text=🔄 [Cron] breakout_v7 LIVE 重啟 PID=$NEW_PID (MXF/小台/TF5)
時間: $(date '+%F %H:%M:%S')" 2>/dev/null || true
fi
