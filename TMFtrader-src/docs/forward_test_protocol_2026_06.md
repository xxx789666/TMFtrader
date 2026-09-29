# 前推(out-of-time)驗證協議 — 三支組合 / 2026-06 起

> **交付狀態**:2026-05-31 交付至 **live repo**(TMFtrader-src),原始版本來自獨立研究 repo `tmf-strategy-lab`,本文件已改寫為 **self-contained + live-accurate**,可獨立閱讀,無需 lab repo。
> **角色**:這是 live 期貨營運者的前推驗證協議 —— go/no-go 決策閘、最少樣本門檻、paper-first 排序,以及 breakout_v7 / night_v3 的「非牛市 regime 必過」要求。
> lockbox 後唯一乾淨的驗證:三支已蓋章策略在「從未見過的未來資料」上的真實表現。
> 🔒 凍結時點 **2026-05-31**;前推期 **2026-06-01 起**(此前資料 realism 已碰、不算乾淨)。
> 凍結配置與評估工具(研究側單一真相來源):`forward_eval.py`。

---

## 0. 🚨 上線前阻斷項(PRE-FLIGHT BLOCKER — 必讀)

**三支策略目前尚未接進 live engine,直接設 env 跑會靜默 fall through 到 `AdaptiveMomentumStrategy()`。**

`engine.py` 的 `_create_strategy(spec.strategy_type)` 依 instrument spec 的 `strategy_type` 字串分支。經對真實 `engine.py` / `instrument_config.py` 核對:

- 目前 `INSTRUMENT_SPECS` 內只存在 `strategy_type` 值 **`'breakout'`** 與 **`'gold_trend'`**。
- 新的三個值 **`'breakout_v7'` / `'day_orb'` / `'night_v3'`** **尚未被任何 instrument spec 指派**。
- 因此在把 spec 的 `strategy_type` 設為這三個新字串之前,`_create_strategy` 永遠不會收到新型別,會走到結尾的 `return AdaptiveMomentumStrategy()` —— **你以為在跑 v7,實際跑的是動量策略。**

➡️ **paper 開跑前的硬性前置:**
1. 在 `strategy/__init__` 路徑加上三個 import(見 §5)。
2. 在 `engine.py._create_strategy` 結尾 `return AdaptiveMomentumStrategy()` 之前插入三個新分支(見 §5,既有 `'orb'/'breakout'/'gold_trend'` 分支不可動)。
3. 在 `instrument_config.py` 的 `INSTRUMENT_SPECS` 把要前推的 instrument 之 `strategy_type` 設為對應新字串,**否則 `_create_strategy` 收不到新型別**。
4. 把 `TickAggregator` intervals 從 `[1,5,15]` 改為 `[1,5,15,30,60]`(見 §6),否則 30m/60m bar 不會被建出來、day_orb/night_v3 暖機與驅動皆失效。

> deps 驗證結果:`strategy/base.py`、`strategy/breakout.py`、`core/market_data.py`、`core/position.py` LAB 與 LIVE **逐位元組相同**,`will_import_clean = true`,**無 collision、無 blocking issue、無 breaking verdict**。唯一阻斷項是上述「新 strategy_type 尚未接線」的接線缺口,非依賴衝突。

---

## 1. 凍結配置(參數鎖死,勿改)

| 策略 | engine strategy_type | 類別 | TF | 凍結關鍵參數 |
|---|---|---|---|---|
| breakout_v7 趨勢 | `breakout_v7` | `BreakoutDualSlopeStrategy` | **5m** | slope_lookback48 / slope_thr0.015 / kill_a_short / require_dual_slope(EMA60/EMA200 雙水平同向) / min_adx21 / afternoon_min_adx34 / expand_ratio1.20 / trail_trigger_atr1.2 / trail_dist_atr1.25 / early_cut_bars50 / squeeze_grace_bars1 / max_loss_twd4000 |
| day_orb 回歸 | `day_orb` | `DayORBStrategy(breakout)` | **30m** | or_bars6 / buf0.30 / max_or5.0 / sl1.0 / max_hold30 / min_adx17.5 / min_or1.3 / force_close(13,25) / max_loss_twd4000 / point_value10 |
| night_v3 突破 | `night_v3` | `NightORBStrategy(breakout)` | **60m** | or_bars8 / buf0.35 / max_or6.5 / sl1.0 / max_hold24 / min_adx25 / min_or2.4 / max_loss_twd4000 / point_value10(JSON best={} 證實零調參) |

