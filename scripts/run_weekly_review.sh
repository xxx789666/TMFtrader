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
LOCAL_PROJECT_DIR="${LOCAL_PROJECT_DIR:-$PROJECT_ROOT/TMFtrader-src}"
HERMES_BIN="${HERMES_BIN:-$HOME/.local/bin/hermes}"
WEEK_ENDING="${WEEK_ENDING:-today}"

# 資料來源優先順序（loader.py 讀 TMF_DATA_ROOT env var）：
#   1. .env.sync 內顯式設定 TMF_DATA_ROOT → 用設定值
#   2. 偵測本機 paper EA 路徑 → 用 ~/vps_trader_paper（symlink → 永豐-自動化交易）
#   3. fallback：repo 內 ../data（不推薦、會是舊快照）
if [[ -z "${TMF_DATA_ROOT:-}" ]] && [[ -d "$HOME/vps_trader_paper/TMFtrader-src/data" ]]; then
  export TMF_DATA_ROOT="$HOME/vps_trader_paper/TMFtrader-src/data"
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
export TMF_DATA_ROOT="${TMF_DATA_ROOT:-$HOME/vps_trader_paper/TMFtrader-src/data}"
# 2026-05-23: 過濾 paper trades、只統計 live。trade.reason 開頭 [PAPER] 排除。
export EXCLUDE_PAPER_TRADES=true

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

# 帳戶級日/夜 edge（與券商對帳一致;weekly_session_report.py 產出、sync 拉回）
SESSION_JSON=$(python3 -m review.tools_for_hermes load_session_edge 2>&1 || echo '{}')
log "  session_edge: $(echo "$SESSION_JSON" | python3 -c 'import sys,json;d=json.loads(sys.stdin.read());print("avail=",d.get("available"),"day=",(d.get("day") or {}).get("net"),"night=",(d.get("night") or {}).get("net"))' 2>/dev/null || echo 'parse-fail')"

# ---- Step 2b：用 Python 把 JSON 格式化成「純文字事實」(model 處理 raw JSON 容易回空) ----
log "[Step 2b/3] 把資料格式化成 prompt facts"
FACTS=$(python3 <<PY
import json
w = json.loads('''$WEEK_JSON''')
b = json.loads('''$BEST_JSON''')
w_= json.loads('''$WORST_JSON''')
se= json.loads('''$SESSION_JSON''')

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
if se.get('available'):
    d_, n_ = se.get('day') or {}, se.get('night') or {}
    print()
    print("=== 帳戶實際對帳(累積、net=毛-稅-費,與券商App一致)===")
    print(f"  日盤: {d_.get('n',0)} 筆 PF {d_.get('pf',0)} 淨 {d_.get('net',0):+,.0f}")
    print(f"  夜盤: {n_.get('n',0)} 筆 PF {n_.get('pf',0)} 淨 {n_.get('net',0):+,.0f}")
    print(f"  全期淨: {se.get('total_net',0):+,.0f} / 共 {se.get('n_total',0)} 筆")
    print(f"  夜盤判讀: {se.get('night_verdict','')}")
PY
)
log "  ✅ facts 長度 $(echo "$FACTS" | wc -c) bytes"

# ---- Step 2c：Hermes 產敘述 ----
log "[Step 2c/3] Hermes 產覆盤敘述"
read -r -d '' PROMPT <<EOF || true
你是 TMF 期貨週度覆盤師。事實如下（已驗證，不可更改）：

$FACTS

請只輸出一個 JSON 物件（繁體中文內容、不要 markdown 區塊、不要前言），鍵如下：
{
  "story": "1-2 句本週敘事（哪天好哪天壞、為什麼）",
  "diagnosis": ["觀察1（引用真實 MFE/MAE 或訊號類型）", "觀察2（若事實有夜盤判讀則照引用 night_verdict 那句）"],
  "suggestions": ["若總筆數<5 寫 observe-only（樣本太小）；否則給 1-2 條具體可操作方向"],
  "confidence": "low | medium | high"
}
數字一律以事實為準、禁止自行計算或更改。只輸出 JSON。
EOF

# 模型清單(2026-09-12 重選)。舊的 nvidia/llama-3.3-nemotron-super-49b-v1 已被
# NVIDIA 下架(410 Gone, end of life 2026-08-26),害週度覆盤靜默失敗三週
# (8/29、9/5、9/12,最後一次成功 8/22)。當時沒有 fallback,一次非 200 就整個 exit 1。
#
# 這批是 2026-09-12 對本帳號實測「打得通 + 真的吐得出正確繁中 JSON」的結果。
# NIM 目錄列 82 支,但這個免費帳號多數回 404 no-access,能打通的只有 8 支,
# 其中 nemotron-3.5-lightning / muse-glimmer 會把 token 全燒在 thinking 上吐不完 JSON,
# nano-omni 會幻覺(事實是 BUY 它寫成空單),所以都排除。
# 依序 fallback,前一支失敗(非 200 / 空回應 / JSON 解不出來)就換下一支。
REVIEW_MODELS="${REVIEW_MODELS:-${REVIEW_MODEL:-nvidia/nemotron-3-super-120b-a12b nvidia/nemotron-3-ultra-550b-a55b openai/gpt-oss-20b}}"
REVIEW_PROVIDER="${REVIEW_PROVIDER:-nvidia}"

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
REVIEW_MODEL_USED=""

