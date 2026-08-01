# 📘 量化策略優化循環 SOP

> **用途**：系統性找出任一規則型量化策略的績效天花板
> **適用**：任何有明確進場規則的策略（BB 突破、順勢框、SMC、趨勢跟蹤、均值回歸等）
> **建議環境**：RTX 4060 Ti 或更高 GPU、Python 3.12+、20+ CPU 核心
> **文件版本**：v1.0 — 2026-04-16

---

## 🎯 核心原則

1. **每個階段都有客觀停止條件** — 知道何時該停
2. **每個階段都有防過擬合機制** — 避免自欺欺人
3. **GPU 用在該用的地方** — 不為了用 GPU 而用
4. **Walk-Forward 是唯一可信的驗證** — 其他都是參考
5. **Paper Trading 是唯一最終檢驗** — 回測再漂亮都要真跑

---

## 📦 前置準備清單

### 硬體
- [ ] GPU（RTX 3060+ 或更高，8GB VRAM 以上）
- [ ] 16+ GB RAM
- [ ] 100 GB+ SSD（存歷史數據）

### 軟體套件
```bash
pip install xgboost lightgbm catboost optuna pandas numpy scikit-learn \
            torch matplotlib scipy numba pyarrow requests tqdm
```

### 專案目錄結構（每個策略建議一致）
```
project_root/
├── data/                       # 歷史 K 線（parquet）
├── strategies/
│   └── your_strategy.py        # 核心策略邏輯
├── backtest/
│   ├── engine.py              # 回測引擎
│   └── portfolio.py           # 部位/帳務管理
├── optimizer/
│   ├── ml/
│   │   ├── collect_data.py    # Stage 1 資料收集
│   │   ├── features.py        # Stage 2 特徵工廠
│   │   ├── labels.py          # Stage 3 標籤工程
│   │   ├── cv.py              # Stage 4 CV 設計
│   │   ├── models.py          # Stage 5 模型定義
│   │   ├── optuna_search.py   # Stage 6 超參搜尋
│   │   ├── robustness.py      # Stage 7 穩健性測試
│   │   └── paper_trade.py     # Stage 8 實時模擬
│   └── results/               # 模型、CSV、報告
└── docs/                       # 文件
```

---

## 🎬 策略前提檢查（Stage 0）

**開始前必做**：確認策略本身有合理邏輯，不要在無 edge 策略上浪費 GPU 時間。

### 檢查清單
- [ ] 策略規則**明確文字化**（能寫成程式）
- [ ] 有**至少 3 年歷史資料**
- [ ] 原始策略（無 ML）回測**至少不要災難性虧損**（> -30%）
- [ ] 已修正基本 bug（停損、倉位、多空對稱等）
- [ ] 至少**100 筆以上交易**才有 ML 訓練意義

### 若原始策略負報酬過大
**ML 通常無法救不合理策略**。先回到規則本身：
- 停損點是否合理？
- 訊號方向是否正確？
- 手續費是否被低估？
- 倉位是否失控？

---

## 📊 Stage 1：資料擴充（1 天，2h GPU）

### 🎯 目標
樣本數 ≥ **50,000**；涵蓋 **多市場 regime**（牛/熊/震盪）。

### 必做項目

#### 1.1 多標的下載
最少 10 個標的。加密貨幣建議：
```
必須：BTC, ETH
主流：SOL, BNB, XRP, DOGE, AVAX
次要：LINK, MATIC, DOT, ATOM, LTC, UNI, NEAR
```

其他市場（依策略適用）：
- 股指期貨：ES、NQ、YM、RTY
- 商品：黃金 GC、原油 CL、天然氣 NG
- 外匯：EUR/USD、GBP/USD、USD/JPY

#### 1.2 最少 3 年資料
若策略訊號每週 5-10 次，**3 年 × 10 標的 = 10,000+ 樣本**合格。
策略訊號稀有（每月 < 5）→ 至少 **6 年歷史**。

#### 1.3 多時間框架聚合
從 1m 原始 K 線聚合到：**2m、5m、15m、1h、4h、8h、1d**

#### 1.4 額外資料源（加分）
- 期貨資金費率（Binance Futures API）
- 未平倉量 OI
- 現貨與期貨基差
- 大戶持倉變化
- 交易所鏈上指標（如 Glassnode）

### 停止條件 / Gate
- [ ] 樣本數 ≥ 50,000（含所有標的）
- [ ] 涵蓋至少一段熊市 + 一段牛市
- [ ] 資料無缺漏（> 99% completeness）
- [ ] 已存成 parquet（快速載入）