標的:**TMF 真微台**(實際上線);MXF/TXF 為等比例長歷史代理,可交叉檢查。
> ⚠️ 凍結值口徑差異備註(來自 evidence discrepancy 交叉核對):day_orb 的凍結配置是各 OOS 窗 best 的**中位數選定值**,並非產生 sealed 數字之 JSON(`wfo_oos_day_orb_v2_ablation.json`)每窗實際優化值(該 JSON 每窗 or_bars 2-8、min_adx 0-25、min_or 0.3-2.0 皆變動)。breakout_v7 只剩 3 個進場閾值(min_adx / afternoon_min_adx / expand_ratio)取各窗最佳中位,出場與結構參數全釘死。前推一律用上表凍結值,**不得 per-window 再優化**。

---

## 2. 回測基線(前推要對照的期望值)

主力上線標的為 **TMF 真微台**;下表 PF/DD/筆數同時列 MXF realism(代理)與基線速查。

| 策略 | PF(基線) | DD%(基線) | 筆/月 | 備註 |
|---|--:|--:|--:|---|
| breakout_v7 | ~1.8 | ~6 | ~1.0 | 低頻趨勢、最低 DD |
| day_orb | ~2.0 | ~12 | ~3.5 | 均值回歸、最硬、最不依賴 drift |
| night_v3 | ~1.6 | ~20 | ~4.0 | 動量突破、DD 最高 |
| **合併** | — | **~5** | ~10 | 三支低相關 |

### 2.1 各支證據明細(供判讀,非全部用同一口徑 — 已標註)

**breakout_v7(`BreakoutDualSlopeStrategy` 5m,日盤限定)**
- MCPT(保漂移 N=300):p(PF)=0.040 顯著、p(net)=0.080 **邊際**(僅捕捉多頭 drift);去漂移 p(PF)≈0.003 但 discriminating 力弱。
- OOS PF 兩口徑須分:(a) WFO 逐窗中位 MXF 1.64 / TXF 2.07;(b) realism 全期單一配置 MXF 1.80 / TXF 1.73。MCPT 用 realism net +348,620 / PF 1.80(MXF)。
- ⚠️ cross-contract 反轉:WFO 中位 TXF(2.07)>MXF(1.64),但 realism TXF(1.73)<MXF(1.80) —— 文件未解釋,判讀時留意。
- OOS DD:realism MXF 6.4% / TXF 10.0% / TMF Phase0 7.9%(三支中最低)。
- OOS 筆數:WFO 各窗極小(MXF 3-13);realism 全期 MXF 76 / TXF 82(~1 筆/月)。**極低頻是核心 caveat**,2024 PF 50+ 是 7 筆噪音。
- **drift caveat**:帶多頭順風車。p(net) 僅 0.08,漂亮淨值相當部分是 2020-26 牛市 drift;逐年多數年跑輸買進持有,只 2022 空頭年贏。**edge 為日盤專屬,套夜盤翻負(TXF 24h PF 0.91)。前推必須跨越非牛市 regime 才算數。**

**day_orb(`DayORBStrategy` 30m)**
- MCPT(保漂移 N=300):p(net)=0.017 且 p(PF)=0.003 —— **兩者皆顯著,三支中最硬、最不依賴 drift**(均值回歸不靠趨勢漲)。
- OOS PF:WFO 消融中位 MXF≈2.13 / TXF≈2.19;realism 全期 MXF 2.10 / TXF 1.99 / TMF 1.82。MCPT 用 realism net +1,805,573 / PF 2.10(MXF)。
- OOS DD:realism MXF 11.3% / TXF 18.6% / **TMF 真微台 24.0%(最高,部位需按此設計)**。
- OOS 筆數:WFO 各窗充足(MXF 各窗 11-52);realism 全期 MXF 261 / TXF 256 / TMF 92(~3.0-3.5 筆/月)。統計遠比 v7 扎實。
- **drift caveat**:最穩、最少 regime 依賴、MCPT net+PF 皆過。震盪/空頭年特別強(TXF 2022 +75%/DD3.8%),與 v7 互補。仍有 lockbox 選擇偏誤,個別輸窗 2021-03 與 2024-09~2025-03(MXF/TXF 雙負)。**相對最可信、可優先信任**,但生死仍須前推。

