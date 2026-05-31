# LIVE 交接清單(研發 → live)/ MAIN MANIFEST / 2026-05-31

> **交付**:2026-05-31 投遞至 LIVE repo(`vps永豐微台指/TMFtrader-src`)。
> **來源**:研發產出於 `tmf-strategy-lab`(私有 R&D repo)。
> **狀態**:本文件自包含(self-contained)。live 端僅依本文件即可完成 port / 接線 / 上線紀律,無需回查研發 repo。
> **驗證口徑**:本文件所有「接線點」程式碼與行號,均以實際 LIVE `core/engine.py` 為準(Verify 階段逐路徑追蹤)。若與研發 repo 舊版交接稿衝突,**以本文件為準**。

---

## §0 摘要 / TL;DR

- 三支策略(`breakout_v7` / `day_orb` / `night_v3`)以**純新增檔**進場,未註冊前不動現行 live。
- 相依驗證:四個共用模組 LAB vs LIVE **byte-for-byte 完全一致**,可直接 drop-in;`will_import_clean=true`,**無 blocking issue、無衝突**(詳 §2)。
- 接線三步:(a) 加 3 個 import、(b) 加 3 個 `_create_strategy` 分支、(c) 改 `TickAggregator` intervals(詳 §3)。
- **PRE-FLIGHT 阻擋(非程式碼 bug,但必做)**:三個新 `strategy_type` 字串必須在 `core/instrument_config.py::INSTRUMENT_SPECS` 指派給某 instrument,否則 `_create_strategy` 永遠收不到新 type、會 fall-through 到 `AdaptiveMomentumStrategy()`。**目前 LIVE specs 只有 `"breakout"` 與 `"gold_trend"` 兩個 type**(已驗:`instrument_config.py` L38 / L51)。
- 紀律:**先 paper、先擇一(建議 day_orb)、永不在 live 調參**(詳 §5)。

---

## §1 要複製的檔(來源 → 目的)— 純新增

來源根:`C:\Users\xx\Desktop\tmf-strategy-lab-main\tmf-strategy-lab-main\`
目的根:`C:\Users\xx\Desktop\vps永豐微台指\TMFtrader-src\`

| 來源檔 | → 目的 | 對應 type | 說明 |
|---|---|---|---|
| `strategy/breakout_dualslope.py` | `strategy/breakout_dualslope.py` | `breakout_v7` | 日盤趨勢 v7;繼承 LIVE 既有 `strategy/breakout.py` |
| `strategy/day_orb.py` | `strategy/day_orb.py` | `day_orb` | 日盤開盤區間(此版採 breakout 結構,見 §3 註) |
| `strategy/night_orb.py` | `strategy/night_orb.py` | `night_v3` | 夜盤開盤區間突破;檔內含 `NightGapORBStrategy`(v4,**未採用**,可留不註冊) |

純新增檔。**未在 `_create_strategy` 註冊且未在 instrument spec 指派 type 前,完全不影響現行 live。**

---

## §2 相依一致性裁定(deps verdict)— 通過,無破壞性 caveat

LIVE 已有,無需複製。Verify 階段對四個共用模組做 LAB vs LIVE 對比:

| 模組 | 裁定 | 關鍵點 |
|---|---|---|
| `strategy/base.py` | **identical(byte-for-byte)** | `BaseStrategy` 抽象 `name`/`on_kbar`;`get_parameters`/`reset` 具體預設;`Signal` @dataclass 接受 strength/stop_loss/take_profit/reason/source,且 **`trail_dist_pts` 是真實宣告欄位**(L38),`breakout.py` L332 的 `s.trail_dist_pts=...` 可用;`SignalDirection` 有 BUY/SELL/CLOSE。 |
| `strategy/breakout.py` | **identical(byte-for-byte)** | `__init__` 接受所有必需 kwargs(expand_ratio/pullback_ema_gap/min_di_gap/trail_trigger_atr/trail_dist_atr/early_cut_bars/early_cut_loss_atr/momentum_rsi_*/min_adx/afternoon_min_adx/squeeze_grace_bars/max_loss_twd/point_value);`on_kbar(self, kbar, snapshot, **kwargs)` 簽章與子類 super() 相符;Signal `.reason` 含 `'A-Squeeze'` 子字串 → `kill_a_short` 相依滿足。 |
| `core/market_data.py` | **identical(byte-for-byte)** | `KBar` 有 datetime/open/high/low/close/volume/interval;`MarketSnapshot` 同時有 **`ema60`(L50)與 `ema200`(L51)** 且由 `IndicatorEngine.update` 填值 → `BreakoutDualSlope` 讀 `snapshot.ema200`/`snapshot.ema60` 乾淨;另含 atr/adx/rsi/rsi_ma5/plus_di/minus_di/volume_ratio/atr_ma20/bar_count。 |
| `core/position.py` | **identical(byte-for-byte)** | `Position` 有 side/entry_price/quantity/bars_since_entry;`Side` enum 有 FLAT/LONG/SHORT。 |

**裁定:`will_import_clean = true`,`collisions = []`,`blocking_issues = []`。無破壞性(breaking)caveat,無命名衝突。** 三支策略可直接 drop-in import。

> 唯一需注意(非破壞性):`BreakoutDualSlopeStrategy` 在子類把 `self._cooldown_bars` 設為 0(L61/L66),`breakout.py` 父類 L122 已先初始化 → 正常運作。

---

## §3 接線三步(paste-able,程式碼逐字來自 engine 驗證)

> 既有 `"orb"` / `"breakout"` / `"gold_trend"` 分支**完全不動**;三個新分支插在 `_create_strategy` 結尾 `return AdaptiveMomentumStrategy()`(**已驗 LIVE L136**)之前。

### (a) imports_to_add — 加到 `core/engine.py` 檔頭 import 區

```python
from strategy.breakout_dualslope import BreakoutDualSlopeStrategy
from strategy.day_orb import DayORBStrategy
from strategy.night_orb import NightORBStrategy
```

### (b) create_strategy_branches — 插入 `_create_strategy`(L136 final return 之前)

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

> **與舊交接稿差異(以本文件為準)**:研發 repo 舊稿曾把 `day_orb` 寫成 `mode="fade", or_bars=7`,並對 `night_v3` 列出一長串 ctor 參數。**Verify 以實際 engine 接線為準**:`day_orb` 採 `mode="breakout", or_bars=6, force_close=(13,25)`;`night_v3` 僅釘 `mode="breakout"` + money-stop,其餘進場結構走 ctor 預設(凍結值見 §4)。凍結的「選擇性旋鈕」數值(min_adx 等)由策略類別內部預設承載,不在此 ctor 重列。

### (c) tickagg_change — `core/engine.py` `InstrumentPipeline.__post_init__`(**已驗 L77**)

```text
BEFORE (engine.py InstrumentPipeline.__post_init__ L77):
    self.aggregator = TickAggregator(intervals=[1, 5, 15])
