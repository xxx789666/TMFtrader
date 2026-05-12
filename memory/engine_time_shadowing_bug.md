# engine.py `time` 遮蓋 Bug

## 根因
`core/engine.py` 頂部：
```python
import time                                      # line 10 (已移除)
from datetime import datetime, time, timedelta   # line 13 → 把 time 遮蓋成 datetime.time
```

結果：整個模組裡 `time` = `datetime.time`（不是 time 模組）。
任何 `time.sleep()` / `time.monotonic()` 呼叫都會炸：
`AttributeError: type object 'datetime.time' has no attribute 'monotonic'`

## 症狀鏈
1. heartbeat 用 `time.monotonic()` → AttributeError
2. try/except 捕捉到，每秒 log 一次錯誤
3. heartbeat 從未成功觸發
4. watchdog 偵測 12 分鐘無心跳 → 重啟
5. 重啟後同樣問題 → 永久重啟循環

## 修復（2026-04-28）
- heartbeat 計時改用 `datetime.now()` + `timedelta.total_seconds()`，不再用 `time.monotonic()`
- 移除死掉的頂層 `import time`（被 line 13 立即覆寫，從未有效）
- 移除 `_engine_loop` 內無用的 `import time as _time_mod`

## 未來 engine.py 使用 time 模組的正確方法
```python
# 在函數內部用 local import
import time as _time_mod
_time_mod.sleep(1)      # 不要用 time.sleep(1)
```

## 偵錯線索
- log 每秒出現 `[Heartbeat] type object 'datetime.time' has no attribute 'monotonic'`
- traceback 指向 comment 行（line number 偏移 → 表示 .pyc 與 .py 不同步）
- 重啟多次仍無效 → 每次都載入相同錯誤的 .pyc
