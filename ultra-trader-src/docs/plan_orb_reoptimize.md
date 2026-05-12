# 夜盤 ORB 策略重新優化計畫
**版本：** v1.0
**建立日期：** 2026-05-01
**預計完成：** Phase 0~3 共 3~5 天

---

## 問題確認（執行前共識）

| 問題 | 根因 | 症狀 |
|------|------|------|
| 績效虛高 | 用訓練資料做回測（in-sample） | 2024全年 WR≈100%，2025+ 明顯退化 |
| PF=2.800 不可信 | 單筆 2026-02 +21,725 撐起 35% 淨利 | 去除後 PF≈1.8 |
| 近期失效 | OOS 表現惡化 | 2026-03 PF=0.891、2026-04 PF<1 |
| ML 模型可疑 | AUC=0.621（勉強高於隨機 0.5） | 3 子模型 ensemble 但可能只是噪音 |
| vol_ratio bug | 每根 K 棒都判斷，非只在突破時 | 誤封鎖整個 session |
| 停損不對稱 | ATR 很大時 SL=2×ATR 過寬 | 3/25 單筆 -233pts 打穿整月 |

**核心原則：先修框架，再調參數。在錯誤框架下優化永遠是浪費。**

---

## 現有系統架構（必須了解才能改）

```
訓練 Pipeline：
  optimizer/ml/collect_data.py      → 收集美股 ETF 歷史資料
  optimizer/ml/features.py          → 計算 ~200 個特徵
  optimizer/ml/labels_orb.py        → 偵測 ORB 信號 + 標籤
  optimizer/ml/models.py            → BreakoutFilterModel (XGB+LGB+RF ensemble)
  optimizer/ml/cv.py                → 交叉驗證

現有模型：
  optimizer/results_orb/orb_filter_b2.pkl          → 目前部署的模型
  optimizer/results_orb/selected_features_b2.txt   → 30 個特徵
  optimizer/results_orb/best_params_b2.json         → 超參數

ORB 設定（labels_orb.py）：
  session_start:  21:30 TST
  orb_bars:       9根（45min 開盤區間）
  entry_after:    22:15
  sl_atr:         2.0
  trail_trigger_atr: 0.8
  trail_dist_atr: 1.25
  min_orb_width_atr: 3.0 ~ 5.0
  max_breakout_vol_ratio: 2.0  ← 有 bug

夜盤策略檔：
  strategy/orb.py                   → 實盤使用
  data/historical/ml/TMF_night_orb_signals.parquet → 回測用信號
```

---

## Phase 0：OOS 真實基準測試
**目標：量化「真正的」策略表現，建立後續改動的基準**
**產出：** `docs/oos_baseline_report.md`
**預估工時：** 0.5天

### 0-1 資料切割

```
訓練期（Train）：2024-01-01 ~ 2024-12-31  → 重新 fit ML 模型
測試期（Test）： 2025-01-01 ~ 2026-04-30  → 純 OOS，不動參數
```

注意：現有 `TMF_night_orb_signals.parquet` 包含全部期間的 ORB 信號（已偵測，不含 ML 過濾），可直接用於 OOS 測試。

### 0-2 執行步驟

**Step A：用 2024 年資料重新訓練 ML 模型**

```python
# 新腳本：scripts/train_oos_model.py
# 1. 載入 TMF_night_orb_signals.parquet
# 2. 過濾 entry_time < 2025-01-01 作為訓練集
# 3. 用 selected_features_b2.txt 的 30 個特徵
# 4. fit BreakoutFilterModel（與 B2 相同超參數）
# 5. 儲存至 optimizer/results_orb/orb_filter_oos_v1.pkl
```

**Step B：在 OOS 期間（2025+）跑回測**

```python
# 新腳本：scripts/backtest_oos_baseline.py
# 1. 載入 TMF_night_orb_signals.parquet
# 2. 過濾 entry_time >= 2025-01-01
# 3. 套用 orb_filter_oos_v1.pkl（2024 年訓練的模型）
# 4. threshold=0.40，vol_ratio<=2.0
# 5. 動態口數（4% 風險，上限3口）
# 6. 輸出：月別 WR、PF、MaxDD
```

### 0-3 Gate 0 判斷標準

| 指標 | 通過 | 不通過 |
|------|------|--------|
| OOS 期 PF | > 1.3 | ≤ 1.3 |
| OOS 期 MaxDD | < 8% | ≥ 8% |
| OOS 月勝率 | > 55% | ≤ 55% |

- **通過 Gate 0** → 進 Phase 2（Walk-Forward 框架）
- **不通過 Gate 0** → 先進 Phase 1 修 Bug，再重跑 Phase 0

---

## Phase 1：修已知 Bug
**目標：排除已知的程式缺陷對結果的汙染**
**修改檔案：** `strategy/orb.py`、`optimizer/ml/labels_orb.py`

### Bug 1：vol_ratio 每根 K 棒都判斷

**位置：** `strategy/orb.py` 第 392-398 行附近