### 代碼骨架
```python
# optimizer/ml/collect_data.py
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", ...]  # 10+
TIMEFRAMES_AGGREGATE = ["2m", "1h", "4h", "8h", "1d"]

for sym in SYMBOLS:
    fetch_binance_1m(sym, "2020-01-01", "2026-04-15")
    aggregate_to_tfs(sym, TIMEFRAMES_AGGREGATE)
```

---

## 🏭 Stage 2：特徵工廠（1 天，CPU 為主）

### 🎯 目標
從 69 個擴到 **200+ 特徵**，再用重要性選回 **50-80 個**。

### 必做項目

#### 2.1 系統性產生特徵（避免手動遺漏）
```python
# 基礎指標 × 多週期 × 多 TF
INDICATORS = {
    "rsi":        lambda c, p: rsi(c, p),
    "macd_hist":  lambda c, f, s, sig: macd_histogram(c, f, s, sig),
    "adx":        lambda h, l, c, p: adx(h, l, c, p),
    "cci":        lambda h, l, c, p: cci(h, l, c, p),
    "bb_pos":     lambda c, p, sd: bb_position(c, p, sd),
    "atr_pct":    lambda h, l, c, p: atr(h, l, c, p) / c,
    "williams_r": lambda h, l, c, p: williams_r(h, l, c, p),
    "roc":        lambda c, p: c.pct_change(p),
    "obv_slope":  lambda c, v, p: obv_slope(c, v, p),
    "stoch_k":    lambda h, l, c, p: stoch(h, l, c, p),
}
PERIODS = [7, 14, 21, 50, 100]
TFs = ["2m", "15m", "1h", "4h", "8h", "1d"]

# 自動生成 ≈ 10 指標 × 5 週期 × 6 TF = 300 特徵
```

#### 2.2 衍生特徵
- **斜率**：`sma_50_slope = (sma_50 - sma_50.shift(10)) / price`
- **差分**：`rsi_14 - rsi_28`
- **比值**：`atr_14 / atr_50`
- **滯後**：`feature.shift(1)`, `shift(5)`, `shift(10)`
- **Rolling 統計**：`rolling_std`, `rolling_skew`, `rolling_kurt`

#### 2.3 規則特徵（策略專屬）
- 框/結構的相關幾何特徵
- 進場點距離關鍵位的比例
- 前 N 根 K 線的形態（body、wick、方向）

#### 2.4 市場 Regime 特徵
- 趨勢強度（ADX、線性回歸斜率）
- 波動率狀態（相對過去 N 天的 percentile）
- 成交量狀態（相對 MA 的 z-score）
- 時段（Asia/London/NY）

#### 2.5 特徵選擇（重要！）
```python
# 用 XGBoost feature_importance 排序
# 取前 80 個，刪除 < 1% importance 的
# 避免維度災難

model = XGBClassifier()
model.fit(X_all_features, y)
top_features = pd.DataFrame({
    'feature': all_features,
    'importance': model.feature_importances_,
}).sort_values('importance', ascending=False).head(80)
```

### 停止條件 / Gate
- [ ] 初始特徵池 ≥ 200
- [ ] 選後 top 50-80 特徵
- [ ] 無 NaN 或 inf
- [ ] 已做**滯後對齊**（特徵時間 ≤ 標籤時間 - 1）

### 反模式（絕對要避免）
- ❌ 用「未來數據」當特徵（如下根 K 線的 close）
- ❌ `rolling().mean()` 窗口包含當前 bar 的未來
- ❌ 全局 z-score（用到全部資料的 mean/std，洩漏未來）

---

## 🏷️ Stage 3：標籤工程（1 天）

### 🎯 目標
從二元升級到**資訊量更大的標籤**。

### 標籤類型（依策略選）

#### 3.1 二元（最簡單）
```python
label = 1 if hit_tp else 0  # else hit SL
```

#### 3.2 R-Multiple 回歸（推薦）
```python
# 每筆交易的 R 值
r_multiple = (exit_price - entry_price) / (entry_price - stop_price)
# 勝 +15R、小勝 +3R、打平 0R、小虧 -0.5R、大虧 -1R
label = r_multiple  # float
```

#### 3.3 三重屏障法（Lopez de Prado 經典）
```python
# 每筆交易有三個出場條件
# 1. TP barrier (profit target)
# 2. SL barrier (stop loss)
# 3. Time barrier (max holding period)

def triple_barrier(price_series, tp_pct, sl_pct, max_bars):
    """返回 (barrier_hit: 'tp'|'sl'|'time', pnl_pct, bars_held)"""
```

