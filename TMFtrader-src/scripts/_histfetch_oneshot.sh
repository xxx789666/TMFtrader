#!/usr/bin/env bash
export TZ=Asia/Taipei
PROJ=/home/xx/TMFtrader-src
cd "$PROJ"
# 只在指定日期跑（防未來週三 cron 誤觸）
[ "$(date +%F)" = "2026-05-27" ] || exit 0
# 先自刪本 cron 行（保證只跑一次）
# 安全自刪：先把 crontab 讀進變數，確認非空才回寫，避免讀空把整張表清掉
_ct=$(crontab -l 2>/dev/null)
if [ -n "$_ct" ]; then
  printf '%s\n' "$_ct" | grep -v '_histfetch_oneshot.sh' | crontab - 2>/dev/null || true
fi
PY=.venv/bin/python3.12
LOG="data/logs/histfetch_$(date +%Y%m%d).log"
{
  echo "================ histfetch one-shot start $(date '+%F %T %A') ================"
  echo "--- TMFR1 (微台, 2021-07 起) ---"
  timeout 1200 $PY scripts/fetch_history_kbars.py --contract TMFR1 --start 2021-07-01 \
      --history-budget-mb 420 --reserve-mb 70 --sleep 3
  echo "--- TXFR1 (大台, 2020-03-22 起) ---"
  timeout 1200 $PY scripts/fetch_history_kbars.py --contract TXFR1 --start 2020-03-22 \
      --history-budget-mb 420 --reserve-mb 70 --sleep 3
  echo "================ histfetch one-shot end $(date '+%F %T') ================"
} >> "$LOG" 2>&1