**night_v3(`NightORBStrategy(breakout)` 60m,夜盤)**
- MCPT(保漂移 N=300):p(PF)=0.007 顯著、p(net)=0.063 **邊際**(同 v7 主要捕捉 drift)。
- OOS PF:WFO 零調參 seal 中位 MXF 1.65 / TXF 1.55;realism 全期 MXF 1.58 / TXF 1.63 / TMF 1.43。MCPT 用 realism net +1,053,913 / PF 1.58(MXF)。
- OOS DD:realism MXF 20.2% / TXF 18.1% / TMF 21.4%。滑價穩健(slip2 仍 PF 1.55)。**DD 顯著高於日盤兩支(~20% vs 6-18%)。**
- OOS 筆數:WFO 各窗 17-29;realism 全期 MXF 282 / TXF 288 / TMF 97(~3.8-3.9 筆/月,命中 ~4/月目標)。低 WR ~36% + PF 1.5(多次小虧少數大賺)。
- **drift caveat**:帶多頭順風車。p(net) 0.063 邊際。早期弱(2020-21 微負)、近年強(2023-26),有 regime 依賴。零調參輸窗 2021-03 與 **2025-09(最近,值得盯是否近期 regime 變)**。跳空條件化(v4)反而傷,純突破最強。**前推必須跨越非牛市 regime 才算數;夜盤特有 live 摩擦(薄流動性/真實滑價可能>2點/SDK斷線/04:55強平)未含於回測。**

> ⚠️ 口徑不一致彙整(judge 時務必對齊):各支 PF/net 在 LESSONS(WFO 逐窗)、realism(全期單一配置)、MCPT/Phase0(realism)三份文件引用值不同 —— 例如 day_orb WFO net +1.27M/+5.23M vs realism +1.81M/+6.58M;night_v3 seal net +928,015 vs realism +1,053,913;v7 WFO net MXF +202,995 vs realism +348,620。**比較前推實績時固定用 realism 基線(上表),勿混引 WFO 逐窗值。** TMF 本金口徑:realism 全期用 125K 跑出基線 DD,部署建議用 150K(見 §4)—— 前推基線 DD 是 125K 算出的。

---

## 3. 判定門檻(forward_thresholds — go/no-go 閘)

對照基線:PF v7~1.8 / day_orb~2.0 / night_v3~1.6;DD% ~6/~12/~20;筆/月 ~1.0/~3.5/~4.0;合併 DD~5% / 筆/月~10。

- **✓ 符合**:PF ≥ 1.0 **且** DD ≤ 基線 ×1.5。
- **⚠️ 偏離**:0.9 ≤ PF < 1.0,**或** DD 超基線 1.5x —— 觀察、**不擴大部位**。
- **✗ 失效**:PF < 0.9 **持續** → 該支下架、回研究(但**不准再對 2020-2026 撞版本**,只能設計全新假設另走前推)。
- **頻率偏離閘**:實際筆/月 < 基線 0.5x 或 > 2x → 檢查資料/邏輯(可能 regime 變或 bug)。

### 3.1 night 策略特別規則(n≥10 規則)
> 🛑 **夜盤策略(night_v3)在累積 n≥10 筆實際成交前,不得僅憑回測或前推早期樣本判生死。** live 夜盤已知有 trail bug 歷史教訓且樣本稀少;回測虧但 live 樣本不足時不准下架。夜盤判定一律等實單/前推成交數達 **n≥10** 才開始套用 §3 的 ✓/⚠️/✗ 閘。低 WR(~36%)策略的早期序列雜訊極大,小樣本判失效是誤殺。

---

## 4. 資金配置與落地(capital_alloc)

- **實際上線標的 = TMF 微台**。MXF/TXF 為等比例長歷史代理(MXF=微台×5 本金 625K;TXF=×20 本金 2.5M;TMF realism 本金 125K)。
- **風控口徑**:`max_loss_twd` = 本金 ×0.032。TMF 125K → **4,000 TWD/邊**(符合 micro-TX caliber,與 §1/§5 凍結值一致);等比 MXF 625K → 20,000;100K → 3,200(~64 點)為破功線。tmf_3x 動態 1-3 口;commission 18 元/口/邊 + tax 0.00002 + slippage 1 點。
- **部署建議初始資金**:
  - **TMF NT$150,000**(最低 ~100K)為主力最務實。
  - MXF 600-750K(最低 500K);TXF 2.4-3.0M(最低 2M)。
  - 須留 **2 倍 DD 緩衝**:夜盤 ~20%(MXF caliber)/ day_orb ~24%(TMF caliber)—— ⚠️ 兩數字本金 caliber 不同,勿混讀(夜盤 20% 是 MXF realism;day_orb 24% 是 TMF realism,其 MXF 僅 11.3%)。
  - 資金過低破功:日盤 MXF <250K 反效果;夜盤 MXF <250K 危險、<100K 破功(PF 0.81)。