#### 3.4 Meta-Labeling（進階）
```python
# 第一層：原始策略判斷「要不要做」
# 第二層 ML：判斷「第一層的訊號是否可信」
# 優勢：第一層規則簡單，第二層學 filter
```

### 停止條件 / Gate
- [ ] 標籤分佈不極端（正樣本 ≥ 2%）
- [ ] 若極端不平衡（< 2%），改用回歸或多類別
- [ ] 標籤已檢查**無 look-ahead bias**（用未來計算）

---

## 🔐 Stage 4：穩健 CV（0.5 天，4h GPU）

### 🎯 目標
設計**抗過擬合**的交叉驗證。

### 必做項目

#### 4.1 Purged K-Fold + Embargo
```python
# 時間序列專用 CV
# Train 和 Test 之間留 purge gap + embargo
# 避免「相鄰樣本」互相洩漏

from mlfinlab.cross_validation import PurgedKFold

cv = PurgedKFold(
    n_splits=5,
    samples_info_sets=times,
    pct_embargo=0.01,  # 1% embargo
)
```

#### 4.2 Walk-Forward（Rolling Retrain）
```python
# 每月重訓的滾動窗口
# Train: 最近 12 個月
# Test: 下 1 個月

for current_month in test_months:
    train = data[current_month - 12 months : current_month]
    test = data[current_month : current_month + 1 month]
    model = train_model(train)
    predict(test)
```

#### 4.3 多 Random Seed 測試
同一組超參，跑 5-10 個不同 seed：
- 若結果差異 < 10%：穩定
- 若差異 > 30%：高度過擬合

### 停止條件 / Gate
- [ ] 全年多數月份（> 60%）AUC > 0.55
- [ ] 最差月 AUC > 0.50（不遜於隨機）
- [ ] 多 seed 結果標準差 < 5%

---

## 🧠 Stage 5：模型集成（2 天，15h GPU）

### 🎯 目標
用**多模型 + Stacking** 降低單模型偏差。

### 推薦模型組合

#### 5.1 樹形模型（核心）
```python
models = {
    'xgb': XGBoost(max_depth=7, n_est=500, device='cuda'),
    'lgb': LightGBM(num_leaves=63, n_est=500, device='gpu'),
    'cat': CatBoost(iterations=500, task_type='GPU'),
    'rf':  RandomForest(n_est=500, n_jobs=-1),
}
```

#### 5.2 神經網路（可選，視資料量）
```python
# 樣本 > 20,000 才值得試
# LSTM / Transformer 用序列輸入
# XGBoost 用表格輸入

if n_samples > 20000:
    models['lstm'] = LSTMClassifier(seq_len=30, hidden=64)
    models['transformer'] = TransformerEncoder(d_model=64, n_heads=4)
```

#### 5.3 Stacking（Meta-Learner）
```python
# Level 1: 上述 5 個模型
# Level 2: Logistic Regression / LightGBM 融合
from sklearn.linear_model import LogisticRegression

meta = LogisticRegression(C=1.0)
level1_preds = np.column_stack([
    xgb.predict_proba(X)[:, 1],
    lgb.predict_proba(X)[:, 1],
    cat.predict_proba(X)[:, 1],
    rf.predict_proba(X)[:, 1],
])
meta.fit(level1_preds_train, y_train)
```

### 停止條件 / Gate
- [ ] Ensemble AUC > 個別最佳模型 AUC
- [ ] 多模型預測相關性 < 0.9（有足夠多樣性）
- [ ] Stacking 後的 OOS 表現優於 simple average

---

## ⚙️ Stage 6：Optuna 多目標搜尋（1 天，20h GPU）

### 🎯 目標
**同時優化多個目標**，不只 AUC。

### 多目標定義
```python
def objective(trial):
    # 超參
    params = {
        'max_depth': trial.suggest_int('max_depth', 3, 10),
        'learning_rate': trial.suggest_float('lr', 0.01, 0.1, log=True),
        ...
    }

    # 訓練
    model = train_with_params(params)

    # 回測
    result = backtest(model, data)

    # 回傳多目標
    return (
        result.oos_auc,              # 最大化
        -result.max_drawdown_pct,    # 最小化（轉負）
        result.sharpe_ratio,         # 最大化
        result.monthly_positive_ratio, # 最大化（正月比例）
    )

study = optuna.create_study(
    directions=['maximize'] * 4,
    sampler=optuna.samplers.TPESampler(seed=42),
)
study.optimize(objective, n_trials=500)
```

