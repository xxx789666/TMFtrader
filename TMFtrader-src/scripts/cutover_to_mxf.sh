#!/bin/bash
# Cutover:現行(3 TMF live + aft_orb paper)→ 2 支 MXF live:day_v7(=breakout_v7)+ night_v7。
# 2026-06-08 起。下架 day_orb / aft_orb / night_v3(night_v3 已被 lab 判死、換 night_v7 30m)。
#
# 預設 DRY-RUN(只印計劃、零動作)。真正執行需三道閘:
#   ① 旗標 --apply  ② 環境 CONFIRM_MXF_CUTOVER=YES  ③ 落在無交易死區(05:00-08:30 或 13:45-14:50)且無倉
# 建議「週一早上 05:00-08:30」跑:day_v7 趕在 08:45 開盤前起;night_v7 交給 14:50 cron(15:00 前建 OR)。
# idempotent:crontab 用 sed 換 launcher + 移除下架策略行 + temp-file edit(不踩自刪 race)。
set -u
cd /home/xx/TMFtrader-src
export TZ=Asia/Taipei
APPLY=0; [ "${1:-}" = "--apply" ] && APPLY=1
NOW_HM=$(date +%H%M); NOW=$(date '+%F %T')

# 換 launcher:breakout_v7→mxf(day_v7);night_v3→night_v7_mxf(夜盤改 30m)
SED=(
  "s|start_breakout_v7_live.sh|start_breakout_v7_mxf_live.sh|"
  "s|start_night_v3_live.sh|start_night_v7_mxf_live.sh|"
)
REMOVE='start_day_orb|start_aft_orb'   # 下架 day_orb + aft_orb 的 cron 行

echo "== MXF Cutover 計劃(2 支:day_v7 + night_v7)@ $NOW =="
echo "[1] 停 3 支 TMF live + aft_orb paper"
echo "[2] crontab:breakout_v7→mxf、night_v3→night_v7_mxf、移除 day_orb+aft_orb 行"
echo "[3] 立即起 day_v7(breakout_v7 MXF);night_v7 交給 14:50 cron"
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

if [ "${CONFIRM_MXF_CUTOVER:-}" != "YES" ]; then
  echo "!! 缺 CONFIRM_MXF_CUTOVER=YES。中止。"; exit 1; fi
HM=$((10#$NOW_HM))
if ! { { [ "$HM" -ge 500 ] && [ "$HM" -le 830 ]; } || { [ "$HM" -ge 1345 ] && [ "$HM" -le 1450 ]; }; }; then
  echo "!! 現在 $NOW_HM 不在平窗(05:00-08:30 或 13:45-14:50)。中止。"; exit 1; fi
if [ -f data/active_position.json ]; then
  echo "!! data/active_position.json 存在(疑有 live 持倉)。先平倉再切。中止。"; exit 1; fi

echo ">> APPLYING ..."
pkill -f 'start_paper.py --mode live' 2>/dev/null && echo "  [1a] killed TMF live" || echo "  [1a] (無 live)"
pkill -f 'start_paper.py --mode paper' 2>/dev/null && echo "  [1b] killed aft_orb paper" || echo "  [1b] (無 paper)"
sleep 3
TMP=/tmp/ct_mxf_$$.txt
crontab -l 2>/dev/null > "$TMP"
for s in "${SED[@]}"; do sed -i "$s" "$TMP"; done
grep -vE "$REMOVE" "$TMP" > "$TMP.2" && mv "$TMP.2" "$TMP"
crontab "$TMP"; rm -f "$TMP"
echo "  [2] crontab 已更新(day_v7+night_v7、移除 day_orb/aft_orb)"
bash scripts/start_breakout_v7_mxf_live.sh
echo "  [3] 已起 day_v7(breakout_v7 MXF);night_v7 等 14:50 cron"
sleep 5
echo
echo "== 切換後 =="
echo "-- crontab --"; crontab -l | grep -E "_mxf_live|_live.sh|_paper.sh"
echo "-- live 進程 --"; ps -eo pid,etimes,cmd | grep 'start_paper.py --mode live' | grep -v grep
echo "-- day_v7 啟動日誌 --"
L=$(ls -t data/logs/breakout_v7_mxf_live_*.log 2>/dev/null | head -1)
[ -n "$L" ] && grep -aE "point_value|對齊|INSTRUMENTS|login OK|Engine. started" "$L" | tr -d '\033' | tail -4
echo "-- 🔑 權益守門(MXF 每口風控額 ~11,605;<290K → 0 口=不交易)--"
BAL=$(grep -ah 'Account. balance' data/logs/*_mxf_live_*.log 2>/dev/null | tr -d '\033' | tail -1 | grep -oE '[0-9,]+' | tr -d ',' | tail -1)
if [ -n "${BAL:-}" ]; then
  if [ "$BAL" -lt 290000 ]; then echo "  🚨🚨 權益 $BAL < 290,000 → MXF 算 0 口、不進場!補保證金到 60万後重啟。"
  elif [ "$BAL" -lt 580000 ]; then echo "  ⚠️ 權益 $BAL(<60万;MXF 口數可能 <2)"
  else echo "  ✅ 權益 $BAL ≥ 60万"; fi
else echo "  (讀不到 balance、手動確認)"; fi
echo
echo "Rollback:pkill live;crontab 把 *_mxf_live 換回 *_live.sh、night_v7→night_v3、加回 day_orb/aft_orb 行;跑舊 launcher。"
echo "⚠️ night_v7 由 14:50 cron 起;想立即起:bash scripts/start_night_v7_mxf_live.sh"