- **架構**:單 session 單次 login(1 連線,永豐上限 5/身分證)+ Strategy Router + position-lock 仲裁。
  - 同帳戶同商品同時只允一支持倉。**日 v7 與日 ORB 同跑日盤須仲裁;夜 v3 時段不重疊,無衝突。**
  - **不可開 3 process 各自 login**(部位淨額合併會平錯單)。
- 保證金上線前須查 TAIFEX 當期公告(會季調)。

---

## 5. 🔧 engine 接線(paste-able,來自 engine.py 真實核對 — 勝過任何 stale 文件)

> 以下程式碼為對真實 live `engine.py` 追蹤事件路徑後驗證所得。若舊文件的接點程式碼與此衝突,**以此為準**。
> 既有 `'orb' / 'breakout' / 'gold_trend'` 分支保持不動;三個新分支插在結尾 `return AdaptiveMomentumStrategy()` 之前。`NightGapORBStrategy`(v4)雖定義於 night_orb.py 但本次不需要,不 import / 不註冊。

### 5.1 imports
```python
from strategy.breakout_dualslope import BreakoutDualSlopeStrategy
from strategy.day_orb import DayORBStrategy
from strategy.night_orb import NightORBStrategy
```

### 5.2 `_create_strategy` 新增分支
```python
if strategy_type == "breakout_v7":
    # BreakoutDualSlopeStrategy — v7 確認 edge（kill-A-short + EMA200 斜率閘 + EMA60/200 雙水平對齊）
    # 日盤限定、5m。V7_DEFAULTS 由子類內部套用（expand_ratio=1.20 / trail 1.2/1.25 /
    # early_cut=50 / min_adx=21 / afternoon_min_adx=34 / squeeze_grace_bars=1）。
    # 此處只釘死 ctor 額外參數 + money-stop backstop。
    return BreakoutDualSlopeStrategy(
        slope_lookback=48,
        slope_thr=0.015,
        kill_a_short=True,
        require_dual_slope=True,
        max_loss_twd=4000.0,   # 經 **kw 傳入 V7_DEFAULTS 後覆寫至 BreakoutTrendStrategy
    )
if strategy_type == "day_orb":
    # DayORBStrategy — 日盤開盤區間突破（or_bars=6、min/max OR 寬度過濾、每日 1 筆、13:25 強平）。
    # ctor 預設已釘死，這裡顯式重申避免未來預設漂移。
    return DayORBStrategy(
        mode="breakout",
        or_bars=6,
        max_loss_twd=4000.0,
        point_value=10.0,
        force_close=(13, 25),
    )
if strategy_type == "night_v3":
    # NightORBStrategy — 夜盤開盤區間突破（不可動到既有 "orb" 分支）。
    # ctor 預設已釘死；max_loss_twd=4000 / point_value=10。
    return NightORBStrategy(
        mode="breakout",
        max_loss_twd=4000.0,
        point_value=10.0,
    )
```

### 5.3 money-stop 備註
三支的 `max_loss_twd=4000 / point_value=10` 為 **backstop only**;真實 P&L 用 engine 內 instrument spec 的 point_value(與研究 OOS 對齊)。保留上述凍結值,使 in-strategy backstop 不會在 spec-based 邏輯前先 fire。**但務必確認新 strategy_type 字串已被 instrument spec 指派(見 §0),否則永遠不會走到這些分支。**

---

## 6. ⏱ TimeFrame 驅動機制(關鍵 — 單 process 單 TF)

> 經追蹤完整事件路徑(`aggregator.on_tick → _update_bar → _callbacks[interval] → 僅 self.timeframe callback 入隊 ('kbar',...) → _engine_loop → _process_kbar → strategy.on_kbar / check_exit`):

