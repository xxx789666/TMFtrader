#!/usr/bin/env bash
# 一鍵看 VPS 今日所有交易紀錄（日盤 + 夜盤）
VPS=xx@35.221.239.245
KEY=~/.ssh/google_compute_engine

# 用 Asia/Taipei 當日日期（不論本機在哪時區）
TODAY=$(TZ=Asia/Taipei date +%F)
TODAY_COMPACT=$(TZ=Asia/Taipei date +%Y%m%d)

ssh -i $KEY -o IdentitiesOnly=yes $VPS bash -s <<REMOTE
TODAY=$TODAY
TODAY_COMPACT=$TODAY_COMPACT
TZ=Asia/Taipei

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "   VPS 交易紀錄速覽 ($TODAY)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

echo
echo "▌ 1. 日盤（breakout / TMF）"
DAY_JSON=~/ultra-trader-src/data/performance/daily/\${TODAY}_live.json
if [ -f "\$DAY_JSON" ]; then
  python3 <<PY
import json
with open("\$DAY_JSON") as f:
    d = json.load(f)
trades = d.get("trades", [])
signals = d.get("paper_signals", [])
print(f"  mode={d.get('trading_mode','?')} trades={len(trades)} signals={len(signals)}")
for t in trades:
    side = t.get('side','?').upper()
    ep, xp = t.get('entry_price','?'), t.get('exit_price','?')
    pnl = t.get('net_pnl', t.get('pnl','?'))
    mfe = t.get('max_favorable',0)
    mae = t.get('max_adverse',0)
    et = t.get('entry_time','?')[:19]
    xt = t.get('exit_time','?')[:19]
    reason = (t.get('reason','?') or '')[:40]
    print(f"  {et} {side} @{ep} → {xt} @{xp} pnl={pnl:+} MFE={mfe} MAE={mae} | {reason}")
if signals and not trades:
    print(f"  (有訊號但無進場、可能 risk 阻擋)")
    for s in signals[-3:]:
        print(f"    {s.get('time','?')[:19]} {s.get('action','?')} @{s.get('price','?')} | {s.get('reason','')[:60]}")
PY
else
  echo "  (今日尚無日盤紀錄)"
fi

echo
echo "▌ 2. 夜盤 ORB CSV（MXF / B2 ML）"
NIGHT_CSV=~/ultra-trader-src/data/paper_trading/live_night_orb_\${TODAY_COMPACT}.csv
[ ! -f "\$NIGHT_CSV" ] && NIGHT_CSV=~/ultra-trader-src/data/paper_trading/night_orb_\${TODAY_COMPACT}.csv
if [ -f "\$NIGHT_CSV" ]; then
  TOTAL=\$(wc -l < "\$NIGHT_CSV")
  echo "  總行數: \$TOTAL（首行 = header、>1 = 有交易）"
  if [ "\$TOTAL" -gt 1 ]; then
    echo "  最近 5 筆："
    head -1 "\$NIGHT_CSV"
    tail -5 "\$NIGHT_CSV"
  else
    echo "  (尚無 ORB 觸發、夜盤 21:30-04:00 才會建立區間)"
  fi
else
  echo "  (CSV 尚未建立、夜盤 14:55 啟動後才建)"
fi

echo
echo "▌ 3. 夜盤 breakout JSON（如果 breakout 夜盤有訊號才有 — 現在已關閉）"
NIGHT_JSON=~/ultra-trader-src/data/performance/daily/\${TODAY}_live_night.json
[ -f "\$NIGHT_JSON" ] && cat "\$NIGHT_JSON" | python3 -m json.tool 2>&1 | head -30 || echo "  (無、breakout 夜盤已關閉、預期內)"

echo
echo "▌ 4. 風控狀態（risk_state）"
echo "  日盤："
cat ~/ultra-trader-src/data/risk_state.json 2>/dev/null | python3 -m json.tool 2>&1 | head -10
echo
echo "  夜盤："
cat ~/ultra-trader-src/data/risk_state_night.json 2>/dev/null | python3 -m json.tool 2>&1 | head -10

echo
echo "▌ 5. 今日 Engine log 內訊號類事件（過濾顯示）"
LOG=~/ultra-trader-src/data/logs/ultratrader_\${TODAY_COMPACT}.log
if [ -f "\$LOG" ]; then
  echo "  Signal / Entry / Exit / Order / ORB 相關："
  grep -iE 'Signal|Entry|Exit|\[Order\]|place_order|fired|breakout SHORT|breakout LONG|A-Squeeze|ORB.*Range' "\$LOG" 2>/dev/null | tail -15
  echo "  (空 = 還沒觸發任何訊號)"
fi

echo
echo "▌ 6. 跑中的 process"
pgrep -fa "scripts/start.py|night_orb.py|watchdog.py" || echo "  ✗ 沒有 process 在跑！"
REMOTE
