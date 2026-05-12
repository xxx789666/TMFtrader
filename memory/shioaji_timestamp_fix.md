---
name: Shioaji kbars timestamp format
description: Shioaji kbars.ts is a pre-shifted UTC+8 epoch; must use utcfromtimestamp to get Taiwan local time
type: project
---

Shioaji `kbars.ts[i]` values are NOT standard Unix UTC epochs. They are pre-shifted by +8h (as if the epoch counts from 1970-01-01 00:00:00 Asia/Taipei instead of UTC).

**Correct conversion** (in `broker.py` `get_historical_kbars()`):
```python
ts = datetime.utcfromtimestamp(epoch_sec)  # gives Taiwan local time (naive datetime)
```

**Wrong** (causes 8h-ahead bars):
```python
ts = datetime.fromtimestamp(epoch_sec)  # on UTC+8 machine adds another 8h → wrong!
```

**Why:** `fromtimestamp(pre_shifted_epoch)` on UTC+8 machine = Taiwan_time + 8h.
`utcfromtimestamp(pre_shifted_epoch)` = Taiwan_time (correct, because pre-shift cancels the UTC→local conversion).

Same logic applies to pandas Timestamp branch: use `.replace(tzinfo=None)` NOT `.astimezone().replace(tzinfo=None)`.

**How to apply:** Any time Shioaji REST kbars data is loaded, use `utcfromtimestamp` for int/float timestamps. The Solace WebSocket tick `tick.datetime` from Shioaji is already correct Taiwan local time — no conversion needed.

**Root cause discovered:** The old server process (PID 78012) survived `taskkill /F /IM python.exe` from bash. Use `/c/Windows/System32/taskkill.exe //F //PID <pid>` to kill specific process.