AFTER:
    self.aggregator = TickAggregator(intervals=[1, 5, 15, 30, 60])
```

此改動**單獨只會建出 30m/60m bars,不會驅動任何策略在 30/60 跑**。追蹤源碼後的推理:

1. `on_kbar_complete` 註冊(engine.py L246-258)只為 `self.timeframe`、5、15 註冊 callback,**無 30/60 註冊**。`market_data.py` 的 `on_kbar_complete`(L134-137)對不在 `self._callbacks` key 的 interval **靜默忽略**;`_callbacks` 以 `self.intervals`(L122)為 key,故改 intervals 後 30/60 key 會存在,但 engine 從未替它們掛 callback。
2. 即使有掛的 5m/15m callback **也不驅動策略**:`_on_kbar_5m_complete`(L774)/`_on_kbar_15m_complete`(L783)只透過 MTF indicator engine 刷新 `pipeline.snapshot_5m`/`snapshot_15m`,**從不 enqueue `("kbar",...)` 事件**。只有 `_on_kbar_complete`(L767,即 `self.timeframe` 那條 callback)會 enqueue `("kbar",(instrument,kbar))`,`_engine_loop`(L804-806)再 route 到 `_process_kbar` —— **這是唯一呼叫 `pipeline.strategy.on_kbar`(L1052)與 `check_exit`(L1035)的地方**。

   → 策略恰好在**單一 TF = `self.timeframe`** 被驅動。要讓策略在 30m/60m 跑,須 (a) 把 30/60 加進 intervals(本改動)**且** (b) 設 `TIMEFRAME=30` 或 `60` 讓 `self.timeframe` 選那個 TF。**不需**為 30/60 另註冊新 callback —— 既有 L247-250 區塊已經做了 `pipeline.aggregator.on_kbar_complete(self.timeframe, ... _on_kbar_complete ...)`。只有在你想讓策略主跑某 TF、同時拿 30/60 當「次要 MTF 快照」時才需另加 callback,而**三支策略都不需要**。
3. Warmup synth(L427)迭代 `pipeline.aggregator.intervals`,從 1m 史合成每個 `!=1` 的 interval。故 intervals 一變成 `[1,5,15,30,60]`,L427 迴圈**已自動**從 1m warmup 史合成 30m/60m completed_bars,零額外程式碼。`get_bars_dataframe(self.timeframe,...)`(L455)再以 `self.timeframe`(30 或 60)暖機主 indicator engine → `day_orb`/`night_v3` 正確暖機。

### timeframe_mapping(每支策略的驅動 TF)

| type | TIMEFRAME env | 說明 |
|---|---|---|
| `breakout_v7` | **5** | 日盤 5m(與現行 `"breakout"` 隱含跑的 TF 相同) |
| `day_orb` | **30** | 日盤 ORB on 30m |
| `night_v3` | **60** | 夜盤 ORB on 60m |

選擇機制(已驗):engine 啟動時讀一次 `self.timeframe = int(os.getenv("TIMEFRAME","1"))`(L226)。每個 instrument 以 `_create_strategy(spec.strategy_type)`(L240)建策略,並在 `self.timeframe` 註冊主 kbar callback(L247-250)。故**單一 paper/live run 的驅動 TF 純由 `TIMEFRAME` env 決定,策略類別純由 instrument spec 的 `strategy_type` 決定**。paper 跑某策略:把 `INSTRUMENTS` 設成 `spec.strategy_type` 為目標的那個 instrument,並把 `TIMEFRAME` 設 5 / 30 / 60。

### wiring_gaps(接線缺口,務必理解)

1. **5m/15m kbar callback 不驅動策略**。只有 `self.timeframe` 那條(`_on_kbar_complete`,L767)會 enqueue `("kbar",...)` → `_process_kbar` → `strategy.on_kbar`(L1052)/`check_exit`(L1035)。`_on_kbar_5m_complete`(L774)/`_on_kbar_15m_complete`(L783)只更新 snapshot。**策略恰跑在單一 TF = `self.timeframe` = `int(os.getenv('TIMEFRAME','1'))`**。
2. engine 內**無 30m/60m 的 `on_kbar_complete` 註冊**。把 30/60 加進 intervals 後,`market_data._callbacks` 會有 30/60 key 但保持空,除非你註冊。對 `day_orb`(TIMEFRAME=30)與 `night_v3`(TIMEFRAME=60)**沒問題**,因為既有 `self.timeframe` 註冊(L247-250)會把 `_on_kbar_complete` 掛到 `self.timeframe`。只要「策略所需 TF == `self.timeframe`」就**不需額外註冊**。
3. **單進程限制(關鍵)**:`TIMEFRAME` 是單一全域 env(L226),驅動所有 pipeline 的主 callback。**不能在同一進程同時跑 `breakout_v7`(5m)與 `night_v3`(60m)在不同主 TF** —— 每個 paper/live 進程只能跑一個 TIMEFRAME。請以**獨立進程**(各自 `INSTRUMENTS` + `TIMEFRAME` env)分開跑,與既有 day-breakout(5m)/ night-orb 拆法一致。
4. 若日後要某策略主跑某 TF、同時吃 30/60 當次要 MTF 快照,才須加顯式註冊 + handler(例:`pipeline.aggregator.on_kbar_complete(30, lambda kbar, inst=code: self._on_kbar_30m_complete(inst, kbar))` 並定義 `_on_kbar_30m_complete` 更新新的 `pipeline.snapshot_30m`)。三支都是單 TF,**不需要**。
5. **money-stop / point_value 僅為 backstop**:真實 P&L 用 engine 內 instrument spec 的 point_value(與研究 OOS 一致)。保持凍結 `max_loss_twd=4000`/`point_value=10`,讓策略內 backstop 不會在 spec-based 邏輯前先觸發。**並務必確認新 `strategy_type` 值已在 `instrument_config.py` INSTRUMENT_SPECS 指派給某 instrument** —— 目前只有 `"breakout"` / `"gold_trend"`,在 spec 設好之前 `_create_strategy` 永遠收不到新 type。

> ⚠️ **PRE-FLIGHT 阻擋(必讀)**:三個新 `strategy_type` 字串(`"breakout_v7"`/`"day_orb"`/`"night_v3"`)必須先在 `core/instrument_config.py::INSTRUMENT_SPECS` 接好(目前只有 `"breakout"`/`"gold_trend"`,已驗 L38/L51),否則 `_create_strategy` 直接 fall-through 到 `AdaptiveMomentumStrategy()`(L136)。`night_orb.py` 內的 `NightGapORBStrategy`(v4)**未請求**,不 import / 不註冊。

---

## §4 凍結 lockbox 參數(per strategy,凍結時點 2026-05-31)

每口風控 `max_loss_twd=4000`(微台 TMF 口徑)、`point_value=10`。凍結來源:`scripts/forward_eval.py` FROZEN。

| type | 類別 / TF | 凍結結構(釘死) | 凍結進場閾值(各 OOS 窗最佳中位) |
|---|---|---|---|
| `breakout_v7` | BreakoutDualSlope / 5m | slope_thr=0.015、雙水平對齊(EMA60/EMA200 同向)、kill-A-short;出場 trail_trigger_atr=1.2 / trail_dist_atr=1.25 / early_cut_bars=50 | min_adx=21 / afternoon_min_adx=34 / expand_ratio=1.20(僅此 3 個進場閾值取中位) |
| `day_orb` | DayORB / 30m | or_bars=7 / buf=0.30 / max_or=5.0 / sl=1.0 / max_hold=30 | min_adx=17.5 / min_or_atr=1.3(選擇性旋鈕取中位) |
| `night_v3` | NightORB(breakout) / 60m | or_bars=8 / buf_atr=0.35 / max_or_atr=6.5 / sl_atr=1.0 / max_hold=24;**全參數釘死、零 per-window 優化**(seal JSON best={}) | min_adx=25 / min_or_atr=2.4 |

> **凍結 caveat(誠實標註,口徑差異)**:`day_orb` 的凍結值是「選定中位數」,**並非** sealed 數字所用 JSON(`wfo_oos_day_orb_v2_ablation.json`)各窗實際優化值(該 JSON 每窗 or_bars 2-8 / min_adx 0-25 / max_hold 12-36 都在變)。§3(b) ctor 的 `or_bars=6` 為 engine 接線釘死值,與本表 §4 研究凍結結構表(or_bars=7)為**不同口徑**(接線 ctor vs 研究凍結),live 以 §3(b) 接線值上線、前推時對照 §4 凍結基線觀察。`night_v3` JSON best={} 已證實零調參。

---

## §5 上線紀律(live 端必守)

1. **先 paper**:`simulation=True`。前推 ≥6 個月對基線(建議 12 個月),才考慮小量實單。**升級門檻**:全期前推 ✓符合(PF≥1.0 且 DD≤基線×1.5)且合併 DD ≤10% → 才進 Phase 3 小量實單,從 TMF 微台最小資金起。
2. **先擇一**:**建議先跑 `day_orb`** —— MCPT 最顯著(保漂移 N=300:p(net)=0.017 且 p(PF)=0.003,兩者皆過,三支中最硬、最不依賴 drift),且與 live 既有策略**不重疊**(`breakout_v7` 與現行 `"breakout"` 同為日盤 5m 趨勢、會打架;`day_orb` 是 30m 均值回歸,互補)。
3. **`day_v7` + `day_orb` 同搶日盤倉位**:兩者在**同帳戶 + 同商品**競爭同一個日盤部位。**除非啟用 `position_lock`(同帳戶同商品同時只允一支持倉)做仲裁,否則同時只跑一支**。
4. **不要用 `night_v3` 取代既有 `"orb"`**:LIVE 既有 ORB 更成熟(45min / US-overlap / ML 過濾)。兩者於 **paper 並存對打**(不同模擬帳戶 / 分開記錄)做比較即可,**勿直接替換**。
5. **永不在 live 調參**:lockbox 凍結。前推只觀察 + 通過/否決;任何改善只能回研發端、用全新假設另走前推(不准再撞 2020-2026)。
6. **`max_loss_twd=4000` 為微台 TMF 口徑**(本金 125K × 0.032)。等比代理:MXF 625K → 20,000;切勿把 4000 套到大台/代理本金。

### 漂移(drift)caveat — 前推必須跨越非牛市 regime

| type | drift 風險 | 證據 |
|---|---|---|
| `breakout_v7` | **帶多頭順風車** | 保漂移 N=300:p(PF)=0.040 顯著、**p(net)=0.080 僅邊際**(漂亮淨值相當部分是 2020-26 牛市 drift capture);逐年多數年跑輸 buy&hold,只 2022 空頭年贏;套夜盤翻負(TXF 24h PF 0.91)。**前推必須跨非牛市才算數。** |
| `day_orb` | **最穩、最少 regime 依賴** | p(net)=0.017 且 p(PF)=0.003 皆過;均值回歸在震盪/空頭年特強(TXF 2022 +75%/DD3.8%);**相對最可信、可優先信任**。仍有 lockbox 選擇偏誤(全期 +289% 為樂觀值),個別輸窗 2021-03 與 2024-09~2025-03。 |
| `night_v3` | **帶多頭順風車** | p(PF)=0.007 顯著、**p(net)=0.063 僅邊際**(同 v7 主要捕捉 drift);早期弱(2020-21 微負)、近年強;零調參輸窗 2021-03 與 **2025-09(最近,須盯近期 regime 是否變)**;夜盤特有 live 摩擦(薄流動性/滑價可能>2點/SDK 斷線/04:55 強平)未含於回測。 |

---

## §6 回測基線(前推對照口徑,微台 TMF 125K)

> 註:`realism` 全期單一配置口徑(供前推對照);WFO 逐窗口徑數字另有差異,見 §7。

| type | PF(基線) | 最大 DD% | 筆/月 | OOS 筆數(realism 全期 TMF) |
|---|--:|--:|--:|--:|
| `breakout_v7` | ~1.8 | ~6(TMF 7.9) | ~1.0 | 低頻(MXF 76 / TXF 82);**極低頻為核心 caveat** |
| `day_orb` | ~2.0 | ~12(TMF 24.0) | ~3.5 | TMF 92(統計遠比 v7 扎實) |
| `night_v3` | ~1.6 | ~20(TMF 21.4) | ~4.0 | TMF 97(WR ~36% + PF 1.5,多次小虧少數大賺) |

前推判定門檻(對照上表基線):✓符合 = PF≥1.0 且 DD≤基線×1.5;⚠️偏離 = 0.9≤PF<1.0 或 DD 超基線 1.5x(觀察、不擴大部位);✗失效 = PF<0.9 持續 → 該支下架回研究。頻率偏離閘:實際筆/月 <基線0.5x 或 >2x → 查資料/邏輯。最少 6 個月、建議 12 個月。

---

## §7 已知口徑不一致(誠實揭露,非 bug,前推時須認對口徑)

- **`day_orb` 凍結參數來源**:凍結為單一配置(or_bars7/min_adx17.5/min_or1.3 等),但產生 sealed 數字的 `wfo_oos_day_orb_v2_ablation.json` best 每窗都不同 —— 凍結值是選定中位數而非 WFO 實際優化值。
- **`day_orb` PF 兩處不一致(不同口徑)**:LESSONS 引 WFO 中位 MXF 2.13 / TXF 2.19;realism 全期 MXF 2.10 / TXF 1.99;MCPT 用 2.10 / net +1,805,573。
- **`night_v3` PF 兩處不一致**:seal 中位 MXF 1.65 / TXF 1.55;realism 全期 MXF 1.58 / TXF 1.63;MCPT 用 1.58 / net +1,053,913。
- **`breakout_v7` PF 方向反轉**:WFO 中位 TXF 2.07 > MXF 1.64;realism 全期 TXF 1.73 < MXF 1.80。文件未解釋此 cross-contract 反轉。
- **DD 緩衝口徑混用**:「夜盤 20%、day-ORB 24%」—— 夜盤 20% 是 MXF caliber(MXF night DD 20.2%),day-ORB 24% 是 TMF caliber(TMF DD 24.0%,MXF 僅 11.3%)。同句混兩種本金 caliber,易誤讀。
- **TMF 本金不一致**:realism 全期用 TMF 125K(max_loss 對應 4,000),但部署建議初始 TMF 150K(最低 100K)。前推基線 DD 是 125K 算出的。
- **`breakout_v7` net 三處不同(同口徑差異)**:WFO net MXF +202,995 / TXF +1,025,414(LESSONS 引)vs realism +348,620 / +1,496,674(MCPT/Phase0 引)。

---

## §8 資金 / 連線架構(上線前)

- **實際上線標的 = TMF 微台**;MXF/TXF 為等比例長歷史代理(MXF=微台×5 本金 625K、TXF=×20 本金 2.5M、TMF realism 本金 125K)。
- 風控 `max_loss_twd` = 本金 ×0.032:TMF 125K → 4,000 TWD/邊(micro-TX caliber)。
- 部署建議初始資金:**TMF NT$150,000(最低 ~100K)為主力最務實**;MXF 600-750K(最低 500K);TXF 2.4-3.0M(最低 2M)。須留 2 倍 DD 緩衝。資金過低破功(日盤 MXF <250K 反效果;夜盤 MXF <250K 危險、<100K 破功 PF 0.81)。
- **連線**:單 session 單次 login(1 連線,永豐上限 5/身分證)+ Strategy Router + position-lock 仲裁(同帳戶同商品同時只允一支持倉)。**不可開 3 process 各自 login**(部位淨額合併會平錯單)。保證金上線前須查 TAIFEX 當期公告(會季調)。
- 成本口徑:tmf_3x 動態 1-3 口、commission 18 元/口/邊 + tax 0.00002 + slippage 1 點。
