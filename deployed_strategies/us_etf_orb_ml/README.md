# US ETF ORB ML Filter — 部署說明

> 建立日期：2026-04-18
> 用途：夜盤 ORB 策略，ML 過濾器**只用於 US ETFs**，TMF 維持純 ORB

---

## 策略架構

```
【US ETF 夜盤 ORB + ML Filter】
  5分K（14:30~21:00 UTC） → ORB 信號（45分鐘開盤區間突破）
                          → ML 過濾器（P ≥ threshold 才下單）→ 下單

【TMF 夜盤 ORB（純規則）】
  5分K（21:30~04:00 TST） → ORB 信號 → 直接下單（不套 ML）
```

---

## 適用商品

| 商品 | 類型 | ML 過濾 |
|------|------|---------|
| SPXL | S&P500 3x 槓桿 | ✅ 套用 ML |
| TECL | 科技 3x 槓桿  | ✅ 套用 ML |
| QQQM | Nasdaq 100     | ✅ 套用 ML |
| IVV  | S&P500 ETF     | ✅ 套用 ML |
| XLK  | 科技 ETF       | ✅ 套用 ML |
| SOXX | 半導體 ETF     | ✅ 套用 ML |
| IBB  | 生技 ETF       | ✅ 套用 ML |
| FNGS | FANG+ ETF      | ✅ 套用 ML |
| TQQQ | Nasdaq 3x 槓桿 | ✅ 套用 ML |
| TMF  | 微型台指（夜盤）| ❌ 純 ORB，不套 ML |

---

## 信號過濾條件（Method B — US ETFs）

進場前需同時滿足：
1. **ORB 寬度** ∈ [3.0, 5.0] × ATR（過濾假突破和混亂市場）
2. **EMA200 趨勢方向** — LONG 需在 EMA200 上方，SHORT 需在 EMA200 下方
3. **RSI 限制** — LONG 時 RSI ≤ 70，SHORT 時 RSI ≥ 30

---

## 模型資訊

| 項目 | 值 |
|------|----|
| 模型檔 | `optimizer/results_orb/orb_filter_v3.pkl` |
| 特徵清單 | `optimizer/results_orb/selected_features.txt` |
| 閾值 | 待 Stage 6 Optuna 重新最佳化後確定 |
| 訓練商品 | SPXL/TECL/TQQQ/QQQM/IVV/XLK/SOXX/IBB/FNGS + TMF（B1 放寬後） |
| LOIO CV Mean AUC | 0.6365（Method B v3） |

---

## 部署流程

```
Phase 1（當前）：
  - US ETF ORB 信號產生 → ML 過濾（P ≥ threshold）→ 下單
  - TMF ORB 信號產生 → 直接下單（不套 ML）
  - Paper Trading 驗證 3 個月

Phase 2（3 個月後評估）：
  - 若 US ETF 部分 WR ≥ 55%、PF ≥ 2.0 → 上線
  - 評估 TMF 是否累積足夠 OOS 樣本後納入 ML
```

---

## 參考報告

- ORB ML 訓練報告：`deployed_strategies/tmf_orb_night/ML過濾器訓練報告.md`
- ORB 穩健性報告：`optimizer/results_orb/robustness_report_v3.md`