```python
# 現況（錯誤）：區間建立後每根 K 棒都跑這個 check
if self.max_breakout_vol_ratio > 0:
    vol_ratio = volume / avg_vol
    if vol_ratio > self.max_breakout_vol_ratio:
        self._entered = True   # 誤殺 session！

# 正確：只在「觸發突破的那根 K 棒」才做 vol_ratio check
# 修改位置：把 vol_ratio check 移到 breakout trigger 條件的 if block 內部
# 即：只有在 price > orb_high 或 price < orb_low 的那根才判斷
```

同步修改 `labels_orb.py` 的信號偵測邏輯，使兩者一致。

### Bug 2：停損距離上限

**位置：** `optimizer/ml/labels_orb.py` ORB config `sl_atr=2.0`

```python
# 問題：ATR=116pts 時，SL=2.0×116=232pts，一筆輸光整月利潤
# 修正：加上 max_sl_pts 上限
_MXF_ORB_CFG = {
    ...
    "sl_atr": 2.0,
    "max_sl_pts": 120,    # 新增：停損最多 120pts（約 1,200 TWD/口）
    ...
}
# stop_price = max(entry - sl_atr * atr, entry - max_sl_pts)  # LONG
# stop_price = min(entry + sl_atr * atr, entry + max_sl_pts)  # SHORT
```

### Bug 修完後必做：重新生成 ORB 信號

```bash
# Bug 修完後，重新跑一次信號生成（不重新訓練 ML）
python scripts/update_night_orb_data.py
# 然後再跑一次 Phase 0 的回測確認數字有改善
```

---

## Phase 2：Walk-Forward 驗證框架
**目標：建立真正能防止 lookahead bias 的驗證機制**
**產出：** `scripts/walk_forward_orb.py`、`docs/walk_forward_report.md`
**預估工時：** 1天

### 滾動窗口設計

```
訓練窗口：6個月（固定長度，不累積）
測試窗口：1個月（純 OOS）
滾動步長：1個月

時間線（以現有資料 2024-01 ~ 2026-04 為例）：
  Fold 01  Train: 2024-01~06  Test: 2024-07  → 記錄 OOS 績效
  Fold 02  Train: 2024-02~07  Test: 2024-08
  Fold 03  Train: 2024-03~08  Test: 2024-09
  ...
  Fold 21  Train: 2025-10~2026-03  Test: 2026-04

最終：21個獨立 OOS 月份的累積損益曲線
```

### 腳本設計（scripts/walk_forward_orb.py）

```python
"""
Walk-Forward 回測
  1. 依滾動窗口切割訓練/測試集
  2. 每個 Fold：用訓練集 fit BreakoutFilterModel
  3. 在測試集（OOS）跑回測
  4. 累積所有 Fold 的 OOS 損益
  5. 輸出月別 WR/PF/MaxDD 表格
"""

TRAIN_MONTHS = 6   # 訓練窗口
TEST_MONTHS  = 1   # 測試窗口

# 關鍵：每個 Fold 的模型只用「訓練期內」的資料 fit
# 任何測試期的資料都不進入訓練
```

### 通過門檻（Gate 2）

| 指標 | 通過 |
|------|------|
| WF 累積淨利 | > 0 |
| WF 期間 MaxDD | < 10% |
| WF 月勝率（> 0 的月份佔比） | > 55% |
| 連續虧損月份 | ≤ 3個月 |

---

## Phase 3：ML vs 規則 A/B/C 對比
**目標：確認 ML 是否真的帶來 alpha，還是只是噪音**
**產出：** `docs/ml_vs_rules_report.md`
**預估工時：** 0.5天

### 三路對比（在相同 OOS 期間）

```
A. 純 ORB（無任何過濾）
   → 所有 137 筆信號全部下單

B. 規則過濾（無 ML）
   → vol_ratio: 0.5 ~ 1.8
   → orb_width_atr: 2.5 ~ 4.5（適度放寬上限）
   → entry_time: 22:00 ~ 02:00

C. 現有 B2 ML（修 bug 後）
   → vol_ratio <= 2.0 + XGB/LGB/RF ensemble, threshold=0.40

D. Walk-Forward ML（Phase 2 的成果）
   → 每月用滾動模型預測
```

### 判斷邏輯

| 結果 | 結論 | 行動 |
|------|------|------|
| D > C > B > A | ML + WF 有效 | 部署 D |
| B ≈ C ≈ D > A | 規則已夠，ML 無效 | 部署 B（更簡單穩健） |
| A ≈ B ≈ C ≈ D | ORB 本身無 edge | 暫停夜盤策略 |

### 若選擇規則過濾（B 方案）

候選規則（每個規則單獨測試，再組合）：

```python
R1 = (0.5 <= vol_ratio <= 1.8)           # 適量突破，非追高也非假突破
R2 = (22 <= entry_hour <= 2)             # 22:00~02:00 流動性較穩定
R3 = (2.5 <= orb_width_atr <= 4.5)       # 適當寬度的開盤區間
R4 = (direction == prev_day_trend)        # 順前一日收盤趨勢
R5 = (f_adx >= 20)                       # 突破時有趨勢支撐

# 評估標準：每個規則的 Information Coefficient（IC）
# IC = corr(規則分數, 實際勝敗)
# IC > 0.05 才考慮保留
```

