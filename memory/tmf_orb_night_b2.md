---
name: TMF ORB Night B2 ML Filter → Phase 5 優化完成
description: TMF 夜盤 ORB 策略完整狀態（含 2026-05 Phase 0~5 重優化結果）
type: project
---

## ⚠️ 重大優化（2026-05-01）— Phase 0~5 全通過

### 3 個關鍵 Bug 已修復（strategy/orb.py）

**Bug 1 — vol_ratio 過濾位置錯誤**
- 原本：每根 K 棒都判斷 vol_ratio，設 `_entered=True` 跳過後續訊號
- 修復：將 vol_ratio 檢查移入 `if long_ok:` / `if short_ok:` 內，只在突破時判斷

**Bug 2 — trail_dist_atr > trail_trigger_atr（保證虧損退出）**
- 原本：trail_trigger=1.0×ATR，trail_dist=1.25×ATR → 觸發後立刻虧損退出
- 修復：trail_trigger=0.8×ATR，trail_dist=0.3×ATR

**Bug 3 — 無 SL 上限（極端 ATR 導致 -233pt 單筆虧損）**
- 修復：新增 `max_sl_pts=120.0` 參數，cap SL 距離

### ML Filter 停用（AUC=0.49，比隨機差）

- Train AUC=0.9808 / TMF OOS AUC=0.4871 → 嚴重 overfitting
- `ml_model_path=""` → ML disabled in engine.py
- **重訓條件**：OOS 累積 ≥ 200 筆新交易後考慮重訓 B3

---

## Phase 5 穩健性測試結果（全通過）

OOS 期間：2025-01 ~ 2026-04，n=75 筆

| Gate | 標準 | 實際值 | 判定 |
|------|------|--------|------|
| MC PF p5 | > 1.0 | 1.616 | ✓ |
| MC MDD p95 | < 12% | 5.67% | ✓ |
| MC 淨利 p5 | > 0 | +20,353 | ✓ |
| 敏感度 ΔPF | < 0.5 | 0.499 | ✓ |
| 近期 PF（9月）| > 1.2 | 1.567 | ✓ |
| 近期 MaxDD | < 8% | 3.68% | ✓ |
| 近期連虧月 | ≤ 2 | 2 | ✓ |

### 優化前後對比

| 指標 | 優化前 | 優化後 |
|------|--------|--------|
| WR | 54.7% | 78.7% |
| PF | 1.618 | 1.616 |
| 淨利 TWD | +25,849 | +20,353 |
| MaxDD | 5.18% | 3.57% |
| trail_dist | 1.25×ATR | 0.30×ATR |
| max_sl_pts | 無限制 | 120pts |
| ML | 使用中 | 停用 |

---

## 當前 engine.py 夜盤 ORB 參數

```python
ORBStrategy(
    orb_minutes=45,
    min_orb_width_atr=3.0,
    max_orb_width_atr=5.0,
    max_breakout_vol_ratio=2.0,   # Bug 1 已修復（只在突破時判斷）
    sl_atr=2.0,
    max_sl_pts=120.0,             # Bug 3 修復
    trail_trigger_atr=0.8,
    trail_dist_atr=0.3,           # Bug 2 修復（原 1.25）
    max_bars=60,
    early_cut_bars=30,
    early_cut_loss_atr=1.5,
    max_loss_twd=4000.0,
    session_start=(21, 30),
    force_close_time=(4, 0),
    ml_model_path="",             # ML 停用
    ml_features_path="",
    ml_threshold=0.40,
)
```

---

## 部署狀態

**當前狀態：Paper Trading 運行中（port 8889）**
- Phase 5 全通過 → 可繼續 Paper Trading 用新參數
- 需重啟夜盤 server 以套用 engine.py 新參數

## 雙伺服器架構

- **Port 8888**：日盤 Breakout 策略（TRADING_MODE=paper）
- **Port 8889**：夜盤 ORB 策略（TMFN，ML 停用，session 21:30-04:00）
- 各自獨立 200K 預算
- 夜盤排程：每天 **14:55** 啟動（`TMFtrader-NightORB` 工作排程器）

## 重訓觸發條件

OOS 累積 ≥ 200 筆新交易（B3 模型）
