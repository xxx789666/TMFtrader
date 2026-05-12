#!/usr/bin/env bash
# 從 GCP VPS 拉最近的交易紀錄到本機 WSL2
#
# 同步內容：
#   data/performance/daily/         僅最近 14 天 *_live*.json
#   data/risk_state.json
#   data/risk_state_night.json
#
# 用法：
#   bash sync_from_vps.sh                    # 預設目標
#   VPS_HOST=user@1.2.3.4 bash sync_from_vps.sh
#
# 環境變數（可由 .env.sync 覆寫）：
#   VPS_HOST           SSH 目標，例 "xx@34.81.x.x"
#   VPS_PROJECT_DIR    VPS 上專案路徑，預設 /root/ultra-trader-src
#   LOCAL_PROJECT_DIR  本機路徑，預設 ~/vps_trader/ultra-trader-src
#   SSH_KEY            SSH 私鑰路徑，預設 ~/.ssh/id_ed25519
#   SYNC_DAYS          往回拉幾天，預設 14
#   TG_BOT_TOKEN       失敗時推 Telegram
#   TG_CHAT_ID
#
# 退出碼：
#   0 成功
#   1 rsync 失敗
#   2 沒有 SSH key / 環境變數設定不全

set -euo pipefail

# 讀 .env.sync（若有）
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/.env.sync" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$SCRIPT_DIR/.env.sync"
  set +a
fi

: "${VPS_HOST:?VPS_HOST 未設定，請在 scripts/.env.sync 內設定 VPS_HOST=user@ip}"
VPS_PROJECT_DIR="${VPS_PROJECT_DIR:-/root/ultra-trader-src}"
LOCAL_PROJECT_DIR="${LOCAL_PROJECT_DIR:-$HOME/vps_trader/ultra-trader-src}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"
SYNC_DAYS="${SYNC_DAYS:-14}"

LOG_DIR="$LOCAL_PROJECT_DIR/data/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/sync_$(date +%Y%m%d_%H%M%S).log"

# ---- 工具 ----
log() {
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG_FILE"
}

notify_tg() {
  local msg="$1"
  if [[ -n "${TG_BOT_TOKEN:-}" && -n "${TG_CHAT_ID:-}" ]]; then
    curl -sS --max-time 10 -o /dev/null \
      "https://api.telegram.org/bot${TG_BOT_TOKEN}/sendMessage" \
      -d chat_id="${TG_CHAT_ID}" \
      -d parse_mode=Markdown \
      -d text="$msg" || true
  fi
}

on_fail() {
  local exit_code=$?
  log "❌ sync 失敗（exit=$exit_code），詳見 $LOG_FILE"
  notify_tg "🛑 *VPS sync 失敗*
host: \`$VPS_HOST\`
exit: $exit_code
log tail:
\`\`\`
$(tail -n 20 "$LOG_FILE")
\`\`\`"
  exit "$exit_code"
}
trap on_fail ERR

# ---- 預檢 ----
if [[ ! -f "$SSH_KEY" ]]; then
  log "找不到 SSH key: $SSH_KEY"
  exit 2
fi

mkdir -p "$LOCAL_PROJECT_DIR/data/performance/daily"
mkdir -p "$LOCAL_PROJECT_DIR/data"

log "開始 sync from $VPS_HOST:$VPS_PROJECT_DIR → $LOCAL_PROJECT_DIR"
log "回拉天數：$SYNC_DAYS"

# ---- 算要拉的日期清單 ----
DATES_INCLUDE=()
for i in $(seq 0 "$((SYNC_DAYS - 1))"); do
  d=$(date -d "$i days ago" +%Y-%m-%d)
  DATES_INCLUDE+=("--include=${d}_live.json")
  DATES_INCLUDE+=("--include=${d}_live_night.json")
done

# ---- 1. 同步 daily JSON ----
log "[1/2] daily JSON"
rsync -avz --timeout=60 \
  -e "ssh -i $SSH_KEY -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15" \
  "${DATES_INCLUDE[@]}" \
  --include='*/' \
  --exclude='*' \
  --prune-empty-dirs \
  "$VPS_HOST:$VPS_PROJECT_DIR/data/performance/daily/" \
  "$LOCAL_PROJECT_DIR/data/performance/daily/" \
  2>&1 | tee -a "$LOG_FILE"

# ---- 2. 同步 risk_state ----
log "[2/2] risk_state"
rsync -avz --timeout=30 \
  -e "ssh -i $SSH_KEY -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15" \
  "$VPS_HOST:$VPS_PROJECT_DIR/data/risk_state.json" \
  "$VPS_HOST:$VPS_PROJECT_DIR/data/risk_state_night.json" \
  "$LOCAL_PROJECT_DIR/data/" \
  2>&1 | tee -a "$LOG_FILE" || log "(risk_state 缺檔可接受)"

# ---- 摘要 ----
n_daily=$(find "$LOCAL_PROJECT_DIR/data/performance/daily/" -name '*_live*.json' -mtime -"$SYNC_DAYS" 2>/dev/null | wc -l)
log "✅ sync 完成。本機近 $SYNC_DAYS 天 daily JSON 計 $n_daily 個檔"

exit 0
