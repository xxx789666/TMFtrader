#!/bin/bash
# Driver — 順序跑 _bisect_setters.py、找第一個撞死的 round
# 用法：bash _run_bisect.sh [observe_sec]
# default observe = 120s (expert 建議 180s、但 fix2 10 秒就死、120s 應足夠)

cd /home/xx/TMFtrader-src
source .venv/bin/activate

OBSERVE_SEC=${1:-120}
MAX_ROUND=12
RESULT_DIR=/tmp/bisect_$(date +%H%M%S)
mkdir -p $RESULT_DIR
echo "Result dir: $RESULT_DIR"

# 起點 round 0 (only tick) 已被 min-verify 5 分鐘穩定驗證、可跳過
# 從 round 1 (+order) 開始
for ROUND in $(seq 1 $MAX_ROUND); do
  LOG=$RESULT_DIR/round_$ROUND.log
  echo "========================================"
  echo "=== ROUND $ROUND start $(date '+%H:%M:%S') ==="
  python3 /home/xx/TMFtrader-src/scripts/_bisect_setters.py $ROUND $OBSERVE_SEC > $LOG 2>&1
  EXIT_CODE=$?

  if grep -q "SURVIVED" $LOG; then
    echo "ROUND $ROUND: SURVIVED ($OBSERVE_SEC s)"
  else
    echo "ROUND $ROUND: ❌ DIED (exit=$EXIT_CODE) ← CULPRIT"
    echo "--- last log lines ---"
    tail -10 $LOG
    echo "--- end ---"
    echo
    echo "=== Culprit found: ROUND $ROUND ===" | tee $RESULT_DIR/SUMMARY.txt
    grep -E "registered|SKIP|ERR" $LOG | tail -20 >> $RESULT_DIR/SUMMARY.txt
    break
  fi
done

echo
echo "=== All rounds done. Result dir: $RESULT_DIR ==="
ls -la $RESULT_DIR
