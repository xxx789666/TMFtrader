#!/usr/bin/env bash
# 週度覆盤 orchestrator（WSL2 內執行）
#
# 流程：
#   1. sync_from_vps.sh  —— 拉最近 14 天交易資料
#   2. hermes invoke tmf-weekly-review  —— 跑 LLM 覆盤、推 TG
#   3. runaway_guard.py  —— 檢查行為是否異常、必要時寫 kill_switch
#
# 用法（手動或 Task Scheduler）：
#   bash run_weekly_review.sh                  # 預設覆盤上週
#   WEEK_ENDING=2026-05-15 bash run_weekly_review.sh
#
# 環境變數（從 scripts/.env.sync 讀）：
#   除了 sync 用的，還會用：
#   PROJECT_ROOT       本機專案根，預設 $HOME/vps_trader
#   HERMES_BIN         hermes 執行檔，預設 hermes
#   GUARD_LOG_DIR      runaway_guard log 目錄

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
[[ -f "$SCRIPT_DIR/.env.sync" ]] && { set -a; source "$SCRIPT_DIR/.env.sync"; set +a; }
# 從 ~/.hermes/.env 補 TG token / NIM key（Hermes wizard 設好的）
[[ -f "$HOME/.hermes/.env" ]] && { set -a; source "$HOME/.hermes/.env" 2>/dev/null || true; set +a; }
# 也支援 TELEGRAM_* 命名（Hermes 與 EA 兩種慣用名）
TG_BOT_TOKEN="${TG_BOT_TOKEN:-${TELEGRAM_BOT_TOKEN:-}}"
TG_CHAT_ID="${TG_CHAT_ID:-${TELEGRAM_CHAT_ID:-}}"

PROJECT_ROOT="${PROJECT_ROOT:-$HOME/vps_trader}"
LOCAL_PROJECT_DIR="${LOCAL_PROJECT_DIR:-$PROJECT_ROOT/ultra-trader-src}"
HERMES_BIN="${HERMES_BIN:-$HOME/.local/bin/hermes}"
WEEK_ENDING="${WEEK_ENDING:-today}"

# 資料來源優先順序（loader.py 讀 TMF_DATA_ROOT env var）：
#   1. .env.sync 內顯式設定 TMF_DATA_ROOT → 用設定值
#   2. 偵測本機 paper EA 路徑 → 用 ~/vps_trader_paper（symlink → 永豐-自動化交易）
#   3. fallback：repo 內 ../data（不推薦、會是舊快照）
if [[ -z "${TMF_DATA_ROOT:-}" ]] && [[ -d "$HOME/vps_trader_paper/ultra-trader-src/data" ]]; then
  export TMF_DATA_ROOT="$HOME/vps_trader_paper/ultra-trader-src/data"
fi
[[ -n "${TMF_DATA_ROOT:-}" ]] && export TMF_DATA_ROOT

LOG_DIR="$LOCAL_PROJECT_DIR/data/logs"
mkdir -p "$LOG_DIR"
RUN_LOG="$LOG_DIR/weekly_review_$(date +%Y%m%d_%H%M%S).log"

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$RUN_LOG"
}

notify_tg() {
  local msg="$1"
  if [[ -n "${TG_BOT_TOKEN:-}" && -n "${TG_CHAT_ID:-}" ]]; then
    curl -sS --max-time 10 -o /dev/null \
      "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
      -d chat_id="${TG_CHAT_ID}" -d parse_mode=Markdown -d text="$msg" || true
  fi
}

trap 'log "❌ orchestrator 異常退出 (exit=$?)"; notify_tg "🛑 *週度覆盤 orchestrator 失敗*\n見 \`$RUN_LOG\`"' ERR

log "================================================================"
log "週度覆盤 orchestrator 啟動 week_ending=$WEEK_ENDING"
log "TMF_DATA_ROOT=${TMF_DATA_ROOT:-<fallback to repo data/>}"
log "================================================================"

