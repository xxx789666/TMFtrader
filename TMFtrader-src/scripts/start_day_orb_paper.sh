#!/bin/bash
# 啟動 day_orb (fade / 30m) PAPER 前推進程 —— 與 live 日盤 breakout 完全隔離。
#
# 隔離手段(重要,避免與 live 互相干擾):
#   - 入口用 scripts/start_paper.py(cmdline 不含 'scripts/start.py')→ 不被 live 的
#     restart_day.sh(pkill -f 'scripts/start.py')殺掉、不被 watchdog_alert.sh 誤判。
#   - Dashboard 用獨立 port 8890(live 是 8888)→ 不撞埠。
#   - 用 PIDFILE 精準 kill 自己上一輪(殺前再用 /proc/<pid>/environ 確認帶
#     STRATEGY_TYPE=day_orb 才動手),**絕不** pkill -f。
#   - 獨立 log:data/logs/day_orb_paper_YYYYMMDD.log。
#   - 純用 env 覆寫 strategy/owner/timeframe/mode/port,不改共用 .env、不改 INSTRUMENT_SPECS
#     (engine load_dotenv(override=False) → 這裡的值會勝出,live 的 .env 不受影響)。
#
# 排程:交易日 08:40 TST(cron UTC 00:40,晚 live 日盤重啟 5 分鐘、錯開 fetch_contracts)。
# 注意:paper = 真實行情、不下單,但仍會 Shioaji login(佔 1 連線,上限 5/身分證)。

set -u
cd /home/xx/TMFtrader-src
export TZ=Asia/Taipei
source .venv/bin/activate

PIDFILE=/tmp/TMFtrader_day_orb_paper.pid

# 只殺自己上一輪(若還活著且確認是 day_orb paper),不碰 live
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
nohup python3.12 scripts/start_paper.py --no-browser --mode paper --port 8890 >> "$LOG" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PIDFILE"
echo "[$(date '+%F %T')] day_orb PAPER start_paper.py PID=$NEW_PID | TIMEFRAME=30 owner=day_orb mode=fade port=8890" >> "$LOG"
