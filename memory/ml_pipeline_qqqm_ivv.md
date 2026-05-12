---
name: ml_pipeline_final_state
description: ML pipeline Stage 1-7 最終架構：Breakout (日盤) + ORB (夜盤) 兩套模型，各自狀態與問題 (2026-04-18)
type: project
---

## Breakout ML Filter（日盤，已完成）

**訓練集:** SPXL+TECL+QQQM+IVV+TMF+TX+Crypto+新增5商品, 1,194 trades
**OOS test:** 2025-07-01+, TMF 17 trades
**結果存放:** `optimizer/results/breakout_filter.pkl`
**資料集:** `data/historical/ml/ml_dataset.parquet`（也備份為 `ml_dataset_breakout.parquet`）

### Stage 7 穩健性（全 PASS）
- 7.1 MC: PASS (total R=12.72, p5 min-cum=-1.00)
- 7.2 Label Shuffle: PASS (shuffled=0.4988, p=1.000)
- 7.3 Perturbation: 7/7 PASS (PF 7.36~14.72)
- 7.4 LOIO CV: 12/12 商品 PASS, Mean AUC=0.825

### OOS 績效（vs v6b 日盤策略）
- OOS TMF: 17 trades, WR=76.5%→76.9%, avg_R +33.4%, PF 3.88→4.91
- 決定：不立即替換，Paper Trading 3 個月
- 報告：`deployed_strategies/tmf_breakout/ML過濾器比對報告.md`

---

## ORB ML Filter（夜盤，開發中）

**訓練集:** TMF_night+SPXL+TECL+TQQQ+QQQM+IVV+XLK+SOXX+IBB+FNGS, 8,108 trades
**基準 WR:** ~47-48%（ORB 本身勝率低於 50%）
**結果存放:** `optimizer/results_orb/orb_filter.pkl`
**資料集:** `data/historical/ml/ml_orb_dataset.parquet`（Stage 3 ORB label）

### Stage 5 模型指標
- Train AUC: 0.9584, Test AUC: 0.5536, Overfit gap: 0.4048（偏高）
- WF AUC mean: 0.5227（略高於隨機）

### Stage 7 穩健性（未完全通過）
- 7.1 MC: FAIL（TMF test=0，命名問題）
- 7.2 Label Shuffle: PASS
- 7.3 Perturbation: SKIP（同上）
- 7.4 LOIO CV: PASS，**Mean AUC=0.6365，10/10 商品通過**

### B1 方案結果（2026-04-18 完成）
TMF 命名 bug 已修正，EMA200+RSI 過濾移除（只保留 ORB 寬度 3-5 ATR）
- TMF 訓練樣本：225 筆（原始 507，Method B 73，B1 225）
- TMF OOS 樣本：77 筆（Method B 時為 0）
- Optuna 閾值：0.576，TMF OOS WR=61.1%，PF=4.95
- Stage 7 Gate：2/4 FAIL（7.1 MC FAIL，7.2 PASS，7.3 Perturbation FAIL，7.4 LOIO PASS）
- LOIO Mean AUC=0.5774（9/10 通過，TMF AUC=0.527）
- 模型存為：`optimizer/results_orb/orb_filter_b1.pkl`

### 雙軌部署計畫
- US ETF ORB：`deployed_strategies/us_etf_orb_ml/`（threshold=0.45，保守）
- TMF ORB：維持純 Layer 1，ML filter 暫不部署（待累積至 ≥150 OOS）

### ORB 夜盤數據
- TMF_night_5m.parquet: `data/historical/ml/TMF_night_5m.parquet`
- 從 tmf_20260411_full_1m.csv 提取，21:30-04:00 TST，42,201 bars
- labels_orb.py: `optimizer/ml/labels_orb.py`（ORB 專用標籤邏輯）

---

## 商品數據說明
- SPXL/TECL/TQQQ/QQQM/IVV/XLK/SOXX/IBB/FNGS: MT5 symbols
- TMF（日盤）: shioaji CSV, session=08:45-13:30 TST
- TMF_night（夜盤）: shioaji CSV, session=21:30-04:00 TST（跨夜）

## 檔案結構
```
optimizer/
  ml/
    collect_data.py, features.py, labels.py, labels_orb.py
    models.py, cv.py, optuna_search.py, robustness.py
    run_pipeline.py
  results/          ← Breakout model 結果
  results_orb/      ← ORB model 結果（待修正後重新跑）
data/historical/ml/
  ml_dataset.parquet              ← Breakout（目前 active）
  ml_dataset_breakout.parquet     ← Breakout 備份
  ml_orb_dataset.parquet          ← ORB dataset
  TMF_night_5m.parquet            ← 夜盤 5分K
```
