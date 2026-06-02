#!/bin/bash
# Cutover:現行單支 live breakout(start.py) → 三支 TMF live(breakout_v7/day_orb/night_v3,headless)。
#
# 預設 DRY-RUN(只印計劃、零動作)。真正執行需三道閘同時成立:
#   ① 旗標 --apply   ② 環境 CONFIRM_LIVE_CUTOVER=YES   ③ 現在落在 05:00-08:30 平窗(盤平、無倉)
# idempotent:重跑安全(crontab 用 temp-file edit 非 pipe、不會踩自刪 race;已存在的行不重複加)。
#
# 動作:
#   [1] 停舊 live breakout(scripts/start.py)
#   [2] 停 3 支 paper(start_paper.py --mode paper)— 釋放 Shioaji 連線(永豐 5/身分證;3live+3paper=6 會爆)
#   [3] crontab:移除 restart_day.sh + 3 支 *_paper.sh 行;新增 3 支 *_live.sh 行
#   [4] 立即啟動日盤兩支 live(v7/day_orb);night_v3 交給 14:50 cron(或手動)
#
# 人扣扳機:本腳本不自動觸發。監測(verify_fixes_0605)只通知、由人判斷後執行。
set -u
cd /home/xx/TMFtrader-src
export TZ=Asia/Taipei
APPLY=0; [ "${1:-}" = "--apply" ] && APPLY=1
NOW_HM=$(date +%H%M); NOW=$(date '+%F %T')

REMOVE_PAT='restart_day.sh|start_breakout_v7_paper.sh|start_day_orb_paper.sh|start_night_v3_paper.sh'
ADD_LINES=(
"15 0 * * 1-5 /home/xx/TMFtrader-src/scripts/start_breakout_v7_live.sh"
"16 0 * * 1-5 /home/xx/TMFtrader-src/scripts/start_day_orb_live.sh"
"50 6 * * 1-5 /home/xx/TMFtrader-src/scripts/start_night_v3_live.sh"
)

echo "== Cutover 計劃 @ $NOW =="
echo "[1] 停舊 live breakout : pkill -f 'scripts/start.py'"
echo "[2] 停 3 支 paper      : pkill -f 'start_paper.py --mode paper'(釋放連線)"
echo "[3] crontab 移除(含)   : $REMOVE_PAT"
echo "    crontab 新增(若無) :"; printf '      %s\n' "${ADD_LINES[@]}"
echo "[4] 立即起 v7/day_orb live(night_v3 交給 14:50 cron)"
echo
echo "-- 現況:start.py(舊 breakout) --"; ps -eo pid,cmd | grep 'scripts/start.py' | grep -v grep || echo "  (無)"
echo "-- 現況:paper 進程 --"; ps -eo pid,cmd | grep 'start_paper.py --mode paper' | grep -v grep || echo "  (無)"
echo "-- 現況:crontab 相關行 --"; crontab -l 2>/dev/null | grep -E "restart_day|_paper.sh|_live.sh" || echo "  (無)"
echo

if [ "$APPLY" != 1 ]; then
  echo ">> DRY-RUN(預設、零動作)。確認後在平窗執行:"
  echo "   CONFIRM_LIVE_CUTOVER=YES bash scripts/cutover_to_3strategy_live.sh --apply"
  exit 0
fi

# ---- APPLY 三道閘 ----
if [ "${CONFIRM_LIVE_CUTOVER:-}" != "YES" ]; then
  echo "!! 缺 CONFIRM_LIVE_CUTOVER=YES(真實 live 切換確認)。中止。"; exit 1; fi
if [ "$NOW_HM" -lt 0500 ] || [ "$NOW_HM" -gt 0830 ]; then
  echo "!! 現在 $NOW_HM 不在 05:00-08:30 平窗(避免盤中/持倉中切換)。中止。"; exit 1; fi
# 平窗仍須確認真的無持倉(避免切換時 live 帳戶有單)
if [ -f data/active_position.json ]; then
  echo "!! data/active_position.json 存在(疑似有 live 持倉)。先確認平倉再切。中止。"; exit 1; fi

echo ">> APPLYING ..."
pkill -f 'scripts/start.py' 2>/dev/null && echo "  [1] killed old breakout(start.py)" || echo "  [1] (無舊 start.py)"
pkill -f 'start_paper.py --mode paper' 2>/dev/null && echo "  [2] killed paper 進程" || echo "  [2] (無 paper)"
sleep 3
TMP=/tmp/ct_cutover_$$.txt
crontab -l 2>/dev/null | grep -vE "$REMOVE_PAT" > "$TMP"
for line in "${ADD_LINES[@]}"; do grep -qF "$line" "$TMP" || echo "$line" >> "$TMP"; done
crontab "$TMP"; rm -f "$TMP"
echo "  [3] crontab 已更新"
bash scripts/start_breakout_v7_live.sh
bash scripts/start_day_orb_live.sh
echo "  [4] 已起 v7/day_orb live"
sleep 4
echo
echo "== 切換後 =="
echo "-- crontab --"; crontab -l | grep -E "restart_day|_paper.sh|_live.sh"
echo "-- live 進程 --"; ps -eo pid,etimes,cmd | grep 'start_paper.py --mode live' | grep -v grep
echo
echo "Rollback:pkill -f 'start_paper.py --mode live';crontab 移除 *_live.sh、加回 paper 三行 + '35 0 * * 1-5 .../restart_day.sh';跑 restart_day.sh + 3 支 *_paper.sh。"