### Pareto Front 分析
```python
# 多目標沒有唯一最優，找 Pareto Front
pareto_solutions = study.best_trials
# 選「個人偏好」的最佳配置
# 例：WR 40%+ 且 DD < 10% 且 Sharpe > 1.5
```

### 停止條件 / Gate
- [ ] 500+ trials 完成
- [ ] 找到至少 1 個符合以下條件的 Pareto 解：
  - AUC > 0.72
  - 最大回撤 < 10%
  - Sharpe > 1.2
  - 正月比例 > 60%

---

## 🛡️ Stage 7：穩健性三重驗證（0.5 天，10h GPU）

### 🎯 目標
**濾掉假 alpha**，確認 edge 是真實的。

### 7.1 Monte Carlo 交易順序重排
```python
# 把所有交易順序隨機打亂 10,000 次
# 計算每次總報酬
# 看分佈的 5% 分位數

def monte_carlo_shuffle(trades, n=10000):
    returns = []
    for _ in range(n):
        shuffled = np.random.permutation(trades)
        total = sum(shuffled)
        returns.append(total)
    return np.percentile(returns, 5)

# ✓ 通過：5% 分位數仍 > 0
# ✗ 失敗：5% 分位數 < 0（說明結果靠連勝運氣）
```

### 7.2 Label Shuffle Test（防 Look-Ahead Bug）
```python
# 打亂 label 重訓同樣模型
y_shuffled = np.random.permutation(y_train)
model_fake = train(X_train, y_shuffled)
auc_fake = evaluate(model_fake, X_test, y_test)

# ✓ 通過：auc_fake ≈ 0.50（隨機）
# ✗ 失敗：auc_fake > 0.55（代表有資訊洩漏 bug）
```

### 7.3 Parameter Perturbation（防參數過擬合）
```python
# 最佳參數 ± 5%、±10%、±20%
# 跑回測，看績效變化

for perturb in [0.95, 0.98, 1.0, 1.02, 1.05, 1.1, 1.2]:
    params_perturb = {k: v * perturb for k, v in best_params.items()}
    result = backtest(params_perturb)
    results.append(result.annual_return)

stability = np.std(results) / np.mean(results)
# ✓ 通過：stability < 0.3（擾動下穩定）
# ✗ 失敗：stability > 0.5（一碰就崩）
```

### 7.4 Regime-Specific Test
```python
# 拆分 test 期為不同 regime
# - 趨勢市（ADX > 25 + 單向移動）
# - 震盪市（ADX < 20）
# - 牛市 / 熊市
# 每個 regime 至少小獲利 → 策略全天候

for regime in ['trend', 'range', 'bull', 'bear']:
    pnl = backtest_regime_only(strategy, regime)
    print(f"{regime}: {pnl:+.2%}")
```

### 停止條件 / Gate（所有必須通過）
- [ ] MC 5% 分位數 > 0
- [ ] Label shuffle AUC < 0.55
- [ ] Parameter perturbation 穩定度 < 0.3
- [ ] 至少 3/4 regime 正報酬

**失敗則回到 Stage 2-6 重做**，不要放到實盤。

---

## 🎬 Stage 8：Paper Trading（2 個月，持續 GPU）

### 🎯 目標
**最終誠實驗證** — 用真實市場數據跑，看結果是否符合回測。

### 架設步驟

#### 8.1 交易所 Testnet API
```python
# Binance Testnet（現貨/期貨）
BINANCE_TESTNET_API = "https://testnet.binance.vision"
# 不是真錢，但有真實市場數據
```

#### 8.2 即時資料管線
```python
async def live_loop():
    while True:
        # 每 2 分鐘
        bars = await fetch_latest_bars(symbol, "2m", n=100)
        features = extract_features(bars)
        signal = model.predict_proba(features)

        if signal > threshold:
            order = place_paper_order(...)
            log_trade(order)

        await asyncio.sleep(120)
```

#### 8.3 追蹤指標
- 實際 slippage vs 回測 slippage
- 真實成交率（有多少次 limit order 沒成交）
- 訊號延遲（預測 → 下單時差）
- 每日 PnL 曲線

#### 8.4 每週對賬
比較：
- 同期真實 PnL
- 同期回測模擬 PnL
- 差距 > 20% → 有 bug 或市場結構變化

