#!/bin/bash
# 週一(2026-06-08)早上 07:00 TST 推 TG 提醒:跑 MXF cutover。
# 一次性 cron:`0 23 7 6 * /home/xx/TMFtrader-src/scripts/monday_golive_reminder.sh`
#   (= 06-07 23:00 UTC = 06-08 07:00 TST,只會在那一刻觸發一次)
# 不自刪 crontab(避開自刪 race 地雷);觸發過就讓它躺著、今年內不會再響。
set -u
cd /home/xx/TMFtrader-src
TG_TOKEN=$(grep '^TG_BOT_TOKEN=' .env 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
TG_CHAT=$(grep '^TG_CHAT_ID=' .env 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
[ -z "$TG_TOKEN" ] || [ -z "$TG_CHAT" ] && exit 0

MSG="🔔 [週一上線提醒] 6/8
平窗 05:00–08:30 內跑:
CONFIRM_MXF_CUTOVER=YES bash scripts/cutover_to_mxf.sh --apply

驗收:✅權益≥60万 / day_v7 point_value=50 / 4 進程起
組合:day_v7+night_v7 = live MXF;day_orb+aft_orb = paper(錄 decision)
詳見 docs/上線計劃與待辦_2026_06_08.md
(或回我「跑週一 cutover」,我平窗幫你跑+驗收)"

curl -sS --max-time 10 -o /dev/null \
  "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${TG_CHAT}" --data-urlencode "text=${MSG}" 2>/dev/null || true
