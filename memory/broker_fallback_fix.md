---
name: KbarPoller fallback_since Bug Fix
description: broker.py KbarPoller 的 _fallback_since 在 REST 也失敗時不設定的 bug，導致 watchdog 無法偵測 K 線凍結
type: project
---

## Bug（2026-04-23 修復）

**位置**：`core/broker.py` `_start_kbar_poller()` 內的 `_poll()` 函式

**根本原因**：`_fallback_since` 只在 KbarPoller **成功**取得 REST 資料時才設定。若 REST API 也失敗（exception），`_fallback_since` 永遠為 None → `fallback_active = False` → watchdog 的 fallback 偵測完全失效。

**Why:** TMFN 收盤後（04:00 起）Solace 斷線 + REST 也回空（休市），兩個失敗路徑都走不到設定 `_fallback_since` 的那行。Watchdog 看不到 fallback，伺服器整天帶著舊連線跑，隔天 14:55 Task Scheduler 因 port 已在監聽跳過重啟（舊版 bat）→「每天 K 線凍結」。

**修復**：將 `_fallback_since` 的設定移到「偵測到 Solace 靜止」的判斷點（`elapsed > interval`），在 REST 嘗試**之前**：

```python
if elapsed > interval:
    # 立即標記 fallback（不論 REST 是否成功）
    if self._fallback_since is None:
        self._fallback_since = time_module.monotonic()
        logger.warning(f"[KbarPoller] Solace 斷線，切換 fallback 模式")
    # 之後才嘗試 REST
    for code, contract in ...:
        try:
            ...
        except Exception as e:
            logger.warning(f"[KbarPoller] {code} 失敗: {e}")
```

**How to apply:** 修復後的行為：
- 04:00 TMFN 收盤，Solace 靜止 → `_fallback_since` 立即設定
- Watchdog 在 15:01（GAP_WINDOW 結束）偵測 `fallback_minutes > 10` → 自動重啟伺服器
- 配合 `start_night_server.bat` 強制殺舊程序 → 每天 15:01 自動刷新連線（即使 14:55 bat 因某原因未執行）