### 停止條件 / Gate
- [ ] 至少 **2 個月**實時運行
- [ ] 實時 PnL 與回測 PnL 差距 < 20%
- [ ] 月 Sharpe > 1.0
- [ ] 無異常 outlier 交易（未預期的大虧）

**通過後**才可考慮真倉部署（建議從 $1000 小額開始）。

---

## 🔄 循環判定

### 每輪結束評估
```python
metrics = {
    'oos_auc': 0.78,
    'monthly_return': 0.015,  # 1.5%
    'max_drawdown': 0.07,     # 7%
    'sharpe': 1.5,
    'positive_months_ratio': 0.65,
}

# 與上一輪比
improvement_auc = this_round['oos_auc'] - prev_round['oos_auc']
improvement_return = this_round['monthly_return'] - prev_round['monthly_return']

# 停止條件
if improvement_return < 0.003:  # < 0.3% 月報酬改進
    print("🏁 天花板已達")
else:
    print(f"📈 改進 {improvement_return*100:+.2f}% — 繼續")
```

### 邊際改進 < 0.3% 月報酬 → 停止
- 回到 **Stage 8 Paper Trading 完整驗證**
- 進入 **實盤小額部署**

### 邊際改進 ≥ 0.3% → 回頭加強
- Stage 1：加更多標的
- Stage 2：生成更多特徵
- Stage 5：加新模型（LSTM/Transformer）

---

## 📝 常見問題 FAQ

### Q1：每階段卡住了怎麼辦？
**A**：先檢查上一階段的 Gate 是否真正通過。若通過但下一階段無改善，可能**策略本身 edge 有限**。

### Q2：跑不出月 1.5% 怎麼辦？
**A**：誠實接受。不是所有策略都能到這水平。可選：
- 組合多個 < 1% 策略 → 疊加
- 加槓桿（2-3x，謹慎）
- 換更有潛力的策略架構

### Q3：GPU 顯存爆掉怎麼辦？
**A**：
- 降低 `batch_size`（NN）
- 降低 `n_estimators`（XGBoost）
- 分 chunk 訓練
- 用 CPU tree method（慢但省 VRAM）

### Q4：Walk-Forward 訓練太慢？
**A**：
- 用 LightGBM 取代 XGBoost（快 2-3x）
- 減少 `n_trials`
- 用 incremental learning（模型只學新資料）

### Q5：Paper Trading 表現遠差於回測？
**A**：常見原因：
1. 滑點估低（真實市場 > 0.1%）
2. 資料有 look-ahead bug
3. 市場 regime 變了
4. 交易所手續費變化
→ **停下來 debug，不要上實盤**

---

## 🎯 成功判定標準

**可以實盤的策略必須同時滿足**：

- ✅ OOS AUC > 0.70
- ✅ 月報酬 > 0.5%（保守目標）
- ✅ Max DD < 15%
- ✅ Sharpe > 1.2
- ✅ 正月比例 > 60%
- ✅ MC 5% 分位 > 0
- ✅ 2 個月 Paper Trading 與回測誤差 < 20%

**任一未達標 → 繼續優化或放棄策略**。

---

## 📊 版本追蹤模板

每輪結束記錄：

```markdown
## Round N — YYYY-MM-DD

### 本輪改動
- Stage X: 加入 XX 特徵
- Stage Y: 換 YY 模型

### 結果
| 指標 | 上輪 | 本輪 | 變化 |
|------|-----|-----|-----|
| OOS AUC | 0.75 | 0.78 | +0.03 |
| 月報酬 | 0.91% | 1.12% | +0.21% |
| Max DD | 3% | 4% | +1% |
| Sharpe | 1.3 | 1.4 | +0.1 |

### 決策
繼續優化 / 達天花板 / 放棄

### 下輪計畫
- ...
```

---

## 📦 移植到其他策略

這套 SOP **適用所有規則型量化策略**，移植步驟：

1. **替換策略核心**：`strategies/your_strategy.py`
   - 實作進場訊號偵測
   - 實作停損停利規則
2. **調整特徵提取**：`optimizer/ml/features.py`
   - 保留共通指標（RSI、ATR 等）
   - 加入策略專屬特徵（框、結構、訊號強度）
3. **調整標籤邏輯**：`optimizer/ml/labels.py`
   - 依新策略的 TP/SL 架構
4. **其他 Stage 3-8 基本不動**

---

## 📚 延伸閱讀

