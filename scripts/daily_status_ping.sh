#!/usr/bin/env bash
# daily_status_ping.sh — 每日盤前/盤後狀態日報、推 TG
#
# 用法：
#   bash scripts/daily_status_ping.sh day     # 日盤收盤後（13:50 TST）
#   bash scripts/daily_status_ping.sh night   # 夜盤收盤後（05:15 TST）
#
# cron（VPS）：
#   50 5  * * 1-5  cd ~/ultra-trader-src && bash scripts/daily_status_ping.sh day   >/dev/null 2>&1
#   15 21 * * 0-4  cd ~/ultra-trader-src && bash scripts/daily_status_ping.sh night >/dev/null 2>&1

set +e
export TZ=Asia/Taipei

PROJECT=$(cd "$(dirname "${BASH_SOURCE[0]}")"/.. && pwd)
SESSION=${1:-day}

# 讀 TG creds
TG_TOKEN=""
TG_CHAT=""
if [ -f "$PROJECT/.env" ]; then
  TG_TOKEN=$(grep '^TG_BOT_TOKEN=' "$PROJECT/.env" | cut -d= -f2- | tr -d '"' | tr -d "'")
  TG_CHAT=$(grep '^TG_CHAT_ID='  "$PROJECT/.env" | cut -d= -f2- | tr -d '"' | tr -d "'")
fi
[ -z "$TG_TOKEN" ] || [ -z "$TG_CHAT" ] && exit 0

TODAY=$(date +%F)
TODAY_COMPACT=$(date +%Y%m%d)

if [ "$SESSION" = "day" ]; then
  HEADER="📊 [日盤日報] $TODAY"
  PROC_NAME='scripts/start.py'
else
  HEADER="🌙 [夜盤日報] $TODAY"
  PROC_NAME='paper_night_orb.py'
fi

# Process 狀態
if pgrep -f "$PROC_NAME" >/dev/null; then
  PROC_STATUS="✅ 跑中"
else
  PROC_STATUS="❌ 沒在跑"
fi

# 交易紀錄
if [ "$SESSION" = "day" ]; then
  DAY_JSON="$PROJECT/data/performance/daily/${TODAY}.json"
  [ ! -f "$DAY_JSON" ] && DAY_JSON="$PROJECT/data/performance/daily/${TODAY}_live.json"
  if [ -f "$DAY_JSON" ]; then
    SUMMARY=$(python3 -c "
import json, sys
try:
    d = json.load(open('$DAY_JSON'))
    t = d.get('trades', [])
    s = d.get('paper_signals', [])
    pnl = d.get('daily_pnl', sum(x.get('net_pnl', x.get('pnl', 0)) for x in t))
    print(f'交易: {len(t)} 筆 / 訊號: {len(s)} 個')
    print(f'日 PnL: {pnl:+.0f} 元')
except Exception as e:
    print(f'(讀檔失敗: {e})')
" 2>/dev/null)
  else
    SUMMARY="(無交易紀錄檔)"
  fi
else
  NIGHT_CSV="$PROJECT/data/paper_trading/night_orb_${TODAY_COMPACT}.csv"
  if [ -f "$NIGHT_CSV" ]; then
    TOTAL=$(wc -l < "$NIGHT_CSV")
    TRADES=$((TOTAL - 1))
    [ "$TRADES" -lt 0 ] && TRADES=0
    if [ "$TRADES" -gt 0 ]; then
      PNL=$(awk -F, 'NR>1 {if($10!="") s+=$10} END {printf "%.2fR", s}' "$NIGHT_CSV")
      SUMMARY="交易: $TRADES 筆 / R 累積: $PNL"
    else
      SUMMARY="交易: 0 筆（無突破訊號、正常）"
    fi
  else
    SUMMARY="(CSV 不存在、夜盤可能未啟動)"
  fi
fi

# 風控狀態
RISK_FILE="$PROJECT/data/risk_state.json"
[ "$SESSION" = "night" ] && [ -f "$PROJECT/data/risk_state_night.json" ] && RISK_FILE="$PROJECT/data/risk_state_night.json"
RISK_STATE="?"
if [ -f "$RISK_FILE" ]; then
  RISK_STATE=$(python3 -c "
import json
try:
    d = json.load(open('$RISK_FILE'))
    print(d.get('circuit_state', d.get('state', 'active')))
except Exception:
    print('?')
" 2>/dev/null)
fi

MSG="$HEADER

Process: $PROC_STATUS
$SUMMARY
風控: $RISK_STATE

時間: $(date '+%H:%M:%S')"

curl -sS --max-time 10 -o /dev/null \
  "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
  --data-urlencode "chat_id=${TG_CHAT}" \
  --data-urlencode "text=$MSG" 2>/dev/null || true