# ---- Step 0：kill_switch 預檢（如果有就停）----
if [[ -f "$HOME/.hermes/kill_switch" ]]; then
  reason=$(cat "$HOME/.hermes/kill_switch")
  log "⚠️ kill_switch 啟動：$reason"
  notify_tg "🛑 *週度覆盤未啟動：kill_switch 已啟動*
原因：\`$reason\`
解除：\`rm ~/.hermes/kill_switch\`"
  exit 0
fi

# ---- Step 1：sync from VPS（可選）----
# 規則：有 .env.sync 且裡面有 VPS_HOST → 跑 sync；否則跳過、用本機資料
log "[Step 1/3] sync from VPS"
if [[ -f "$SCRIPT_DIR/.env.sync" ]] && grep -qE '^VPS_HOST=[^#].+' "$SCRIPT_DIR/.env.sync"; then
  log "  偵測到 .env.sync，啟動 sync"
  if bash "$SCRIPT_DIR/sync_from_vps.sh"; then
    log "  ✅ sync OK"
  else
    log "  ❌ sync 失敗，停止 orchestrator（sync_from_vps 已自行推 TG）"
    exit 1
  fi
else
  log "  ⏭️  跳過 sync（無 .env.sync 或 VPS_HOST 未設定）—— 使用本機現有資料"
  n_local=$(find "$LOCAL_PROJECT_DIR/data/performance/daily/" -name '*_live*.json' 2>/dev/null | wc -l)
  log "  本機現有 daily JSON 檔數：$n_local"
fi

# ---- Step 2a：bash 預載資料（取代 agent 跑 execute_code）----
log "[Step 2a/3] bash 預載 load_week / load_daily JSON"
export PYTHONPATH="$LOCAL_PROJECT_DIR"
export TMF_DATA_ROOT="${TMF_DATA_ROOT:-$HOME/vps_trader_paper/ultra-trader-src/data}"

WEEK_JSON=$(python3 -m review.tools_for_hermes load_week --week_ending="$WEEK_ENDING" --compact 2>&1)
if [[ -z "$WEEK_JSON" ]] || echo "$WEEK_JSON" | grep -q '"error"'; then
  log "  ❌ load_week 失敗: $WEEK_JSON"
  notify_tg "🛑 *週度覆盤 Step 2a 失敗*\n\`\`\`\n$WEEK_JSON\n\`\`\`"
  exit 1
fi
log "  ✅ load_week OK ($(echo "$WEEK_JSON" | python3 -c 'import sys,json;d=json.loads(sys.stdin.read());print("trades=",d["n_trades"],"net=",d["net_pnl"])'))"

BEST_DATE=$(echo "$WEEK_JSON" | python3 -c 'import sys,json;d=json.loads(sys.stdin.read());bd=d.get("best_day") or {};print(bd.get("date",""))')
WORST_DATE=$(echo "$WEEK_JSON" | python3 -c 'import sys,json;d=json.loads(sys.stdin.read());wd=d.get("worst_day") or {};print(wd.get("date",""))')

BEST_JSON="{}"
WORST_JSON="{}"
if [[ -n "$BEST_DATE" ]]; then
  BEST_JSON=$(python3 -m review.tools_for_hermes load_daily --date="$BEST_DATE" --session=day 2>&1 || echo '{}')
fi
if [[ -n "$WORST_DATE" && "$WORST_DATE" != "$BEST_DATE" ]]; then
  WORST_JSON=$(python3 -m review.tools_for_hermes load_daily --date="$WORST_DATE" --session=day 2>&1 || echo '{}')
fi
log "  ✅ best_day=$BEST_DATE worst_day=$WORST_DATE"

# ---- Step 2b：用 Python 把 JSON 格式化成「純文字事實」(model 處理 raw JSON 容易回空) ----
log "[Step 2b/3] 把資料格式化成 prompt facts"
FACTS=$(python3 <<PY
import json
w = json.loads('''$WEEK_JSON''')
b = json.loads('''$BEST_JSON''')
w_= json.loads('''$WORST_JSON''')

print(f"週次: {w['week_start']} ~ {w['week_end']}")
print(f"交易日數: {w['n_trading_days']}（有交易 {w['n_days_with_trades']} 日）")
print(f"總交易筆數: {w['n_trades']}（勝 {w['n_wins']} / 負 {w['n_losses']}）")
print(f"勝率: {w['win_rate']*100:.0f}%")
print(f"週淨損益: {w['net_pnl']:+,.0f}")
pf = w.get('profit_factor')
if pf is not None: print(f"PF: {pf:.2f}")
print(f"平均獲利 / 虧損: {w['avg_win']:,.0f} / {w['avg_loss']:,.0f}")
print(f"平均 MFE / MAE 點: {w['avg_mfe']:.1f} / {w['avg_mae']:.1f}")
print(f"方向分佈: {w['sides']}")
bd = w.get('best_day') or {}
wd = w.get('worst_day') or {}
print(f"最佳日: {bd.get('date')} {bd.get('net_pnl',0):+,.0f}")
print(f"最差日: {wd.get('date')} {wd.get('net_pnl',0):+,.0f}")
print(f"最大連虧日數: {w['max_consec_losing_days']}")
print()
print("=== 每日明細 ===")
for r in w.get('daily_rollup', []):
    print(f"  {r['date']} {r['weekday']}: {r['n_trades']} 筆 / {r['net_pnl']:+,.0f} / sessions={','.join(r['sessions_available'])}")
print()
if b.get('trades'):
    print("=== 最佳日訊號摘要 ===")
    for t in b['trades'][:3]:
        sig_strength = (b.get('signals') or [{}])[0].get('signal_strength','?')
        print(f"  {t.get('side')} @ {t.get('entry_price')} → {t.get('exit_price')} pnl={t.get('net_pnl',t.get('pnl')):+.0f} MFE={t.get('max_favorable',0)} MAE={t.get('max_adverse',0)} reason={t.get('reason','?')[:50]}")
if w_.get('trades') and (wd.get('date') != bd.get('date')):
    print("=== 最差日訊號摘要 ===")
    for t in w_['trades'][:3]:
        print(f"  {t.get('side')} @ {t.get('entry_price')} → {t.get('exit_price')} pnl={t.get('net_pnl',t.get('pnl')):+.0f} MFE={t.get('max_favorable',0)} MAE={t.get('max_adverse',0)} reason={t.get('reason','?')[:50]}")
rs = w.get('risk_state')
if rs:
    print()
    print(f"風控: circuit={rs.get('circuit_state')} peak_eq={rs.get('peak_equity')} daily_loss={rs.get('daily_loss')} halt_reason={rs.get('halt_reason','')}")
PY
)
log "  ✅ facts 長度 $(echo "$FACTS" | wc -c) bytes"

# ---- Step 2c：Hermes 產敘述 ----
log "[Step 2c/3] Hermes 產覆盤敘述"
read -r -d '' PROMPT <<EOF || true
你是 TMF 期貨週度覆盤師。事實如下（已驗證，不可更改）：

$FACTS

依以下格式輸出繁體中文週度覆盤訊息（6-12 行）：

📊 TMF 週度覆盤 — {週期}
週淨損益: {±,.0f} | {筆數} 筆 | WR {勝率}%

本週故事：{1-2 句敘事}

最佳日：{日期} {±,.0f}
最差日：{日期} {±,.0f}
連虧日數上限：{N}

診斷：
• {觀察 1，引用真實 MFE/MAE 或訊號類型}
• {觀察 2}

建議：
- 若總筆數 < 5：observe-only（樣本太小）
- 否則給 1-2 條具體可操作的參數方向

信心：{low/medium/high}

只輸出上面格式的繁體中文，不要加 JSON / markdown 區塊 / 前言。
EOF

REVIEW_MODEL="${REVIEW_MODEL:-nvidia/llama-3.3-nemotron-super-49b-v1}"
REVIEW_PROVIDER="${REVIEW_PROVIDER:-nvidia}"
log "  Model: $REVIEW_MODEL via direct NIM API（不繞 Hermes）"

# 直接打 NIM（避免 Hermes 對純文字摘要任務常 empty response 的問題）
# 之後若要 agent 自主流程（跨 session 記憶、skill evolution）再走 hermes chat
if [[ -z "${NVIDIA_API_KEY:-}" ]]; then
  NVIDIA_API_KEY=$(grep "^NVIDIA_API_KEY=" "$HOME/.hermes/.env" 2>/dev/null | cut -d= -f2- | tr -d '"')
fi
if [[ -z "$NVIDIA_API_KEY" ]]; then
  log "  ❌ 找不到 NVIDIA_API_KEY"
  exit 1
fi

REPORT_FILE=$(mktemp)
REQ_FILE=$(mktemp)
python3 -c "
import json, sys
prompt = '''$PROMPT'''
print(json.dumps({
  'model': '$REVIEW_MODEL',
  'messages': [{'role':'user', 'content': prompt}],
  'temperature': 0.3,
  'max_tokens': 500,
}))
" > "$REQ_FILE"

HTTP_CODE=$(curl -sS -o "$REPORT_FILE" -w '%{http_code}' \
  https://integrate.api.nvidia.com/v1/chat/completions \
  -H "Authorization: Bearer $NVIDIA_API_KEY" \
  -H "Content-Type: application/json" \
  --data-binary @"$REQ_FILE")

if [[ "$HTTP_CODE" != "200" ]]; then
  log "  ❌ NIM 回應 $HTTP_CODE: $(cat $REPORT_FILE | head -c 300)"
  exit 1
fi

REPORT=$(python3 -c "
import json, sys
with open('$REPORT_FILE') as f:
    r = json.load(f)
print(r['choices'][0]['message']['content'].strip())
usage = r.get('usage', {})
import sys
sys.stderr.write(f'usage: in={usage.get(\"prompt_tokens\")} out={usage.get(\"completion_tokens\")} total={usage.get(\"total_tokens\")}\n')
" 2>&1 >/tmp/_report.txt)
REPORT=$(cat /tmp/_report.txt)
log "  ✅ 摘要產生（$(echo "$REPORT" | wc -c) bytes）"
echo "─── 報告全文 ───" | tee -a "$RUN_LOG"
echo "$REPORT" | tee -a "$RUN_LOG"
echo "─── 報告結束 ───" | tee -a "$RUN_LOG"
rm -f "$REPORT_FILE" "$REQ_FILE" /tmp/_report.txt

# ---- 推 Telegram（不依賴 hermes gateway，直接 curl）----
log "[Step 2d/3] 推 Telegram"
if [[ -n "${TG_BOT_TOKEN:-}" && -n "${TG_CHAT_ID:-}" ]]; then
  TG_RESP=$(curl -sS --max-time 15 \
    "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TG_CHAT_ID}" \
    --data-urlencode "text=${REPORT}" \
    --data-urlencode "parse_mode=" 2>&1)
  if echo "$TG_RESP" | grep -q '"ok":true'; then
    log "  ✅ TG 推送成功"
  else
    log "  ❌ TG 推送失敗：$TG_RESP"
  fi
else
  log "  ⚠️ TG_BOT_TOKEN / TG_CHAT_ID 未設定，跳過推送"
fi
rm -f "$REPORT_FILE"

# ---- Step 3：runaway guard 檢查 ----
log "[Step 3/3] runaway_guard"
cd "$LOCAL_PROJECT_DIR"
if python3 -m review.runaway_guard \
    --hermes-home="$HOME/.hermes" \
    --job-name=tmf-weekly-review 2>&1 | tee -a "$RUN_LOG"; then
  log "  ✅ guard 通過"
else
  log "  🛑 guard 觸發（已寫 kill_switch + 推 TG）"
fi

log "================================================================"
log "週度覆盤 orchestrator 完成。詳見 $RUN_LOG"
log "================================================================"