### 必讀
- **《Advances in Financial Machine Learning》** — Marcos Lopez de Prado
  - Purged K-Fold、Meta-Labeling、Triple-Barrier、Feature Importance
- **《Machine Learning for Asset Managers》** — Marcos Lopez de Prado
  - Denoising、Clustering、CV 避免洩漏
- **《Evidence-Based Technical Analysis》** — David Aronson
  - 統計顯著性、Monte Carlo

### 免費資源
- Hudson & Thames Lab（mlfinlab 套件）
- QuantConnect Research Library
- Optuna 官方教學
- XGBoost / LightGBM 官方文件

---

## 📎 附錄：與 auto-loop 的整合（Agentic Alpha Layer C）

> 本附錄記錄這套 SOP 如何落地到 Agentic Alpha 系統（`openab/alpha-orchestrator`），
> 由 dispatch `phase3-sop-layer-c-etf` 實作。

### auto-loop 在 SOP 上的位置
既有 auto-loop 的「5-lever sweep」**只等於 SOP Stage 6 跑了 5 個 trial**，而且：
- 沒做 Stage 1-5（資料擴充、特徵工廠、標籤工程、穩健 CV、ML 模型集成）
- 沒做 Stage 7（穩健性三重驗證）
- Optuna trials = 5（應 100-300）

兩個被 reject 的策略（`wheel-spy` / `spy-put`）Claude C6 都明確說「不是校準問題、
5 個 lever 都試過了、該換結構」。**單純翻 5 個 lever 找不到 edge 是統計常態**，
不代表策略沒救——但也不代表硬調就有救。Layer C 把完整 SOP 補上，作為
「C6 verdict = Valid → 找天花板」的下一層。

### 落地對應（程式檔 ↔ SOP Stage）
| SOP Stage | 模組 | 備註 |
|---|---|---|
| Stage 1 資料 | `sop/data_fetch.py` | Alpaca **daily** bar（非 Binance 1m）+ parquet cache |
| Stage 2 特徵 | `sop/features.py` | 257 因果特徵 → XGB importance 選 top-N |
| Stage 3 標籤 | `sop/labels.py` | 三重屏障 R-multiple；`backtester.py` numba 回測 |
| Stage 4 CV | `sop/cv.py` | PurgedKFold + embargo、walk-forward |
| Stage 5 模型 | `sop/models.py` | XGB+LGBM+CatBoost stacking、GPU+CPU fallback |
| Stage 6 Optuna | `sop/optuna_search.py` | TPE + MedianPruner、per-model 200 trials |
| Stage 7 穩健 | `sop/robustness.py` | bootstrap p5 / label-shuffle / perturbation / regime |
| 串接 + 報告 | `sop/pipeline.py` | `run_sop_pipeline(slug)` → `Pipeline/sop_optimized/<slug>/report.md` |
| 觸發 | `/sop-run <slug>` | **唯一入口**，人類手動，絕不 auto-cascade（D5） |

### 關鍵適配：1m crypto → daily ETF
SOP 原文假設 Binance 1m K 線；本實作接 Alpaca daily ETF，被迫的偏差：
1. **樣本數**：50,000 樣本門檻在 daily 上不可達；6 年 × 10 ETF ≈ 數千訊號。接受 ~數千。
2. **多時間框架**：無 intraday → 用 **longer rolling windows（5/21/63/252d）** 當多 TF 代理，
   避免 calendar resample 的邊界洩漏。
3. **regime 切分**：用 **ADX（trend/range）+ 200d SMA（bear）**，不靠 VIX/額外資料源。
4. **三重屏障尺寸**：tp/sl/max_bars 是可調參數（預設 5%/3%/20bar），ETF 波動下需校準。
5. **meta-labeling**：規則型策略（如 stage-2 breakout）出進場訊號，ML 學「哪些訊號該做」。

完整偏差清單見 dispatch `phase3-sop-layer-c-etf/output.md §3`。

### 邊界
- **不自動 cascade**：C6 verdict=Valid **不會**自動觸發 Layer C；要人類 `/sop-run`
  （每跑 ~hours GPU、不可逆，避免燒卡 / verdict 寫錯就連環跑）。
- **只 Fork 1（ETF）**：選擇權 SOP 是明確 backlog。
- **Stage 8（paper trading）不在 Layer C**：由既有 C7 `/approve` paper 流程處理。
- **新 vault state 只有 `sop_optimized/`**：不污染既有 6 state。

---

**文件結束。按 Stage 順序執行，遇到 Gate 未過請回頭修復，不要跳過。**
