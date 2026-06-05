#!/bin/bash
# Cutover:3 支 TMF live + aft_orb paper → 4 支 MXF live(小台 50元/點)。2026-06-08 起。
#
# 預設 DRY-RUN(只印計劃、零動作)。真正執行需三道閘同時成立:
#   ① 旗標 --apply  ② 環境 CONFIRM_MXF_CUTOVER=YES  ③ 現在落在無交易死區(05:00-08:30 或 13:45-14:50)且無倉
# 建議在「週一早上 05:00-08:30」平窗跑:日盤兩支起好趕在 08:45 開盤前;night_v3/aft_orb 交給 14:50/14:52 cron。
# idempotent:crontab 用 sed 改 launcher 檔名(已是 mxf 不重複改)+ temp-file edit(不踩自刪 race)。
#
# 動作:
#   [1] 停 3 支 TMF live(start_paper.py --mode live)+ aft_orb paper(--mode paper)
#   [2] crontab:sed 把 4 個 launcher 換成 *_mxf_live(v7/day_orb/night_v3 的 *_live.sh、aft 的 _paper.sh)
#   [3] 立即起日盤兩支 MXF live(v7/day_orb);night_v3/aft_orb 交給其 cron
#
# 人扣扳機:本腳本不自動觸發。MXF=5×真錢,確認保證金 60万到位、且這幾天 TMF live 乾淨後再切。
set -u
cd /home/xx/TMFtrader-src
export TZ=Asia/Taipei
APPLY=0; [ "${1:-}" = "--apply" ] && APPLY=1
NOW_HM=$(date +%H%M); NOW=$(date '+%F %T')

SED=(
  "s|start_breakout_v7_live.sh|start_breakout_v7_mxf_live.sh|"
  "s|start_day_orb_live.sh|start_day_orb_mxf_live.sh|"
  "s|start_night_v3_live.sh|start_night_v3_mxf_live.sh|"
  "s|start_aft_orb_paper.sh|start_aft_orb_mxf_live.sh|"
)

echo "== MXF Cutover 計劃 @ $NOW =="
echo "[1] 停 3 支 TMF live + aft_orb paper(釋放連線、避免 TMF/MXF 並存)"
echo "[2] crontab sed 換 launcher → *_mxf_live(4 支;idempotent)"
echo "[3] 立即起 v7/day_orb MXF live;night_v3(14:50)/aft_orb(14:52)交給 cron"
echo
echo "-- 現況:live 進程 --"; ps -eo pid,cmd | grep 'start_paper.py --mode live' | grep -v grep || echo "  (無)"
echo "-- 現況:paper 進程 --"; ps -eo pid,cmd | grep 'start_paper.py --mode paper' | grep -v grep || echo "  (無)"
echo "-- 現況:crontab 相關行 --"; crontab -l 2>/dev/null | grep -E "_live.sh|_paper.sh" || echo "  (無)"
echo "-- 持倉鎖 --"; [ -f data/active_position.json ] && cat data/active_position.json || echo "  (無倉、可切)"
echo

if [ "$APPLY" != 1 ]; then
  echo ">> DRY-RUN(預設、零動作)。確認後在平窗執行:"
  echo "   CONFIRM_MXF_CUTOVER=YES bash scripts/cutover_to_mxf.sh --apply"
  exit 0
fi

# ---- APPLY 三道閘 ----
if [ "${CONFIRM_MXF_CUTOVER:-}" != "YES" ]; then
  echo "!! 缺 CONFIRM_MXF_CUTOVER=YES(真實 MXF 切換確認)。中止。"; exit 1; fi
HM=$((10#$NOW_HM))
if ! { { [ "$HM" -ge 500 ] && [ "$HM" -le 830 ]; } || { [ "$HM" -ge 1345 ] && [ "$HM" -le 1450 ]; }; }; then
  echo "!! 現在 $NOW_HM 不在平窗(05:00-08:30 或 13:45-14:50)。中止。"; exit 1; fi
if [ -f data/active_position.json ]; then
  echo "!! data/active_position.json 存在(疑似有 live 持倉)。先平倉再切。中止。"; exit 1; fi

echo ">> APPLYING ..."
pkill -f 'start_paper.py --mode live' 2>/dev/null && echo "  [1a] killed TMF live 進程" || echo "  [1a] (無 live)"
pkill -f 'start_paper.py --mode paper' 2>/dev/null && echo "  [1b] killed aft_orb paper" || echo "  [1b] (無 paper)"
sleep 3
TMP=/tmp/ct_mxf_$$.txt
crontab -l 2>/dev/null > "$TMP"
for s in "${SED[@]}"; do sed -i "$s" "$TMP"; done
crontab "$TMP"; rm -f "$TMP"
echo "  [2] crontab 已換成 *_mxf_live"
bash scripts/start_breakout_v7_mxf_live.sh
bash scripts/start_day_orb_mxf_live.sh
echo "  [3] 已起 v7/day_orb MXF live"
sleep 5
echo
echo "== 切換後 =="
echo "-- crontab --"; crontab -l | grep -E "_mxf_live|_live.sh|_paper.sh"
echo "-- live 進程 --"; ps -eo pid,etimes,cmd | grep 'start_paper.py --mode live' | grep -v grep
echo "-- 啟動日誌(確認 point_value=50/max_loss=20000)--"
for o in breakout_v7 day_orb; do
  L=$(ls -t data/logs/${o}_mxf_live_*.log 2>/dev/null | head -1)
  [ -n "$L" ] && grep -aE "point_value|對齊|INSTRUMENTS|login OK|Engine. started" "$L" | tr -d '\033' | tail -4
done
echo
echo "Rollback:pkill -f 'start_paper.py --mode live';crontab sed 反向換回 *_live.sh/_paper.sh;跑 3 支 *_live.sh + start_aft_orb_paper.sh。"
echo "⚠️ night_v3/aft_orb 由 14:50/14:52 cron 起;若現在已過、想立即起:bash scripts/start_night_v3_mxf_live.sh && bash scripts/start_aft_orb_mxf_live.sh"
