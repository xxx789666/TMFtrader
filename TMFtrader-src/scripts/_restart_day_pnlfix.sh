#!/usr/bin/env bash
# 一次性 14:00 TST 重啟 start.py 套用 PnL 稅修正(2026-05-28、commit a95ae44)
export TZ=Asia/Taipei
PROJ=/home/xx/TMFtrader-src
cd "$PROJ"
# 日期鎖：只今天跑
[ "$(date +%F)" = "2026-05-28" ] || exit 0
# 自刪 cron 行(保證只跑一次)
# 安全自刪：先把 crontab 讀進變數，確認非空才回寫，避免讀空把整張表清掉
_ct=$(crontab -l 2>/dev/null)
if [ -n "$_ct" ]; then
  printf '%s\n' "$_ct" | grep -v '_restart_day_pnlfix.sh' | crontab - 2>/dev/null || true
fi
LOG="data/logs/cron.log"
{
  echo "==== 14:00 day restart for PnL fix $(date '+%F %T') ===="
  # 安全:查持倉、有單就中止
  QTY=$(curl -sS --max-time 5 http://localhost:8888/api/state 2>/dev/null | \
    python3 -c "import json,sys;d=json.load(sys.stdin);print((d.get('position') or {}).get('quantity', 0))" 2>/dev/null || echo "?")
  echo "current position qty = $QTY"
  if [ "$QTY" != "0" ] && [ "$QTY" != "?" ] && [ -n "$QTY" ]; then
    echo "X 有持倉、中止重啟、改人工確認"
    if [ -f .env ]; then
      TG_TOKEN=$(grep '^TG_BOT_TOKEN=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")
      TG_CHAT=$(grep '^TG_CHAT_ID=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")
      [ -n "$TG_TOKEN" ] && curl -sS --max-time 10 -o /dev/null \
        "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
        --data-urlencode "chat_id=${TG_CHAT}" \
        --data-urlencode "text=14:00 day restart 中止：仍有持倉 qty=$QTY，需人工確認"
    fi
    exit 0
  fi
  bash "$PROJ/scripts/restart_day.sh"
  echo "restart_day.sh 完成 $(date '+%F %T')"
} >> "$LOG" 2>&1
