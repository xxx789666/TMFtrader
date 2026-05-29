---
name: Watchdog Weekend Suppression & Token Expiry Fix
description: Watchdog 設計：週末靜默 TG（但允許重啟）、補班日支援、Shioaji token 每日過期自癒、雙伺服器監控、夜盤心跳改用 Heartbeat TMFN、TG 加 MODE_LABEL、每小時推「策略運行正常」
type: project
---

## 已知問題：Shioaji JWT Token 每日過期

- Token 有效期：**24 小時**，每日 00:04 UTC+8 過期（以登入時間為準）
- 過期後 KbarPoller 全部失敗 → K 線凍結 → 無 [Scan] 記錄
- 自癒觸發路徑：`[Scan] 超過 12 分鐘無更新` → watchdog 偵測 → 自動重啟 server → 新 token
- **根本解決**：Task Scheduler 每天 14:55 強制重啟夜盤伺服器（`start_night_server.bat` 殺舊程序再啟新），確保 JWT 每日刷新

**2026-04-20 事故**：`_can_restart()` 因 `is_market_day()=False`（補班周日）封鎖重啟 → 卡死 9 小時，手動介入修復。

---

## 修復後設計（scripts/watchdog.py）

### `is_market_day()`
```python
def is_market_day() -> bool:
    return datetime.now(TW_TZ).weekday() < 5
```
**只用於 TG 通知過濾**，不再用於封鎖重啟。

### `_can_restart()` — 移除 weekday 封鎖
```python
def _can_restart(self) -> bool:
    # 不依賴 is_market_day()，補班日（周日開盤）也需能重啟
    if in_window(CLOSE_WINDOWS):
        return False
    elapsed = time.time() - self._last_restart
    if elapsed < RESTART_COOLDOWN:
        return False
    return True
```

### `in_trading_session()` — 純時間判斷，不依賴星期
```python
def in_trading_session() -> bool:
    return in_window(TRADING_SESSIONS)  # 補班日適用
```

**Why:** `weekday() < 5` 無法識別台灣補班日（如 2026-04-20 周日開盤）。`_can_restart()` 封鎖補班日重啟會導致 token 過期後無法自癒。

**How to apply:**
- `is_market_day()` → 僅用於 `tg()` 推播判斷，避免週末 TG 洗版
- `_can_restart()` → 只看 CLOSE_WINDOWS（收盤前 20 分鐘）+ 冷卻時間
- `in_trading_session()` → 只看時間，補班日自然適用

---

## 雙伺服器監控（`--night` 參數）

- 日盤 Watchdog：`python scripts/watchdog.py`（監控 port 8888，log: watchdog.log）
- 夜盤 Watchdog：`python scripts/watchdog.py --night`（監控 port 8889，log: watchdog_night.log）
- 夜盤啟動腳本：`start_night_watchdog.bat`
- 夜盤重啟指令：執行 `start_night_server.bat`（而非 `scripts/start.py`）

---

## 週末 Solace 斷線（原始問題，仍有效）

週末 Solace 無 tick → fallback 模式 → `is_market_day()=False` → tg() 不推播 → 不重啟
（正常現象，`_can_restart()` 仍由 CLOSE_WINDOWS + 冷卻保護，不會無限循環）

---

## TMFN 夜盤 tick 路由 Bug（2026-04-20 發現修復）

**根本原因**：Shioaji 訂閱 `MXFR1`（近月別名），但 Solace WebSocket 回傳 tick 的 `tick.code` 是實際交割月代碼 `MXFE6`。
- `_code_to_instrument` 只有 `"MXFR1" → "TMFN"`，找不到 `"MXFE6"`
- fallback prefix 比對：`"MXFE6".startswith("TMFN") = False` → instrument=""
- `_last_tick_time` 仍更新（tick 進來了）→ KbarPoller 不啟動
- engine `_process_tick()` 丟棄 instrument="" 的 tick → current_price 不更新
- 結果：heartbeat 卡在 warmup 價、K 線不動、ORB 策略無 on_kbar 呼叫

**TMF 不受影響原因**：instrument code "TMF" 是 "TMFE6" 的前綴 → prefix fallback 成功。

**修復（core/broker.py）**：在 connect() 和 reconnect() 的 `_code_to_instrument` 建立時，同時加入 `contract.target_code` 映射：
```python
self._code_to_instrument[contract.code] = code        # MXFR1 → TMFN
if hasattr(contract, 'target_code') and contract.target_code:
    self._code_to_instrument[contract.target_code] = code  # MXFE6 → TMFN
```

**How to apply:** 任何 shioaji_code ≠ instrument_code（如 TMFN→MXF）的商品都需此修復。TMF 因名稱前綴巧合無需修復。

---

## TG 訊息模式標籤（2026-04-20 新增）

所有 TG 訊息現在加入 `[{_MODE_LABEL}]`，例如：
- `[TMFtrader] [夜盤(8889)] 伺服器重啟`
- `[TMFtrader] [日盤(8888)] 策略運行正常`

**新增變數：**
- `_MODE_LABEL = "夜盤(8889)"` 或 `"日盤(8888)"`
- `_SCAN_LABEL = "Heartbeat TMFN"` 或 `"Scan"`
- `_SCAN_RE_DAY` / `_SCAN_RE_NIGHT`（模式切換）