for _m in $REVIEW_MODELS; do
  log "  嘗試 model: $_m (direct NIM API,不繞 Hermes)"
  # 關掉 reasoning:這幾支預設會思考,500~1500 token 全燒在 thinking 上、JSON 吐不完。
  # nemotron-3 系列吃 chat_template_kwargs.thinking=false;gpt-oss 吃 reasoning_effort。
  MODEL="$_m" python3 -c "
import json, os
model = os.environ['MODEL']
prompt = '''$PROMPT'''
body = {
  'model': model,
  'messages': [{'role':'user', 'content': prompt}],
  'temperature': 0.3,
  'max_tokens': 900,
}
if model.startswith('nvidia/nemotron-3'):
    body['chat_template_kwargs'] = {'thinking': False}
elif model.startswith('openai/gpt-oss'):
    body['reasoning_effort'] = 'low'
print(json.dumps(body))
" > "$REQ_FILE"

  HTTP_CODE=$(curl -sS --max-time 180 -o "$REPORT_FILE" -w '%{http_code}' \
    https://integrate.api.nvidia.com/v1/chat/completions \
    -H "Authorization: Bearer $NVIDIA_API_KEY" \
    -H "Content-Type: application/json" \
    --data-binary @"$REQ_FILE") || HTTP_CODE="000"

  if [[ "$HTTP_CODE" != "200" ]]; then
    log "  ⚠️  $_m 回 $HTTP_CODE: $(head -c 200 "$REPORT_FILE")"
    continue
  fi
  # 200 還不夠:reasoning 模型會回 200 但 content 是空的/沒有完整 JSON
  if ! python3 -c "
import json, re, sys
r = json.load(open('$REPORT_FILE'))
c = (r['choices'][0]['message'].get('content') or '').strip()
m = re.search(r'\{.*\}', c, re.S)
if not m: sys.exit(1)
j = json.loads(m.group(0))
sys.exit(0 if j.get('story') else 1)
" 2>/dev/null; then
    log "  ⚠️  $_m 回 200 但拿不到可解析的 JSON(多半是 thinking 吃光 token),換下一支"
    continue
  fi
  REVIEW_MODEL_USED="$_m"
  log "  ✅ 採用 $_m"
  break
done

if [[ -z "$REVIEW_MODEL_USED" ]]; then
  log "  ❌ 所有候選模型都失敗($REVIEW_MODELS)——多半是又被下架或帳號沒權限。"
  log "     查目錄:curl -H 'Authorization: Bearer <key>' https://integrate.api.nvidia.com/v1/models"
  exit 1
fi
REVIEW_MODEL="$REVIEW_MODEL_USED"

# 版面由 Python 固定組裝(數字優先用帳戶級 session_edge),LLM 只供 story/diagnosis/suggestions/confidence
python3 <<PY >/tmp/_report.txt
import json, re
with open('$REPORT_FILE') as f:
    r = json.load(f)
content = r['choices'][0]['message']['content'].strip()
# 去除可能的 code fence,抓第一個 {...}
m = re.search(r'\{.*\}', content, re.S)
try:
    llm = json.loads(m.group(0)) if m else {}
except Exception:
    llm = {}

w  = json.loads('''$WEEK_JSON''')
se = json.loads('''$SESSION_JSON''')

# 表頭數字:有帳戶級就用帳戶級(與券商對帳一致),否則退回引擎週統計
if se.get('available'):
    tot = se.get('total') or {}
    net, n, wr = tot.get('net', 0), tot.get('n', 0), tot.get('wr', 0)
else:
    net, n, wr = w.get('net_pnl', 0), w.get('n_trades', 0), (w.get('win_rate') or 0) * 100

bd = w.get('best_day') or {}
wd = w.get('worst_day') or {}
story = (llm.get('story') or '').strip()
diags = llm.get('diagnosis') or []
sugs  = llm.get('suggestions') or []
conf  = llm.get('confidence') or 'low'

L = []
L.append(f"📊 TMF 週度覆盤 — {w.get('week_start')} ~ {w.get('week_end')}")
L.append(f"週淨損益: {net:+,.0f} | {n} 筆 | WR {wr:.0f}%")
L.append("")
if story:
    L.append(f"本週故事：{story}")
    L.append("")
L.append(f"最佳日：{bd.get('date')} {bd.get('net_pnl',0):+,.0f}    最差日：{wd.get('date')} {wd.get('net_pnl',0):+,.0f}")
L.append(f"連虧日數上限：{w.get('max_consec_losing_days',0)}")
L.append("")
if se.get('available'):
    d_, n_ = se.get('day') or {}, se.get('night') or {}
    pf_fmt = lambda v: "∞(全勝)" if v is None else f"{v}"
    L.append("帳戶日/夜對帳(與券商App一致)：")
    L.append(f"  日盤 {d_.get('n',0)} 筆 PF {pf_fmt(d_.get('pf'))} {d_.get('net',0):+,.0f}")
    L.append(f"  夜盤 {n_.get('n',0)} 筆 PF {pf_fmt(n_.get('pf'))} {n_.get('net',0):+,.0f}")
    L.append(f"  └ {se.get('night_verdict','')}")
    L.append("")
L.append("診斷：")
for d in diags[:3]:
    L.append(f"• {str(d).strip()}")
L.append("")
L.append("建議：")
for s in sugs[:2]:
    L.append(f"- {str(s).strip()}")
L.append("")
L.append(f"信心：{conf}")
print("\n".join(L))
PY
REPORT=$(cat /tmp/_report.txt)
log "  ✅ 報告組裝完成（$(echo "$REPORT" | wc -c) bytes）"
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
