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
# 夜盤 session 跨日（昨晚啟動、今晨 05:10 收盤）、CSV 用啟動日命名、所以 night 模式抓昨天
YESTERDAY=$(date -d "yesterday" +%F)
YESTERDAY_COMPACT=$(date -d "yesterday" +%Y%m%d)

if [ "$SESSION" = "day" ]; then
  HEADER="📊 [日盤日報] $TODAY"
  PROC_NAME='scripts/start.py'
  REPORT_DATE="$TODAY"
  REPORT_COMPACT="$TODAY_COMPACT"
else
  # 夜盤日報跑在 05:15、報的是「昨夜啟動的 session」
  HEADER="🌙 [夜盤日報] $YESTERDAY 夜盤"
  PROC_NAME='night_orb.py'
  REPORT_DATE="$YESTERDAY"
  REPORT_COMPACT="$YESTERDAY_COMPACT"
fi

# Process 狀態
if pgrep -f "$PROC_NAME" >/dev/null; then
  PROC_STATUS="✅ 跑中"
elif [ "$SESSION" = "night" ]; then
  # 夜盤日報跑在 05:15、cron 已於 05:10 pkill paper_night_orb、process 不在是預期
  PROC_STATUS="✅ 已收盤（05:10 cron 自動停）"
else
  PROC_STATUS="❌ 沒在跑"
fi

# 交易紀錄
# 注意：performance tracker 會同時寫 ${DATE}.json（session 結束時 final flush）
# 跟 ${DATE}_live.json（每筆 trade 即時更新）。兩者可能不同步——例如夜盤 ORB
# 寫了空版 .json 後、日盤 trade 進來只更新 _live.json。所以兩份都讀、取 trades 多的版本。
if [ "$SESSION" = "day" ]; then
  DAY_JSON="$PROJECT/data/performance/daily/${TODAY}.json"
  LIVE_JSON="$PROJECT/data/performance/daily/${TODAY}_live.json"
  SUMMARY=$(python3 -c "
import json, os
cands = []
for p in ['$DAY_JSON', '$LIVE_JSON']:
    if not os.path.exists(p):
        continue
    try:
        with open(p) as f:
            d = json.load(f)
        cands.append((len(d.get('trades', [])), os.path.getmtime(p), d))
    except Exception:
        pass
if not cands:
    print('(無交易紀錄檔)')
else:
    cands.sort(key=lambda x: (x[0], x[1]), reverse=True)
    d = cands[0][2]
    t = d.get('trades', [])
    s = d.get('paper_signals', [])
    pnl = d.get('daily_pnl', sum(x.get('net_pnl', x.get('pnl', 0)) for x in t))
    print(f'交易: {len(t)} 筆 / 訊號: {len(s)} 個')
    print(f'日 PnL: {pnl:+.0f} 元')
" 2>/dev/null)
else
  # 2026-05-21 fix：live 模式 CSV 改名為 live_night_orb_*.csv、paper 仍是 night_orb_*.csv
  # 兩個都看、取有資料 / 較新者
  LIVE_CSV="$PROJECT/data/paper_trading/live_night_orb_${REPORT_COMPACT}.csv"
  PAPER_CSV="$PROJECT/data/paper_trading/night_orb_${REPORT_COMPACT}.csv"
  NIGHT_CSV=""
  if [ -f "$LIVE_CSV" ]; then
    NIGHT_CSV="$LIVE_CSV"
    MODE_LABEL="LIVE"
  elif [ -f "$PAPER_CSV" ]; then
    NIGHT_CSV="$PAPER_CSV"
    MODE_LABEL="Paper"
  fi
  if [ -n "$NIGHT_CSV" ]; then
    TOTAL=$(wc -l < "$NIGHT_CSV")
    TRADES=$((TOTAL - 1))
    [ "$TRADES" -lt 0 ] && TRADES=0
    if [ "$TRADES" -gt 0 ]; then
      PNL=$(awk -F, 'NR>1 {if($10!="") s+=$10} END {printf "%.2fR", s}' "$NIGHT_CSV")
      SUMMARY="[$MODE_LABEL] 交易: $TRADES 筆 / R 累積: $PNL"
    else
      SUMMARY="[$MODE_LABEL] 交易: 0 筆（無突破訊號、正常）"
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
