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
# 告警去重（2026-07-27 user:別一直推一樣的）:首報立推;同一告警持續中每 REMIND_EVERY 才
# 提醒一次(標持續時長);恢復時推一則 ✅ 並清狀態。
set -u
export TZ=Asia/Taipei

PROJ=/home/xx/TMFtrader-src
LOG_DIR="$PROJ/data/logs"
WLOG="$LOG_DIR/watchdog_alert.log"
DAY=$(date +%Y%m%d)
REMIND_EVERY=14400   # 同一告警持續中,每 4 小時才再提醒一次
FREEZE_MAX=720       # 12 分鐘沒更新視為凍結
mkdir -p "$LOG_DIR"

HHMM=$((10#$(date +%H%M)))   # 0905 → 905（避免 08 被當八進位）
EPOCH=$(date +%s)
wlog(){ echo "[$(date '+%F %T')] $*" >> "$WLOG"; }

# 市場休市日:引擎本就不啟動(launcher 休市 guard)→ 跳過所有檢查,不誤報 down/凍結(2026-06-19 端午)。
if grep -qx "$(date +%F)" "$PROJ/scripts/market_holidays.txt" 2>/dev/null; then
  wlog "市場休市 → 跳過看門狗檢查"; exit 0
fi

TG_TOKEN=$(grep '^TG_BOT_TOKEN=' "$PROJ/.env" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
TG_CHAT=$(grep '^TG_CHAT_ID='  "$PROJ/.env" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'")
send_tg(){
  [ -n "$TG_TOKEN" ] && [ -n "$TG_CHAT" ] && \
    curl -sS --max-time 10 -o /dev/null \
      "https://api.telegram.org/bot${TG_TOKEN}/sendMessage" \
      --data-urlencode "chat_id=${TG_CHAT}" --data-urlencode "text=$1" 2>/dev/null || true
}
alert(){
  # 狀態檔存兩個 epoch:「首見 最後推播」。舊格式(單一 epoch)相容:last 缺→0→會立即提醒一次後歸靜。
  local key="$1" msg="$2" sf="/tmp/TMFtrader_wd_alert_$1" first=0 last=0
  [ -f "$sf" ] && { read -r first last < "$sf" 2>/dev/null || true; }
  first=${first:-0}; last=${last:-0}
  if [ "$first" -eq 0 ]; then
    echo "$EPOCH $EPOCH" > "$sf"
    wlog "ALERT[$key] $msg"; send_tg "🐶 [Watchdog] $msg"
  elif [ $((EPOCH - last)) -ge $REMIND_EVERY ]; then
    echo "$first $EPOCH" > "$sf"
    local hrs=$(( (EPOCH - first) / 3600 ))
    wlog "REMIND[$key] $msg"
    send_tg "🐶 [Watchdog] $msg（已持續約 ${hrs}h;同一告警每 $((REMIND_EVERY/3600))h 提醒一次）"
  else
    wlog "skip[$key]（去重:首報已推、持續中）"
  fi
}
clear_alert(){
  # 狀態由「異常」轉「正常」→ 推一則恢復通知並清狀態(沒異常過=靜默)。
  local key="$1" sf="/tmp/TMFtrader_wd_alert_$1" first=0 last=0
  [ -f "$sf" ] || return 0
  read -r first last < "$sf" 2>/dev/null || true
  rm -f "$sf"
  local mins=$(( (EPOCH - ${first:-$EPOCH}) / 60 ))
  wlog "RECOVER[$key]"; send_tg "🐶✅ [Watchdog] $key 恢復（中斷約 ${mins} 分鐘）"
}

# ── 1. 三支 live 進程存活（PID 檔 + 重啟 grace 窗;PID 檔不存在=尚未啟動,不報）
check_live(){
  local owner="$1" g1="$2" g2="$3"
  if [ $HHMM -ge $g1 ] && [ $HHMM -le $g2 ]; then wlog "$owner 重啟窗、跳過"; return; fi
  local pidf="/tmp/TMFtrader_${owner}_live.pid" pid=""
  [ -f "$pidf" ] && pid=$(cat "$pidf" 2>/dev/null || echo "")
  if [ -z "$pid" ]; then wlog "$owner 無 PID 檔（尚未啟動）、跳過"; return; fi
  if kill -0 "$pid" 2>/dev/null; then wlog "$owner live OK (pid $pid)"; clear_alert "${owner}_live"
  else alert "${owner}_live" "🚨 live $owner 不在了（pid $pid 已歿）@ $(date '+%m/%d %H:%M')"; fi
}
# 2026-06-08 起 live 只 2 支:day_v7(=breakout_v7)+ night_v7（取代 night_v3;day_orb/aft_orb 已下架）
check_live breakout_v7 813 820   # 08:15 cron 重啟（day_v7）
check_live night_v7    1448 1455 # 14:50 cron 重啟（夜盤 30m 全夜盤）
check_live chips_exec   815 830  # 08:20 cron 重啟(2026-07-22 補:7/16 轉 live 後漏掛)
check_live maxpain_exec 815 830  # 08:21 cron 重啟(2026-07-03 轉 live 後漏掛)

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
      wlog "live log fresh (${age}s 前) $(basename "$newest")"; clear_alert "freeze"
    fi
  else
    wlog "交易時段但無當日 *_live log、跳過凍結檢查"
  fi
else
  wlog "非交易時段、跳過凍結檢查"
fi

# ── 3. requote 引擎呆單守護（2026-08-01,TING 部署方設計移植:狀態檔驅動,持續告警到人工清除）
#   原理:看到引擎在跑→記狀態檔;之後行程消失且最近 requote log 沒寫「收工」→ 可能有呆單。
#   alert() 的 4h 提醒節奏持續叫,直到人工 rm 狀態檔。「收工」字樣由引擎保證只在
#   verify_no_residual 確認無殘留掛單時寫入(同日引擎端修正,撤單失敗的退場訊息不含此二字)。
RQ_ST=/tmp/TMFtrader_wd_requote_was_running
if pgrep -f "fishing_requote_engine.py" >/dev/null 2>&1; then
  touch "$RQ_ST"; wlog "requote 引擎運行中"
elif [ -f "$RQ_ST" ]; then
  rq_lg=$(ls -t "$PROJ/data/requote_live.log" "$PROJ/data/requote_shadow.log" 2>/dev/null | head -1)
  if [ -n "$rq_lg" ] && tail -5 "$rq_lg" | grep -q "收工"; then
    rm -f "$RQ_ST"; clear_alert "requote_orphan"; wlog "requote 正常收工"
  else
    alert "requote_orphan" "🆘 requote 引擎消失且未走收工流程 → 可能有呆單掛在市場!立即開券商 App 查未成交委託並撤光;處理完執行 rm $RQ_ST 停止提醒"
  fi
fi

# ── 4. log 黑洞偵測（2026-08-01,TING 方 8/1 實際事故移植:fd 指向已刪 inode=行程健康、log 永不增長）
#   先收集後判定(健康行程不可清掉不健康行程的告警);唯一解=重啟該行程。
bh=""
for p in $(pgrep -f "fishing_live_paper.py" 2>/dev/null; pgrep -f "fishing_requote_engine.py" 2>/dev/null); do
  readlink "/proc/$p/fd/1" 2>/dev/null | grep -q '(deleted)' && bh="$bh $p"
done
if [ -n "$bh" ]; then
  alert "log_blackhole" "🟠 pid$bh 的 stdout 指向已刪除檔案 — log 進黑洞,行程健康但只有重啟能恢復記錄(查誰 mv/rm 過 log、或 logrotate 誤用 rename 式輪替)"
else
  clear_alert "log_blackhole"
fi
