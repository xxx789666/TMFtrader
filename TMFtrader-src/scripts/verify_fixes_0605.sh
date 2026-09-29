#!/bin/bash
# 監測(到 6/5):三支 TMF LIVE(breakout_v7/day_orb/night_v3)修復是否生產正常 + 抓新問題。
# 純讀取 + 推 TG。在死區時間跑(cron 05:30 夜盤收後 / 14:10 日盤收後)。6/5 後自動 no-op。
#
# 核心 carry-over 偵測(與 log 格式無關、最可靠):死區時間若 active_position.json 仍存在
# = 有倉裸抱穿過收盤(盤末強平失效)→ 真錢 carry-over,立即 flag。
set -u
cd /home/xx/TMFtrader-src
export TZ=Asia/Taipei
TODAY=$(date +%Y-%m-%d)
if [[ "$TODAY" > "2026-06-05" ]]; then exit 0; fi
NOW=$(date '+%F %H:%M'); DAY=$(date +%Y%m%d); YDAY=$(date -d 'yesterday' +%Y%m%d)
L=data/logs
FLAGS=""
flag(){ FLAGS="$FLAGS
⚠️ $1"; }
strip(){ sed 's/\x1b\[[0-9;]*m//g'; }
STRATS="breakout_v7 day_orb night_v3"

# A. live 進程(預期 2-3:日間 v7+day_orb、夜間 +night_v3)
PROCS=$(ps -eo cmd | grep 'start_paper.py --mode live' | grep -v grep | wc -l)
[ "$PROCS" -lt 2 ] && flag "live 進程僅 $PROCS 隻(預期 2-3)"

# B. 今日 live log 真實錯誤(排除自動恢復的價格異常)+ 熔斷器狀態
ERRF=0
for s in $STRATS; do
  n=$(grep -hE 'ERROR|Traceback' $L/${s}_live_$DAY.log 2>/dev/null | grep -vE 'ANOMALY|緊急停機: 價格異常|價格異常已穩定' | grep -c .)
  ERRF=$((ERRF + n))
done
[ "$ERRF" -gt 0 ] && flag "今日 live log $ERRF 行非預期錯誤"
# 熔斷器:owner-scope 後各 live 策略自己的 risk_state;無則回退舊共用檔
for rs in data/live/*/risk_state.json data/risk_state.json; do
  [ -f "$rs" ] || continue
  cs=$(grep -oE '"circuit_state": *"[^"]*"' "$rs")
  [ -n "$cs" ] && { echo "$cs" | grep -q active || flag "$(basename "$(dirname "$rs")") 熔斷器未恢復: $cs"; }
done

# C. ★核心★ 死區仍有持倉鎖 = carry-over(盤末強平失效、裸抱穿過盤)
LK=data/active_position.json
if [ -f "$LK" ]; then
  EU=$(grep -oE '"entry_unix": *[0-9.]+' "$LK" | grep -oE '[0-9.]+' | head -1)
  NOWU=$(date +%s)
  if [ -n "$EU" ] && [ "$((NOWU - ${EU%.*}))" -lt 43200 ]; then   # < 12h 非殭屍
    OWN=$(grep -oE '"owner": *"[^"]*"' "$LK" | head -1)
    flag "死區仍有持倉鎖(疑 carry-over、盤末強平未平):$OWN entry_unix=$EU"
  fi
fi

# D. 輔助:log 內「下一盤撮合才平」的時間簽名(去色碼;三支 live 都查)
for s in $STRATS; do cat $L/${s}_live_$DAY.log $L/${s}_live_$YDAY.log 2>/dev/null | strip > /tmp/_vf_$s 2>/dev/null || true; done
for s in night_v3 breakout_v7; do
  c=$(grep -E 'CLOSE|硬停|平倉|出場' /tmp/_vf_$s 2>/dev/null | grep -E '0[89]:[0-5][0-9]:' | grep -iv '盤末')
  [ -n "$c" ] && flag "$s 疑似裸抱過夜(08-09平倉):$(echo "$c"|tail -1|cut -c1-70)"
done
for s in day_orb breakout_v7; do
  c=$(grep -E 'CLOSE|硬停|平倉|出場' /tmp/_vf_$s 2>/dev/null | grep -E '14:[4-5][0-9]:|15:[01][0-9]:' | grep -iv '盤末')
  [ -n "$c" ] && flag "$s 疑似裸抱過日盤收盤(14:45-15:19平倉):$(echo "$c"|tail -1|cut -c1-70)"
done

# 摘要:各 live 最近進出場(寬鬆 grep 涵蓋 live/paper 兩種格式;帶日期前綴)
SUMMARY=""
for s in $STRATS; do
  EV=$(
    for d in "$YDAY" "$DAY"; do
      lf="$L/${s}_live_$d.log"; [ -f "$lf" ] || continue
      mmdd="${d:4:2}-${d:6:2}"
      cat "$lf" | strip | grep -vE 'start_paper.py|headless 真實下單' \
        | grep -E 'BUY x|SELL x| CLOSE |硬停|盤末強平|追蹤出場|notify_entry|\[(LIVE|PAPER)\] ' \
        | sed 's/ | .*INFO.* | / | /' | cut -c1-62 | sed "s/^/${mmdd} /"
    done | tail -2
  )
  [ -n "$EV" ] && SUMMARY="$SUMMARY
[$s]
$EV"
done
rm -f /tmp/_vf_* 2>/dev/null || true

if [ -z "$FLAGS" ]; then
  VERDICT="✅ PASS — 死區無持倉、無 bug 簽名、無新錯誤、熔斷器 active、live 進程正常"
else
  VERDICT="🚩 需檢查:$FLAGS"
fi
MSG="🔍 [LIVE 監測 → 6/5] $NOW
live 進程 $PROCS | 今日錯誤 $ERRF
$VERDICT
${SUMMARY:-（近期無 live 進出場）}"
echo "$MSG"
if [ -f .env ]; then
  TG_TOKEN=$(grep '^TG_BOT_TOKEN=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")
  TG_CHAT=$(grep '^TG_CHAT_ID=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")
  [ -n "${TG_TOKEN:-}" ] && [ -n "${TG_CHAT:-}" ] && \
    curl -sS --max-time 10 -o /dev/null \
      "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${TG_CHAT}" --data-urlencode "text=${MSG}" 2>/dev/null || true
fi
