#!/bin/bash
# 啟動 breakout_v7 (BreakoutDualSlope / 日盤 5m 趨勢) PAPER 前推 —— 與 live 及其他 paper 完全隔離。
#
# 隔離手段:
#   - 入口用 scripts/start_paper.py(cmdline 不含 'scripts/start.py')→ 不被 live 的
#     restart_day.sh(pkill -f 'scripts/start.py')/ watchdog 波及。
#   - 無 dashboard(headless):省記憶體;進出場/止損/止盈/追蹤/盤末強平等事件走 TG。
#   - PIDFILE 精準 kill 自己(殺前用 /proc/<pid>/environ 確認帶 STRATEGY_TYPE=breakout_v7),絕不 pkill -f。
#   - 獨立 log:data/logs/breakout_v7_paper_YYYYMMDD.log。
#   - paper 持倉鎖走 data/active_position_paper.json(mode-scoped),不碰 live 的 active_position.json。
#
# 排程:交易日 08:42 TST(cron UTC 00:42,晚 live 日盤 restart 與 day_orb paper 各 2 分、錯開 fetch_contracts;
#       仍早於 08:45 日盤開盤)。注意:breakout_v7 與 live breakout 同為日盤 5m 趨勢,此為 paper A/B 對照。
# 注意:paper = 真實行情、不下單,但仍會 Shioaji login(佔 1 連線)。

set -u
cd /home/xx/TMFtrader-src
export TZ=Asia/Taipei
PY=.venv/bin/python3   # 明確用 venv python(不靠 activate/PATH,避免 cron 環境抓到系統 python)

PIDFILE=/tmp/TMFtrader_breakout_v7_paper.pid

if [ -f "$PIDFILE" ]; then
  OLD=$(cat "$PIDFILE" 2>/dev/null || true)
  if [ -n "${OLD:-}" ] && kill -0 "$OLD" 2>/dev/null; then
    if tr '\0' ' ' < "/proc/$OLD/environ" 2>/dev/null | grep -q 'STRATEGY_TYPE=breakout_v7'; then
      kill "$OLD" 2>/dev/null || true
      sleep 3
    fi
  fi
fi

export TRADING_MODE=paper
export INSTRUMENTS=TMF
export TIMEFRAME=5
export STRATEGY_TYPE=breakout_v7
export STRATEGY_OWNER=breakout_v7
export RECORD_TICKS=0   # paper 不錄 tick(live 已錄、避免寫進同一個 data/ticks 檔)

LOG="data/logs/breakout_v7_paper_$(date +%Y%m%d).log"
mkdir -p data/logs
nohup "$PY" scripts/start_paper.py --mode paper >> "$LOG" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PIDFILE"
echo "[$(date '+%F %T')] breakout_v7 PAPER start_paper.py PID=$NEW_PID | TIMEFRAME=5 owner=breakout_v7 headless(TG-only)" >> "$LOG"
