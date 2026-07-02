#!/bin/bash
# maxpain_exec LIVE(真錢)on MXF 小台 — 2026-07-02 由 paper 轉 live(user 指示)。
# 訊號/邏輯 100% 凍結 v2:maxpain_daily.py(cron 18:40)寫 next_signal.json → 本引擎
# t+1 開盤窗進第1口、+1% scale-in 加第2口、−2% tick 硬停、抱到週選結算日 13:30 強平。
# 多日持倉:POSITION_LOCK_STALE_HOURS=220 讓 live 鎖不會被 12h 殭屍判定誤刪(單池互斥:
# maxpain 持倉期間 breakout_v7 進場會被擋,反之亦然 — 設計如此)。
# what-if 影子(noTP/noSL 等 6 變體)照記 data/maxpain_v2/whatif.csv → maxpain_put paper 對帳不中斷。
# headless、事件走 TG。精準 kill:PIDFILE + /proc/environ 確認 owner,絕不 pkill -f。
set -u
cd /home/xx/TMFtrader-src
# 市場休市日不啟動(無行情→假連線中斷迴圈+TG洗版;2026-06-19 端午事故)。週末已由 cron Mon-Fri 排除。
if grep -qx "$(TZ=Asia/Taipei date +%F)" scripts/market_holidays.txt 2>/dev/null; then
  mkdir -p data/logs
  echo "[$(date '+%F %T')] $(TZ=Asia/Taipei date +%F) 市場休市 → 跳過 maxpain_exec live 啟動" >> data/logs/holiday_skip.log
  exit 0
fi
find . -name '*.pyc' -not -path './.venv/*' -delete 2>/dev/null || true
export TZ=Asia/Taipei
PY=.venv/bin/python3
PIDFILE=/tmp/TMFtrader_maxpain_exec_live.pid

if [ -f "$PIDFILE" ]; then
  OLD=$(cat "$PIDFILE" 2>/dev/null || true)
  if [ -n "${OLD:-}" ] && kill -0 "$OLD" 2>/dev/null; then
    if tr '\0' ' ' < "/proc/$OLD/environ" 2>/dev/null | grep -q 'STRATEGY_OWNER=maxpain_exec'; then
      kill "$OLD" 2>/dev/null || true
      sleep 3
    fi
  fi
fi
# 防雙開:若舊 paper 版還活著(pidfile 不同)一併收掉 — 同 owner 兩個 process 會互踩
# whatif_pending.json/訊號消耗;live 版取代 paper 版(2026-07-02 轉 live)。
OLD_PAPER_PID=$(cat /tmp/TMFtrader_maxpain_exec_paper.pid 2>/dev/null || true)
if [ -n "${OLD_PAPER_PID:-}" ] && kill -0 "$OLD_PAPER_PID" 2>/dev/null; then
  if tr '\0' ' ' < "/proc/$OLD_PAPER_PID/environ" 2>/dev/null | grep -q 'STRATEGY_OWNER=maxpain_exec'; then
    kill "$OLD_PAPER_PID" 2>/dev/null || true
    rm -f /tmp/TMFtrader_maxpain_exec_paper.pid
    sleep 2
  fi
fi

export TRADING_MODE=live
export INSTRUMENTS=MXF
export TIMEFRAME=30
export STRATEGY_TYPE=maxpain_exec
export STRATEGY_OWNER=maxpain_exec
export RISK_PROFILE=fixed1_live
export RECORD_TICKS=0
export RECORD_DECISIONS=1
export POSITION_LOCK_STALE_HOURS=220
# 執行線=put 版(2026-07-02 user 定案):put 即地板 → 進場後移除 −2% 硬停(策略 check_exit 清 stop)、
# 進場需 put watcher 心跳(fail-closed,沒 put 不進裸倉)。無止盈、抱到結算;what-if 影子照記(noTP=v2 口徑)。
export MAXPAIN_TRAIL_PCT=0.0
export MAXPAIN_PUT_PROTECT=1

LOG="data/logs/maxpain_exec_live_$(date +%Y%m%d).log"
mkdir -p data/logs
nohup "$PY" scripts/start_paper.py --mode live >> "$LOG" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PIDFILE"
echo "[$(date '+%F %T')] maxpain_exec LIVE MXF PID=$NEW_PID | TF=30 owner=maxpain_exec RISK=fixed1_live 完整2口:t+1進/+1%加碼/-2%硬停/結算13:30強平 lock_stale=220h 真實下單" >> "$LOG"

if [ -f .env ]; then
  TG_TOKEN=$(grep '^TG_BOT_TOKEN=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")
  TG_CHAT=$(grep '^TG_CHAT_ID=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")
  [ -n "$TG_TOKEN" ] && [ -n "$TG_CHAT" ] && \
    curl -sS --max-time 10 -o /dev/null \
      "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${TG_CHAT}" \
      --data-urlencode "text=[LIVE] maxpain_exec 真錢引擎啟動 (MXF 2口制/-2%硬停/抱結算, PID ${NEW_PID})"
fi
