#!/bin/bash
# 啟動 day_orb (fade / 30m) PAPER 前推進程 —— 與 live 日盤 breakout 完全隔離。
#
# 隔離手段(重要,避免與 live 互相干擾):
#   - 入口用 scripts/start_paper.py(cmdline 不含 'scripts/start.py')→ 不被 live 的
#     restart_day.sh(pkill -f 'scripts/start.py')殺掉、不被 watchdog_alert.sh 誤判。
#   - 無 dashboard(headless):省記憶體;進出場/止損/止盈/追蹤/盤末強平等事件走 TG。
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
PY=.venv/bin/python3   # 明確用 venv python(不靠 activate/PATH,避免 cron 環境抓到系統 python)

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
export RECORD_TICKS=0   # paper 不錄 tick(live 已錄、避免寫進同一個 data/ticks 檔)

LOG="data/logs/day_orb_paper_$(date +%Y%m%d).log"
mkdir -p data/logs
nohup "$PY" scripts/start_paper.py --mode paper >> "$LOG" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PIDFILE"
echo "[$(date '+%F %T')] day_orb PAPER start_paper.py PID=$NEW_PID | TIMEFRAME=30 owner=day_orb mode=fade headless(TG-only)" >> "$LOG"
