#!/bin/bash
# 啟動 day_orb (fade / 30m) PAPER 前推進程 —— 與 live 日盤 breakout 完全隔離。
#
# 隔離手段(重要,避免誤殺 live):
#   - 用 PIDFILE 精準 kill 自己上一輪,**絕不** pkill -f 'scripts/start.py'
#     (那會把 live 日盤/夜盤的 start.py 一起殺掉)。
#   - 殺之前再用 /proc/<pid>/environ 確認該 PID 真的帶 STRATEGY_TYPE=day_orb 才動手。
#   - 獨立 log:data/logs/day_orb_paper_YYYYMMDD.log。
#   - 純用 env 覆寫 strategy/owner/timeframe/mode,不改共用 .env、不改 INSTRUMENT_SPECS
#     (engine load_dotenv(override=False) → 這裡 export 的值會勝出,live 的 .env 不受影響)。
#
# 排程建議:交易日 08:35 啟動(晚於 live 日盤、錯開 fetch_contracts 尖峰),進程自管日盤 session。
# 注意:paper = 真實行情、不下單,但仍會 Shioaji login(佔 1 連線,上限 5/身分證)+ 拉行情。

set -u
cd /home/xx/TMFtrader-src
export TZ=Asia/Taipei
source .venv/bin/activate

PIDFILE=/tmp/TMFtrader_day_orb_paper.pid

# 只殺自己上一輪(若還活著且確認是 day_orb paper),不碰 live 的 start.py
if [ -f "$PIDFILE" ]; then
  OLD=$(cat "$PIDFILE" 2>/dev/null || true)
  if [ -n "${OLD:-}" ] && kill -0 "$OLD" 2>/dev/null; then
    if tr '\0' ' ' < "/proc/$OLD/environ" 2>/dev/null | grep -q 'STRATEGY_TYPE=day_orb'; then
      kill "$OLD" 2>/dev/null || true
      sleep 3
    fi
  fi
fi

# 前推用 env 覆寫(不改 .env / spec)
export TRADING_MODE=paper
export INSTRUMENTS=TMF
export TIMEFRAME=30
export STRATEGY_TYPE=day_orb
export STRATEGY_OWNER=day_orb

LOG="data/logs/day_orb_paper_$(date +%Y%m%d).log"
mkdir -p data/logs
nohup python3.12 scripts/start.py --no-browser >> "$LOG" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PIDFILE"
echo "[$(date '+%F %T')] day_orb PAPER start.py PID=$NEW_PID | TIMEFRAME=30 owner=day_orb mode=fade" >> "$LOG"
