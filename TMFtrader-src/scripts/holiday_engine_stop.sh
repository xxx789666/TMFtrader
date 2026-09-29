#!/bin/bash
# 假日晨間自動停機(2026-06-20 建,端午事故後)。
# 問題:launcher 休市 guard 只擋「當天 cron 重啟」,擋不到「前一交易日啟動、24h 還在跑」的引擎
#       —— 它們在假日 08:45 無行情時會 reconnect 迴圈洗版 TG。
# 解法:休市日把殘留的 5 支引擎 owner-verified 停掉 + 清 PID 檔(否則 watchdog 會誤報 down)。
#       非休市日 = no-op(self-guard,絕不在交易日動引擎)。
# 排程:cron 每日 00:05 UTC(08:05 TST)—— 夜盤 05:00 收後、08:45 日盤迴圈前、launcher 08:15 前。
# 配合 launcher 休市 guard(擋重啟)+ watchdog 休市 guard(不誤報)= 假日全自動、免手動。
set -u
cd /home/xx/TMFtrader-src || exit 0
export TZ=Asia/Taipei
TODAY=$(date +%F)
grep -qx "$TODAY" scripts/market_holidays.txt 2>/dev/null || exit 0   # 非休市日 → 不動

mkdir -p data/logs
LOG=data/logs/holiday_skip.log
killed=""
# 第一輪 SIGTERM(owner-verified:比對 /proc/PID/environ,絕不 pkill -f)
for own in night_v7 breakout_v7 chips_exec maxpain_exec wave_exec; do
  for p in $(pgrep -f start_paper.py; pgrep -f scripts/start.py); do
    if tr '\0' ' ' < "/proc/$p/environ" 2>/dev/null | grep -q "STRATEGY_OWNER=$own"; then
      kill "$p" 2>/dev/null && killed="$killed $own($p)"
    fi
  done
done
sleep 4
# 第二輪 SIGKILL 殘存
for own in night_v7 breakout_v7 chips_exec maxpain_exec wave_exec; do
  for p in $(pgrep -f start_paper.py; pgrep -f scripts/start.py); do
    tr '\0' ' ' < "/proc/$p/environ" 2>/dev/null | grep -q "STRATEGY_OWNER=$own" && kill -9 "$p" 2>/dev/null
  done
done
rm -f /tmp/TMFtrader_*.pid   # 清 PID 檔:watchdog「無 PID 檔=尚未啟動、跳過」→ 不誤報 down
echo "[$(date '+%F %T')] 休市自動停機:killed=${killed:- 無殘留}、已清 PID 檔" >> "$LOG"

# TG 通知(優雅降級:拿不到 token 就只寫 log)
TG_TOKEN=$(grep '^TG_BOT_TOKEN=' .env 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
TG_CHAT=$(grep '^TG_CHAT_ID=' .env 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
[ -n "${TG_TOKEN:-}" ] && [ -n "${TG_CHAT:-}" ] && \
  curl -sS --max-time 10 -o /dev/null "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TG_CHAT}" \
    --data-urlencode "text=🎏 [Holiday] $TODAY 休市,已自動停機並清 PID 檔(停:${killed:- 無殘留})。週一/下個交易日 cron 恢復。" 2>/dev/null || true
