# Live 雙策略回測調查報告 — 2026-05-29

> 目的:用 live 實際參數回測小台(MXF)/微台(TMF)歷史,確認日盤 BreakoutTrend + 夜盤 ORB 的真實績效。
> 配置對齊 live:`RISK_PROFILE=tmf_3x`(動態 1~3 口,上限 3)、本金 95K–125K、動態稅成本模型。
> 商品代理:TMF 微台 2024-07 才上市(本機資料 2024-07-29~2026-05-27);長歷史用 MXF 小台代理(2020-03+)。

---

## 1. 日盤 BreakoutTrend v6b(engine.py 內建參數)

成本以微台 TMF 計價。tmf_3x、動態口數(停損寬→1口、窄→3口)。

成本以微台 TMF 計價。tmf_3x、動態口數。**已加 intrabar 硬停損/止盈**(`intrabar_hard_exits=True`,複製 live tick 層,見 §4 caveat）。

| 區間 | WR | PF | Ret | MaxDD |
|---|---|---|---|---|
| **TMF 真實合約 2024-07+** | 51.0% | **1.567** | +32.2% | 13.8% |
| MXF 長歷史 2020-2026(125K) | 43.8% | 0.976 | −3.1% | 30.8% |

TMF 逐年(修正後):**2026 +25.7%(PF 4.39)撐全場**;2023/2025 虧損年照舊。
**結論:真實合約整體賺,但幾乎全來自 2026;edge 吃 regime,2025 等震盪年會虧。不是全天候。**

> **2026-05-30 引擎差異修正**:原 FastBacktestEngine 只在收盤跑 check_exit、缺 live `core/engine.py` 的 tick 層硬停損(進場 pos.stop_loss，本例 367pt)/硬止盈。已加 `intrabar_hard_exits` flag 用 bar high/low 補上。實測**偏誤方向與直覺相反**:硬停損是保護性的 → 修正後 PF↑、MaxDD↓(TMF_oos 1.426→1.567、DD 19.4%→13.8%、WR 58→51%)。即之前數字風險調整上偏保守、非偏樂觀。逐年 regime 結論不變。

> 註:同策略 1 口 / 20 萬本金時 MXF 全期 PF 1.066(微正)。換 tmf_3x 後因 `max_loss_twd=4000` 固定金額止損,3 口時等效點數停損縮緊(4000/(3×10)=133 點 vs 1 口 400 點),動態口數下大多非綁定,但寬停損 3 口時會提前咬。

---

## 2. 夜盤 ORB — 三次才測對(重要教訓)

### 2.1 兩個 ORB 實作分岔(關鍵!)
| 項目 | `strategy/orb.py`(回測版,**非 live**) | `scripts/night_orb.py`(**live 夜盤引擎**) |
|---|---|---|
| 量能過濾 `max_breakout_vol_ratio` | 有,=2.0 | **無** |
| 選股過濾 | ML 關 | **B2 ML 模型**(orb_filter_b2.pkl, threshold 0.40) |
| `trail_dist_atr` | 0.3 | **1.25** |
| 進場 | 多筆 | 每夜 1 筆、22:15 後 |

live 進入點 = `start_night_orb.bat` → `python scripts\night_orb.py --threshold 0.40`(無 --no-ml)。
**先前用 strategy/orb.py 跑出的所有 ORB 數字(−44k「量能過擬合」結論)全部作廢 —— 測錯了實作。**

### 2.2 忠實回測法
直接驅動 `NightORBEngine.on_bar()` + 真實 ML 模型,mock 掉 tg_night/position_lock,讀 PaperLogger CSV。
腳本:`scripts/backtest_night_orb_LIVE_engine.py`(支援 symbol 參數)。

### 2.3 結果(NightORBEngine + 真實 ML / tmf_3x / 95K)

**微台 TMF(2024-08~2026-05):**
| trail_dist | WR | PF | 全期淨損益 |
|---|---|---|---|
| 1.25(live 現況) | 42.6% | 0.654 | **−44,538** |
| 0.3(engine.py 修正值) | 65.8% | 0.882 | −13,908 |

**小台 MXF(2020-2026):**
| trail_dist | WR | PF | 全期淨損益 |
|---|---|---|---|
| 1.25(live 現況) | 36.2% | 0.589 | **−119,953** |
| 0.3(修正後) | 61.3% | 0.702 | −78,105 |

---

## 3. 根因(三角驗證)

### 🚩 3.1 trail_dist bug(確認、可立即修)
`night_orb.py` 的 `TRAIL_DIST_ATR=1.25 > TRAIL_TRIGGER_ATR=0.8`:trailing 在 +0.8 ATR 啟動,卻把停損設在 `price − 1.25 ATR = entry − 0.45 ATR`(虧損位),要漲到 +1.25 ATR 才鎖到獲利。+0.8~+1.25 間回落 = 虧損出場。**主要出場原因就是 trail_stop(TMF 122/190、MXF 375/625)。**
姊妹版 `engine.py` ORBStrategy 早已修成 0.3(註解明寫「原值>trigger、必定虧損出場」),**但 live 的 night_orb.py 從沒套用。** 修正影響:TMF WR 42.6%→65.8%、虧損 −44.5k→−13.9k;MXF WR 36%→61%、−120k→−78k。

### 🚩 3.2 即使修好 trail,ORB 仍每年虧
TMF 修正後 −13,908(只 2025 +2,792)、MXF 修正後 −78,105(7 年全負)。**夜盤 ORB 在微台/小台都沒有穩健 edge。**

### 🚩 3.3 ML 過濾器疑似有害
同 MXF 同年:`strategy/orb.py`(**ML 關**)2024 +10,764 / 2025 +10,182(賺);NightORBEngine(**ML 開**)2024 −2,978 / 2025 −1,219(虧)。**ML 關賺、ML 開虧** → B2 ML 過濾(或 night_orb.py 特徵計算)反而選到更差交易。與本 session 主題一致:突破策略上加的「聰明過濾層」(量能/ML)反覆是過擬合、扣分。

### 3.4 排除項
- 資料品質:TMF/MXF 2025 夜盤價格序列幾乎一樣(均價 23,908、range 18 vs 19、零量 bar 都 0)→ 排除。
- pipeline bug:完全相同流程下差異依舊 → 排除。

---

## 4. caveat
- **日盤引擎差異(2026-05-30 已部分修)**:回測用 `FastBacktestEngine`、live 是 `core/engine.py`。兩者**策略碼一致**(同 BreakoutTrendStrategy + v6b 參數、同 check_exit/trail),但 live 多了 tick 層硬停損(pos.stop_loss)/硬止盈,回測原本只在收盤跑 check_exit。已加 `intrabar_hard_exits=True`(bar high/low 觸價出場)補上。**殘留偏樂觀**:硬停損/止盈假設精確成交該價位(無滑價;live market order 觸價送單可能有滑價);同 bar 同觸停損與止盈時停損優先(保守)。粒度仍是 5m bar(非逐 tick),但 high/low 已涵蓋盤中觸價。
- 本機 `orb_filter_b2.pkl`(`optimizer.ml.models.BreakoutFilterModel`,predict_proba **1-D**)與 night_orb.py 程式(預期 **2-D** `[0,1]`)**版本不匹配**;已 patch 成語意一致(prob≥0.4 放行)。VPS 的 model/code 配對若不同,ML 相關數字會有出入。**但 trail bug 與「每年虧」是純邏輯/價格結果,不受 ML 版本影響。**
- model pickle 需 `optimizer` 套件(只在冷備份 `永豐-自動化交易/ultra-trader-src/optimizer/`),回測時加其路徑到 sys.path 尾端。
- MXF 對夜盤「量能/ML 過濾型」策略是**不可靠代理**(量能輪廓 TMF 較薄)。

---

## 5. 建議行動
1. **立即把 VPS `night_orb.py` 的 `TRAIL_DIST_ATR` 改 0.3**(低風險、止血;此樣本期省約 3 萬)。
2. 別期待靠 trail 修正轉正 —— ORB 本質 edge 弱,2026 仍虧。
3. **驗證 B2 ML 是否該整個停用**(--no-ml):ML 關的版本反而賺,值得正式回測確認後決定。
4. 日盤 BreakoutTrend 維持,但認知它吃 regime(2025 類震盪年會虧)。

## 相關腳本
- `scripts/backtest_live_strategies.py` — 日盤 BreakoutTrend(MXF + TMF_oos 對照)
- `scripts/backtest_tmf_yearly.py` — TMF 逐年(兩支)
- `scripts/backtest_night_orb_LIVE_engine.py` — 忠實 live ORB 引擎(吃 symbol 參數、對比 trail 1.25 vs 0.3)
- 全時段 5m 快取:`data/vwap_fade/{MXF,TMF}_full_5m.parquet`

*產生:2026-05-29,本 session*
