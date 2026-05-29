# vwap_fade 穩健性報告 — 2026-05-28

## 執行環境
- 分支: feat/vwap-fade
- Python: Windows 11 Pro, Numba JIT CPU backend
- 資料截點: MXF/TXF 2020-03-22 ~ 2026-05-27 (90k+ bars); TMF OOS 2024-07-29 ~ 2026-05-27

---

## 1. 選定最佳參數

| 欄位 | 值 |
|---|---|
| 來源 | MXF grid (Top-1 wf_score across union of MXF + TXF 324 combos) |
| k | 2.0 |
| k2 | 3.0 |
| sigma_window | 20 |
| adx_max | 25 |
| max_bars | 36 |
| Train wf_score | (MXF train: n=1349, WR=37.7%, PF=0.611, Ret=-48.9%, DD=2.74%) |
| Test wf_score | 0.2882 (MXF test: n=774, WR=42.6%, PF=0.852, Ret=-15.4%, DD=25.9%) |

TXF Top-1: {k=2.5, k2=2.5, sigma_window=40, adx_max=25, max_bars=18}, wf_score=0.2658

---

## 2. Gate 結果表

| Gate | 門檻 | 結果 | PASS/FAIL |
|---|---|---|---|
| P0 成本 | net_pnl 扣 50/口 | 已驗 (Task 1) | PASS |
| P3 MXF/TXF 參數重疊 k | MXF∩TXF Top-10 範圍相交 | MXF[2.0,2.5] ∩ TXF[2.0,2.5] | PASS |
| P3 MXF/TXF 參數重疊 k2 | 同上 | MXF[2.5,3.0] ∩ TXF[2.5,3.0] | PASS |
| P3 MXF/TXF 參數重疊 sigma_window | 同上 | MXF[20,40] ∩ TXF[20,40] | PASS |
| P3 MXF/TXF 參數重疊 adx_max | 同上 | MXF[25,35] ∩ TXF[25,35] | PASS |
| P3 MXF/TXF 參數重疊 max_bars | 同上 | MXF[18,36] ∩ TXF[18,36] | PASS |
| 4-1 MC PF p5 | > 1.0 | 0.7975 | **FAIL** |
| 4-1 MC 淨利 p5 | > 0 | -35,350 TWD | **FAIL** |
| 4-1 MC MDD p95 | < 12% | 23.59% | **FAIL** |
| 4-2 穩定度 k | < 0.3 | 0.1458 | PASS |
| 4-2 穩定度 k2 | < 0.3 | 0.0935 | PASS |
| 4-2 穩定度 sigma_window | < 0.3 | 0.1378 | PASS |
| 4-2 穩定度 adx_max | < 0.3 | 0.2664 (worst) | PASS |
| 4-2 穩定度 max_bars | < 0.3 | 0.0110 | PASS |
| 4-3 震盪日淨利 (range) | > 0 | -18,080 TWD | **FAIL** |
| 4-3 趨勢日淨利 (trend) | > -50% \|range_net\| | n=0 (adx_max=25 過濾趨勢日不入場) | INSUFFICIENT |
| P5 OOS PF | > 1.2 | 0.7975 | **FAIL** |
| P5 頻率 | 1-3 筆/日 | 1.31 筆/日 | PASS |
| P5 \|r\| breakout | < 0.3 | BreakoutStrategy import 失敗 | deferred |
| P5 VWAP 近似 | 進場重疊 > 95% | session VWAP 計算 OK，深度比對 deferred | deferred |

**整體判決: FAIL — 進入 paper 前需修策略或調參**

---

## 3. 觀察與建議

### 通過的 Gate
- **P3 雙商品參數重疊**: 全 5 維度均重疊，證明找到的參數區間對 MXF / TXF 均一致，無明顯過擬合單一商品。
- **P0 成本**: 已在 Task 1 驗證，手續費 50/口 已納入回測引擎。
- **Gate 4-2 參數穩定度**: worst dim adx_max=0.2664 < 0.3，策略對參數小幅擾動具備韌性，沒有「剃刀邊緣」問題。
- **交易頻率**: 1.31 筆/日，落在 1-3 的目標區間。

### 未通過的 Gate
1. **Gate 4-1 (MC) 全 FAIL**:
   - OOS 583 筆交易，PF=0.797, 淨虧損 -35,350 TWD (-17.7%)。
   - MC shuffle 後 PF p5=0.797（與原樣完全相同，因 MC 不改變 PF 分子分母比，每次 shuffle 結果幾乎一致）。
   - 根本問題：策略在 OOS 期間本質上是虧損的，MC 只是把虧損的分布揭示出來。

2. **Gate 4-3 震盪日 FAIL**:
   - adx_max=25 使策略「只在低 ADX 日進場」，但低 ADX 日 (ADX < 20 = range bucket) 同樣虧損 -18,080 TWD。
   - 趨勢日 bucket 為空（adx_max=25 本來就不在趨勢日進場），門檻無法評估。
   - 核心發現：**策略的 VWAP 均值回歸邏輯在 TMF OOS 期間無論 ADX 高低均無效**。