---

## 夜盤 ORB 心跳偵測（2026-04-20 修改 / 2026-04-27 根本修復）

ORB 策略不寫 `[Scan]`，夜盤 watchdog 改偵測 `[Heartbeat] TMFN:`。

```python
_SCAN_RE_DAY   = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*\[Scan\]")
_SCAN_RE_NIGHT = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*\[Heartbeat\] TMFN:")
_SCAN_RE = _SCAN_RE_NIGHT if NIGHT_MODE else _SCAN_RE_DAY
_SCAN_LABEL = "Heartbeat TMFN" if NIGHT_MODE else "Scan"
```

### 根本問題（2026-04-27 修復）

**舊設計缺陷**：`core/engine.py` 的心跳以 `queue.Empty` 計數為基礎：
```python
except queue.Empty:
    self._heartbeat_count += 1
    if self._heartbeat_count % 60 == 0:  # 每 60 次 empty 才觸發
        self._heartbeat()
```
夜盤行情活躍時 tick 頻繁，`queue.Empty` 很少發生 → 心跳可能 60～120 分鐘才出現一次 → watchdog 12 分鐘超時誤報 → 無限重啟迴圈。

**修復（`core/engine.py`）**：改用 wall-clock timer：
```python
# __init__ 新增：
self._last_heartbeat_time = 0.0

# event loop：
except queue.Empty:
    self._heartbeat_count += 1
    _now = time.monotonic()
    if _now - self._last_heartbeat_time >= 60.0:
        self._last_heartbeat_time = _now
        self._heartbeat()
```
同時在 import 區加入 `import time`（原本只有 `from datetime import ... time ...`）。

**驗證（2026-04-27 22:22-22:25）**：心跳穩定每 ~60s 出現，watchdog 持續 `[OK] 最後心跳 45-108s 前`，無誤報重啟。

---

## 「策略運行正常」每小時 TG 推播（2026-04-20 新增）

心跳正常時，每小時最多推一次（交易日限定）：
```python
_OK_TG_INTERVAL = 3600  # class-level 常數
self._last_ok_tg: float = 0.0
# tick() 中心跳 OK 分支：
if is_market_day() and (time.time() - self._last_ok_tg) > self._OK_TG_INTERVAL:
    self._last_ok_tg = time.time()
    tg(f"[TMFtrader] [{_MODE_LABEL}] 策略運行正常\n心跳: {age:.0f}s 前\n引擎: {state.get('engine_state','?')}\n時間: ...")
```

---

## Night Watchdog 手動啟動方式（bat 在 bash 中無效時）

`start_night_watchdog.bat` 用 `start /MIN pythonw`，在 bash 子程序中無法正常 spawn。
改用 PowerShell：
```powershell
Start-Process -FilePath 'pythonw.exe' -ArgumentList 'scripts\watchdog.py --night' -WorkingDirectory 'C:\...\TMFtrader-src' -WindowStyle Hidden
```

重啟雙 watchdog（升級代碼後）：
```powershell
Stop-Process -Id <日盤PID> -Force
Stop-Process -Id <夜盤PID> -Force
Start-Process -FilePath 'pythonw.exe' -ArgumentList 'scripts\watchdog.py' -WorkingDirectory '...' -WindowStyle Hidden
Start-Process -FilePath 'pythonw.exe' -ArgumentList 'scripts\watchdog.py --night' -WorkingDirectory '...' -WindowStyle Hidden
```

---

## 休盤靜默修復（2026-04-21）

**問題**：夜盤 05:00 收盤後 server 正常下線，watchdog 仍反覆嘗試重啟並發 TG，從 04:15 傳到 08:07+。

**根本原因**：
- `_can_restart()` 只封鎖 `CLOSE_WINDOWS`（04:45-05:00），05:00 後空窗期不封鎖
- server 無回應時無論是否在交易時段都會嘗試重啟
- Solace / CB TG 也沒有 trading session 保護

**修復（scripts/watchdog.py）**：

1. `_can_restart()` 加入 `GAP_WINDOWS` 封鎖：
```python
if in_window(GAP_WINDOWS):
    wlog("[Skip] 日夜交接空窗期，不重啟")
    return False
```

2. server 無回應時先檢查是否在交易時段：
```python
if state is None:
    if not in_trading_session():
        wlog("[Check] 伺服器無回應（休盤中，正常）")
        return
    wlog("[Check] 伺服器無回應 [ERR]")
    if self._can_restart():
        self._do_restart("伺服器無回應")
    return
```

3. CB resume / Solace TG 加 `in_trading_session()` 保護：
```python
if in_window(GAP_WINDOWS) or not in_trading_session():
    wlog("[CB] 非交易時段，跳過 resume")
# Solace TG
if fb_min < 2 and is_market_day() and in_trading_session():
    tg(...)
if is_market_day() and in_trading_session():
    tg(...)  # Solace 恢復
```

**How to apply:** 任何新增 TG 通知的地方，都應同時加 `is_market_day()` + `in_trading_session()` 雙重保護。
