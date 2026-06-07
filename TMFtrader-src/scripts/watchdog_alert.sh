#!/usr/bin/env bash
# scripts/watchdog_alert.sh — Linux 告警型看門狗（cron 每 5 分鐘觸發）
#
# 設計原則（2026-05-26）：只「偵測 + 推 TG」,絕不自動重啟、絕不呼叫任何 HTTP/Shioaji API。
#   純讀本機 PID 檔 + log mtime + curl TG。失敗一律靜默。
#
# 2026-06-03 改版（3 策略 TMF live cutover 後）：
#   舊版監測 scripts/start.py（已不存在,live 改 start_paper.py --mode live）+ night_orb.py（已暫停）,
#   會誤報且漏掉新 live。改為:
#   1. 三支 live(breakout_v7/day_orb/night_v3)各自 PID 檔是否還活著（各有重啟 grace 窗;PID 檔不存在=尚未啟動,不報）
#   2. 凍結偵測:交易時段內最新的 *_live log 檔 mtime 是否 >12 分鐘沒更新（與 log 格式無關）
# 同一告警 30 分鐘內不重複推。
set -u
export TZ=Asia/Taipei

PROJ=/home/xx/TMFtrader-src
LOG_DIR="$PROJ/data/logs"
WLOG="$LOG_DIR/watchdog_alert.log"
DAY=$(date +%Y%m%d)
COOLDOWN=1800        # 同一告警 30 分鐘冷卻
FREEZE_MAX=720       # 12 分鐘沒更新視為凍結
mkdir -p "$LOG_DIR"

HHMM=$((10#$(date +%H%M)))   # 0905 → 905（避免 08 被當八進位）
EPOCH=$(date +%s)
wlog(){ echo "[$(date '+%F %T')] $*" >> "$WLOG"; }

TG_TOKEN=$(grep '^TG_BOT_TOKEN=' "$PROJ/.env" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
TG_CHAT=$(grep '^TG_CHAT_ID='  "$PROJ/.env" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
send_tg(){
  [ -n "$TG_TOKEN" ] && [ -n "$TG_CHAT" ] && \
    curl -sS --max-time 10 -o /dev/null \
      "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${TG_CHAT}" --data-urlencode "text=$1" 2>/dev/null || true
}
alert(){
  local key="$1" msg="$2" sf="/tmp/TMFtrader_wd_alert_$1" last=0
  [ -f "$sf" ] && last=$(cat "$sf" 2>/dev/null || echo 0)
  if [ $((EPOCH - last)) -ge $COOLDOWN ]; then
    echo "$EPOCH" > "$sf"; wlog "ALERT[$key] $msg"; send_tg "🐶 [Watchdog] $msg"
  else
    wlog "skip[$key]（冷卻中）$msg"
  fi
}

# ── 1. 三支 live 進程存活（PID 檔 + 重啟 grace 窗;PID 檔不存在=尚未啟動,不報）
check_live(){
  local owner="$1" g1="$2" g2="$3"
  if [ $HHMM -ge $g1 ] && [ $HHMM -le $g2 ]; then wlog "$owner 重啟窗、跳過"; return; fi
  local pidf="/tmp/TMFtrader_${owner}_live.pid" pid=""
  [ -f "$pidf" ] && pid=$(cat "$pidf" 2>/dev/null || echo "")
  if [ -z "$pid" ]; then wlog "$owner 無 PID 檔（尚未啟動）、跳過"; return; fi
  if kill -0 "$pid" 2>/dev/null; then wlog "$owner live OK (pid $pid)"
  else alert "${owner}_live" "🚨 live $owner 不在了（pid $pid 已歿）@ $(date '+%m/%d %H:%M')"; fi
}
# 2026-06-08 起 live 只 2 支:day_v7(=breakout_v7)+ night_v7（取代 night_v3;day_orb/aft_orb 已下架）
check_live breakout_v7 813 820   # 08:15 cron 重啟（day_v7）
check_live night_v7    1448 1455 # 14:50 cron 重啟（夜盤 30m 全夜盤）

# ── 2. 凍結偵測（交易時段:日 08:45–13:45、夜 15:00–翌05:00;用最新 live log mtime）
trading=0
[ $HHMM -ge 845 ] && [ $HHMM -le 1345 ] && trading=1
{ [ $HHMM -ge 1500 ] || [ $HHMM -le 500 ]; } && trading=1
if [ $trading -eq 1 ]; then
  # *_live_ 萬用前綴 → 同時抓 TMF(breakout_v7_live_…)與 MXF(breakout_v7_mxf_live_…)log
  newest=$(ls -t "$LOG_DIR"/*_live_$DAY.log 2>/dev/null | head -1)
  if [ -n "$newest" ] && [ -f "$newest" ]; then
    age=$((EPOCH - $(stat -c %Y "$newest")))
    if [ $age -gt $FREEZE_MAX ]; then
      alert "freeze" "⚠️ live log 已 $((age/60)) 分鐘沒更新（疑凍結）$(basename "$newest")"
    else
      wlog "live log fresh (${age}s 前) $(basename "$newest")"
    fi
  else
    wlog "交易時段但無當日 *_live log、跳過凍結檢查"
  fi
else
  wlog "非交易時段、跳過凍結檢查"
fi