3. **P5 OOS PF FAIL**:
   - OOS PF=0.797，距離門檻 1.2 有相當差距。

### 根因分析
- Train (2020-2023) vs OOS (2024-07 起) 存在 **結構性斷層**：
  - 月別表顯示 2025-02 WR=17%、2026-01 WR=30%，多數月份淨虧，僅 2026-03 (+11,190)、2026-04 (+4,140)、2024-07、2025-08、2025-09、2025-11 月獲利。
  - 2024 後台指盤中跳動模式可能因高利率環境、外資回流、ETF 申贖等因素改變，VWAP 回歸速度放慢，造成 max_bars=36 仍常常 timeout exit。

### 對策選項

**選項 A: 調整出場條件 (優先)**
- 增加 TP 倍數（k2 從 3.0 提高到 4.0+）或縮短 max_bars（18 bars 在 TXF 表現較好）。
- 重新 grid，SPLIT_DATE 改為 2025-01，使 train 更貼近 OOS 分布。

**選項 B: 縮小進場門檻（k 提高到 2.5 或 3.0）**
- 確保只在更極端的偏離下進場，提高個別交易勝率。
- 代價：交易頻率降低。

**選項 C: 改用 TMF 本體 data 做 IS/OOS**
- 目前 grid 用 MXF/TXF 優化、TMF OOS 驗證，存在商品差異。
- 若 TMF 歷史夠長（已知到 2024-07），可改成 TMF 作訓練集，留更近期做 OOS。

**選項 D: 暫停 paper，策略需根本調整**
- 當前 OOS PF=0.797，MC 全 FAIL，建議**不進入 paper 交易**，先修策略再跑一輪 P5。

---

## 4. 附錄

### 4-1 OOS trades 統計
| 指標 | 值 |
|---|---|
| OOS 期間 | 2024-07-29 ~ 2026-05-27 (444 交易日) |
| n_trades | 583 |
| 平均 | 1.31 筆/日 |
| WR | 38.8% |
| PF | 0.7975 |
| Net PnL | -35,350 TWD (-17.68%) |
| MaxDD | 26.10% |
| Sharpe | -1.851 |

### 4-2 月別損益表
| 月份 | 筆數 | 淨損益 (TWD) | 勝率 |
|---|---|---|---|
| 2024-07 | 4 | +1,240 | 75.0% |
| 2024-08 | 29 | -1,990 | 37.9% |
| 2024-09 | 31 | -950 | 45.2% |
| 2024-10 | 29 | -2,230 | 31.0% |
| 2024-11 | 35 | -7,120 | 31.4% |
| 2024-12 | 41 | -3,190 | 36.6% |
| 2025-01 | 15 | -340 | 33.3% |
| 2025-02 | 23 | -5,890 | 17.4% |
| 2025-03 | 32 | -7,160 | 31.3% |
| 2025-04 | 29 | -4,740 | 34.5% |
| 2025-05 | 26 | -3,850 | 34.6% |
| 2025-06 | 22 | -320 | 45.5% |
| 2025-07 | 37 | -4,670 | 40.5% |
| 2025-08 | 30 | +330 | 46.7% |
| 2025-09 | 21 | +1,090 | 52.4% |
| 2025-10 | 18 | -2,100 | 38.9% |
| 2025-11 | 21 | +2,830 | 47.6% |
| 2025-12 | 21 | -2,780 | 33.3% |
| 2026-01 | 30 | -5,070 | 30.0% |
| 2026-02 | 9 | -230 | 33.3% |
| 2026-03 | 27 | +11,190 | 55.6% |
| 2026-04 | 25 | +4,140 | 56.0% |
| 2026-05 | 28 | -3,540 | 35.7% |

獲利月份: 6/23 (26%)，2026-03/04 的獲利未能對沖大部分月份虧損。

### 4-3 Regime split (Gate 4-3)
| Regime | n_trades | net_pnl | mean_pnl | WR |
|---|---|---|---|---|
| range (ADX < 20) | 274 | -18,080 | -66 | 39.4% |
| neutral (20-25) | 309 | -17,270 | -56 | 38.2% |
| trend (ADX > 25) | 0 | 0 | n/a | n/a |

注: adx_max=25 使策略不進場於 ADX > 25 的 bar，所以 trend bucket 必然為空。

### 4-4 Grid 結果路徑
- MXF 完整 grid (162 combos): `data/vwap_fade/grid_MXF_20260528_1259.csv`
- MXF best params: `data/vwap_fade/best_params_MXF_20260528_1259.json`
- TXF 完整 grid (162 combos): `data/vwap_fade/grid_TXF_20260528_1259.csv`
- TXF best params: `data/vwap_fade/best_params_TXF_20260528_1259.json`
