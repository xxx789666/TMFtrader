#!/bin/bash
# 啟動 night_v3 (NightORB breakout / 60m) PAPER 前推進程 —— 與 live 及 day_orb paper 完全隔離。
#
# 隔離手段:
#   - 入口用 scripts/start_paper.py(cmdline 不含 'scripts/start.py')→ 不被 live 的
#     restart_day.sh(pkill -f 'scripts/start.py')/ watchdog 波及。檔名也不含 'night_orb.py',
#     不會被 crontab 的 pkill -f 'night_orb.py' 或 watchdog 的 pgrep -f 'night_orb.py' 誤殺/誤判
#     (那是舊夜盤 ORB 用的;舊 ORB 目前暫停、暗著)。
#   - Dashboard 用獨立 port 8891(live=8888、day_orb paper=8890)。
#   - PIDFILE 精準 kill 自己(殺前用 /proc/<pid>/environ 確認帶 STRATEGY_TYPE=night_v3),絕不 pkill -f。
#   - 獨立 log:data/logs/night_v3_paper_YYYYMMDD.log。
#   - paper 持倉鎖走 data/active_position_paper.json(mode-scoped),不碰 live 的 active_position.json。
#
# 排程:交易日 14:50 TST(cron UTC 06:50,晚於舊 restart_night 的 06:55、且早於 15:00 夜盤開盤)。
# 進程自管夜盤 session(15:00 開、04:55 強平),跨午夜歸同一夜。
# 注意:paper 仍會 Shioaji login(佔 1 連線);夜盤薄流動性/滑價/斷線/04:55 強平等 live 摩擦回測未含。

set -u
cd /home/xx/TMFtrader-src
export TZ=Asia/Taipei
PY=.venv/bin/python3   # 明確用 venv python(不靠 activate/PATH,避免 cron 環境抓到系統 python)

PIDFILE=/tmp/TMFtrader_night_v3_paper.pid

if [ -f "$PIDFILE" ]; then
  OLD=$(cat "$PIDFILE" 2>/dev/null || true)
  if [ -n "${OLD:-}" ] && kill -0 "$OLD" 2>/dev/null; then
    if tr '\0' ' ' < "/proc/$OLD/environ" 2>/dev/null | grep -q 'STRATEGY_TYPE=night_v3'; then
      kill "$OLD" 2>/dev/null || true
      sleep 3
    fi
  fi
fi

export TRADING_MODE=paper
export INSTRUMENTS=TMF
export TIMEFRAME=60
export STRATEGY_TYPE=night_v3
export STRATEGY_OWNER=night_v3

LOG="data/logs/night_v3_paper_$(date +%Y%m%d).log"
mkdir -p data/logs
nohup "$PY" scripts/start_paper.py --no-browser --mode paper --port 8891 >> "$LOG" 2>&1 &
NEW_PID=$!
echo "$NEW_PID" > "$PIDFILE"
echo "[$(date '+%F %T')] night_v3 PAPER start_paper.py PID=$NEW_PID | TIMEFRAME=60 owner=night_v3 mode=breakout port=8891" >> "$LOG"