### 6.1 TickAggregator intervals 變更(paste-able)
```python
# BEFORE (engine.py InstrumentPipeline.__post_init__ L77):
self.aggregator = TickAggregator(intervals=[1, 5, 15])
# AFTER:
self.aggregator = TickAggregator(intervals=[1, 5, 15, 30, 60])
```

### 6.2 為什麼這個改動「必要但不充分」
1. **此改動只建出 30m/60m bar,不會驅動任何策略在 30/60 跑。** on_tick 的 `for interval in self.intervals` 迴圈會開始建 30m/60m bar;warmup synth 迴圈(`for interval in pipeline.aggregator.intervals`)會自動從 1m 史料合成 30m/60m completed_bars,**零額外程式碼**。
2. **5m/15m callback 不驅動策略** —— `_on_kbar_5m_complete` / `_on_kbar_15m_complete` 只刷新 `snapshot_5m / snapshot_15m`(MTF 快照),從不入隊 `('kbar',...)`。**只有 self.timeframe 的 callback(`_on_kbar_complete`)會入隊事件 → 進 `_process_kbar` → 呼叫 `strategy.on_kbar` / `check_exit`。**
3. ➡️ **策略只在唯一一個 TF = `self.timeframe` 被驅動。** 要在 30/60 驅動,必須 (a) 把 30/60 加進 intervals(本改動)**且** (b) 設 `TIMEFRAME=30` 或 `60` 讓 `self.timeframe` 選到該 TF。**不需**新註冊 30/60 callback —— 既有的 self.timeframe 註冊區塊已把 `_on_kbar_complete` 掛到 self.timeframe。
4. 加獨立 30/60 callback 只在「策略跑在某 primary TF、同時要 30/60 當 secondary MTF 快照」時才需要 —— **本三支策略皆 single-TF,不需要。**

### 6.3 TimeFrame 映射(每支獨立 process)
| 策略 | 需要的 TIMEFRAME env | 說明 |
|---|---|---|
| breakout_v7 | `TIMEFRAME=5` | 日盤 5m(與現行 'breakout' 分支隱含同 TF) |
| day_orb | `TIMEFRAME=30` | 日盤 ORB on 30m |
| night_v3 | `TIMEFRAME=60` | 夜盤 ORB on 60m |

選擇機制:engine 啟動時讀一次 `self.timeframe = int(os.getenv('TIMEFRAME','1'))`;每個 instrument 依 `spec.strategy_type` 建策略並把 primary kbar callback 註冊在 self.timeframe。
➡️ **要 paper-run 一支策略:把 `INSTRUMENTS` 設為 spec.strategy_type 為目標字串的 instrument,並把 `TIMEFRAME` 設為對應 5/30/60。**

> 🚨 **單 process 單 TF 限制**:TIMEFRAME 是單一 global env,驅動所有 pipeline 的 primary callback。**不能在同一 process 同時跑 breakout_v7(5m)與 night_v3(60m)。** 每個 paper/live process 跑一個 TIMEFRAME,以分開的 `INSTRUMENTS + TIMEFRAME` env 各自起 process(與既有 day-breakout(5m)/ night-orb(60m) 拆分一致)。

---

## 7. paper-first 流程(資料累積後跑)

1. 完成 §0 接線(import + `_create_strategy` 分支 + `INSTRUMENT_SPECS` strategy_type 指派 + TickAggregator intervals)。
2. `fetch_history_kbars.py` 抓 2026-06+(需 SHIOAJI 金鑰)。
3. 資料準備:日盤多 TF 重建(5m/30m)+ 夜盤(60m)。
4. **Phase 1 — paper-first**:三支各自獨立 process(各自 INSTRUMENTS + TIMEFRAME),先跑 paper,TG 全事件覆蓋驗證接線正確(建倉/出場/熔斷皆推 TG)。確認驅動 TF 正確(log 應顯示策略在 5/30/60 觸發,非 1m 或 momentum fallback)。
5. **每月覆盤一次**,以 realism 基線(§2)對照實際 PF/DD/筆數,跑門檻判定(§3)。累積 ≥6 個月再下結論(低頻策略需時間)。夜盤套 n≥10 規則(§3.1)。

---

## 8. 期間長度與升級門檻

- **最少 6 個月、建議 12 個月**前推(三支合併 ~10 筆/月 → 6 月 ~60 筆,勉強夠看)。低頻策略需時間。
- **升級門檻**:全期前推 **✓ 符合 且 合併 DD 未爆(≤10%)** → 才考慮 **Phase 3 小量實單**,從 TMF 微台最小資金起。
- **任一支 ✗ 失效** → 該支不上線;其餘符合者單獨評估。

