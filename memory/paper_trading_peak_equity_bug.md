---
name: Paper Trading Peak Equity 虛假回撤 Bug（重大教訓）
description: peak_equity 跨重啟殘留導致策略空轉 3 週，必須在每次診斷「風控拒絕」時優先排查此問題
type: project
---

## 問題描述

**症狀**：策略所有進場信號被風控拒絕，log 顯示：
```
[PAPER] [TMF] 風控拒絕: 回撤超限: XX.X% > 10.0%
```
但 Dashboard 顯示本金 200,000，帳戶從未有過虧損。

**影響期間**：約 3 週（2026-04-09 之後），策略完全無法進場。

---

## 根本原因

`risk/manager.py` 的 `_peak_equity` 從 `data/risk_state.json` 跨重啟恢復，但 Paper Trading 的 `MockBroker` 餘額每次重啟都重置為初始值（200,000）。

兩者不同步：
- `_balance`（MockBroker）= 200,000（每次重啟歸零）
- `_peak_equity`（risk_state.json）= 237,150（某次有盈利的 session 留下）

結果：`(237,150 - 200,000) / 237,150 = 15.7%` → 超過 max_drawdown_pct=10% → 永遠擋單。

開盤跳空時 `_position_pnl` 計算異常可能使回撤數字更大（如 29.2%），但即使平倉狀態也至少有 15.7% 的虛假回撤。

---

## 修復（2026-04-28，`core/engine.py`）

在 `initialize()` 中 RiskManager 建立後立即重置：

```python
# ---- 風控 ----
self.risk_manager = RiskManager(profile=self.risk_profile)
# Paper/Simulation 模式：MockBroker 每次重啟餘額歸零，peak_equity 必須同步重置
# 否則舊 peak 會造成虛假回撤，永遠封鎖新進場
if self.trading_mode in ("paper", "simulation"):
    self.risk_manager._peak_equity = 0.0
    logger.info("[Risk] Paper mode: peak_equity 已重置（跟隨本次啟動餘額重新計算）")
```

重啟後 log 確認修復生效：
```
[Persist] 載入風控狀態: peak=237,150 daily_loss=0 state=active
[Risk] Paper mode: peak_equity 已重置（跟隨本次啟動餘額重新計算）
[Account] balance: 200,000
```

---

## 診斷方法

每次懷疑「風控擋單」時，**第一步**先查：

```bash
cat data/risk_state.json
```

確認 `peak_equity` 是否遠高於目前 balance。若是，代表 Paper Trading 帳戶有虛假回撤問題。

---

## 日夜盤 State File 分離（2026-04-28 追加修復）

原本日夜盤共用 `data/risk_state.json`，但兩者是不同策略，peak_equity / daily_loss 應完全獨立。

**修復（`risk/persistence.py`）**：依 `DASHBOARD_PORT` 環境變數選擇不同檔案：
- 日盤 (8888)：`data/risk_state.json`
- 夜盤 (8889)：`data/risk_state_night.json`

```python
def _state_file() -> Path:
    port = os.getenv("DASHBOARD_PORT", "8888")
    suffix = "_night" if port == "8889" else ""
    return _DATA_DIR / f"risk_state{suffix}.json"
```

log 確認：`[Persist] 載入風控狀態 (risk_state.json)` / `(risk_state_night.json)` — 檔名會明確顯示。

## 預防原則

1. **Paper Trading 的 peak_equity 不應跨重啟保留**，因為 balance 也不保留
2. 風控狀態檔案（`risk_state.json`）主要為 Live Trading 設計，Paper Trading 不適用 peak_equity 持久化
3. 每次部署新版本或長時間未交易後，應檢查 `risk_state.json` 的 `peak_equity` 是否合理
4. 診斷「策略沒有進場」時，排查優先順序：
   - 風控回撤（`risk_state.json` → `peak_equity`）
   - ADX/squeeze 條件未滿足
   - KbarPoller 中斷（Scan 消失）
   - Token 過期未自癒