---

## Phase 4：MAE/MFE 出場機制重設
**目標：修復「小贏大輸」的出場不對稱問題**
**預估工時：** 0.5天

### MAE/MFE 分析（scripts/analyze_mae_mfe.py）

```python
"""
從 TMF_night_5m.parquet 還原每筆交易的逐 K 棒路徑
計算：
  MFE = entry 後最大順向距離（點數）
  MAE = entry 後最大逆向距離（點數）
"""
分析問題：
  1. 贏家的 MFE 中位數是多少？（trail_stop 在幾% MFE 觸發？）
  2. 輸家在 MAE > X pts 後是否幾乎必輸？（找最佳 hard stop）
  3. 進場後第幾根 K 棒達到最大 MFE？（時間出場可行性）
```

### 根據分析結果調整出場

| 問題 | 候選修正 |
|------|---------|
| trail_stop 太緊（+8~20pts 就出場） | 加 `trail_min_activation_pts`：需獲利 > 30pts 才啟動追蹤 |
| SL 太寬（-233pts） | `max_sl_pts=120` + 動態 SL 上限 |
| 持倉太短 | 加 `min_hold_bars=6`：至少持倉 30分鐘 |
| 持倉太長（3天異常） | 保持 `max_bars=60`（5hr）不動 |

---

## Phase 5：穩健性測試（最後把關）
**僅在 Phase 3 選出方案後執行**
**預估工時：** 0.5天

### 5-1 Monte Carlo（scripts/monte_carlo_orb.py）

```python
N_SIMULATION = 500
for i in range(N_SIMULATION):
    # 隨機打亂交易順序（保持口數不變）
    shuffled = trades.sample(frac=1.0, random_state=i)
    # 計算 MaxDD
結論：500次模擬中，MaxDD 95th percentile < 12% → 通過
```

### 5-2 參數敏感度

```
vol_ratio 上限：1.6 / 1.8 / 2.0 / 2.2 / 2.4
  → 任意相鄰兩組 PF 差距 < 0.3 → 穩健

ORB 寬度：2.0~5.0 / 2.5~4.5 / 3.0~5.0
  → 任意組合 MaxDD 差距 < 3% → 穩健

ML threshold（若保留 ML）：0.35 / 0.40 / 0.45 / 0.50
  → 交易數量變化在 ±20% 以內 → 穩健
```

### 5-3 近期 OOS 績效（最重要）

```
只看 2025-07 ~ 2026-04（最近 9個月）
  PF > 1.2  → 通過
  MaxDD < 8%  → 通過
  連續虧損月 ≤ 2個月  → 通過
```

**3 項全通過 → 可進入 Paper Trading 1個月**

---

## 整體執行流程

```
Day 1
  ├─ Phase 0  OOS 基準測試（train_oos_model.py + backtest_oos_baseline.py）
  └─ Gate 0 判斷
       ├─ 通過 → 跳到 Phase 2
       └─ 不通過 → Phase 1（修 bug）→ 重跑 Phase 0

Day 2
  └─ Phase 2  Walk-Forward 框架（walk_forward_orb.py）
       └─ Gate 2 判斷 → 不通過則回 Phase 1

Day 3
  └─ Phase 3  ML vs 規則 A/B/C 對比 → 選定方案
  └─ Phase 4  MAE/MFE 分析 → 調整出場參數

Day 4~5
  └─ Phase 5  穩健性測試 → 全通過後上 Paper Trading
```

---

## 新增腳本清單

| 腳本 | 用途 |
|------|------|
| `scripts/train_oos_model.py` | 用 2024 資料重訓模型 |
| `scripts/backtest_oos_baseline.py` | OOS 基準回測（Phase 0） |
| `scripts/walk_forward_orb.py` | Walk-Forward 框架（Phase 2） |
| `scripts/compare_abc.py` | A/B/C 規則對比（Phase 3） |
| `scripts/analyze_mae_mfe.py` | MAE/MFE 分析（Phase 4） |
| `scripts/monte_carlo_orb.py` | Monte Carlo 穩健性（Phase 5） |

---

## 修改檔案清單

| 檔案 | 修改內容 |
|------|---------|
| `strategy/orb.py` | Bug 1：vol_ratio 只在突破時判斷 |
| `optimizer/ml/labels_orb.py` | Bug 2：加 max_sl_pts + Bug 1 同步修正 |

---

## 停止條件（何時放棄夜盤策略）

以下任一條件成立，停止優化，重新評估策略適用性：

1. Phase 0 OOS PF < 1.0（ORB 本身已無 edge）
2. Phase 2 Walk-Forward 累積淨利 < 0（WF 模型一致失敗）
3. Phase 3 三路對比 A/B/C 全部 PF < 1.1（規則本身無效）
4. Phase 5 近期 9個月 MaxDD > 15%（風險過高）

停止後行動：
- 暫停夜盤策略下單（改 paper trading 觀察）
- 分析近 3~6 個月市場結構是否改變（波動率、趨勢性）
- 考慮換商品（MXF → 其他夜盤商品）或換策略邏輯