---

## 9. lockbox 紀律(重申)

前推**不是再優化的藉口**。凍結配置就是凍結;前推只「觀察 + 通過/否決」,**絕不調參**。要改善只能設計全新假設、走全新前推,**永不回頭 fit 2020-2026**。

---

## 附錄:依賴驗證結論(deps cross-check)

- `strategy/base.py`、`strategy/breakout.py`、`core/market_data.py`、`core/position.py` —— LAB 與 LIVE **逐位元組相同**。
- `BaseStrategy` 抽象方法齊全;`Signal` dataclass 含 `trail_dist_pts` 真實宣告欄位(breakout.py 的 `s.trail_dist_pts=...` 可用)。
- `MarketSnapshot` 在 LIVE 同時有 `ema60` 與 `ema200` 且由 IndicatorEngine 填值 → `BreakoutDualSlope.on_kbar` 讀取乾淨。
- `breakout.py.__init__` 接受全部所需 kwargs(expand_ratio / trail_trigger_atr / trail_dist_atr / early_cut_bars / min_adx / afternoon_min_adx / squeeze_grace_bars / max_loss_twd / point_value ...);Signal `.reason` 含 'A-Squeeze' 子字串 → kill_a_short 依賴滿足。
- **`will_import_clean = true`,無 collision、無 blocking issue、無 breaking verdict。**
- ⚠️ **唯一阻斷項見 §0**:三個新 strategy_type 尚未在 `instrument_config.py INSTRUMENT_SPECS` 接線,未接前 `_create_strategy` 收不到新型別、會 fall through 到 `AdaptiveMomentumStrategy()`。

---

## 9. 🆕 aft_orb（傍晚 ORB）— 第 4 前推候選（2026-06-05 登記）

> **登記性質**：**僅 paper 前推、不上 live**。現有 3 支正在 TMF live + 即將評估 MXF 放大；aft_orb 須先在 paper 證明 edge（≥6 月、夜盤類套 §3.1 n≥10）才談 live。連線：3 live + 1 aft_orb paper = 4/5（上限內）。

- **是什麼**：夜盤「傍晚段」開盤區間突破（OR 從 15:00 起、進場窗 15:00–23:30、**force_close 23:30**、30m）。與 night_v3（晚段 23:00–05:00）互補。
- **實作**：沿用 `DayORBStrategy`（套傍晚窗），**自動帶 live repo 的 session gate(15:00–23:30) + snapshot.timestamp 盤末強平**（不重蹈 day_orb/night_v3 的 frozen bug）。engine `strategy_type=="aft_orb"` 分支。
- **凍結配置（v2 ablation seal，勿在 live/paper 調參）**：`mode=breakout / TF=30 / or_bars=7 / buf_atr=0.40 / max_or_atr=6.5 / sl_atr=1.5 / min_adx=17.5 / min_or_atr=1.4 / force_close=(23,30) / session=15:00–23:30`。
- **回測基線（realism、slip=1，對照前推用）**：MXF PF **1.40** / DD **13.6%** / ~**10 筆/月**（TXF PF 1.35 / DD 14.5%）。**TMF 無 aft_30m 資料 → 基線為 MXF/TXF 代理**。
- **證據亮點**：7/7 年全正（含 2020）；**2022 空頭年 MXF +66.4%**（動量突破空頭仍賺，回應 drift 疑慮）；滑價穩健（slip0→2 PF 1.43→1.37）。**最弱 2023 PF 1.14 仍正。**
- **drift caveat**：MCPT raw 報酬含 drift（TXF 保漂移不顯著）→ 重點看前推**空頭/震盪年是否守住**，非絕對報酬。傍晚流動性較日盤薄、真實滑價/23:30 強平成交品質未模。
- **前推起算日**：**2026-06-08（下週一）**起（本週五傍晚已過、週末休盤；paper 須 15:00 前啟動才乾淨建 OR，cron 14:52）。
- **判定**：套 §3 門檻；因屬夜盤時段、**套 §3.1 n≥10 規則**（成交數 < 10 不憑早期樣本判生死）。
- **launcher**：`scripts/start_aft_orb_paper.sh`（TF=30、owner=aft_orb、隔離 paper namespace）。
