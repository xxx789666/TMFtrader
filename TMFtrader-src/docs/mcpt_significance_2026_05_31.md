# MCPT 顯著性檢定 + LIVE 接線手冊 — 三支 edge 是否 > 噪音 / 2026-05-31

> **交付說明**:本文於 2026-05-31 交付至 LIVE repo(`vps永豐微台指/TMFtrader-src`),
> 內容源自策略研究 repo `tmf-strategy-lab`。已改寫為 **self-contained**(可獨立閱讀,不需回研究 repo),
> 並對齊 **LIVE 引擎事實**(`core/engine.py` / `core/market_data.py` / `strategy/*` 經逐行 trace 驗證)。
>
> 文件角色:MCPT 顯著性報告 + 把 v7 / day_orb / night_v3 三支接上 LIVE 引擎所需的精確接線。
> **核心警語**:漂移 / regime caveat(見 [⚠️ 漂移警語](#-漂移--regime-警語必讀) 與每支策略段)— 漂亮淨值相當部分搭 2020-2026 牛市順風車,前推必須跨越非牛市 regime 才算數。

---

## ⚠️ 漂移 / regime 警語(必讀)

**這是本報告最重要的一段。** 三支策略的 MCPT 結果分兩種訊號:

- **PF 顯著(p 0.003–0.04,三支全過)** = 進出場時機產生「比同漂移隨機更好的盈虧結構」,**有真擇時結構**,MCPT 沒推翻 edge。
- **net 顯著性(p)** 才是判斷「淨值是不是靠 drift」的關鍵:
  - `day_orb` 回歸:**p(net)=0.017 過關** → 最不依賴 drift(均值回歸不靠趨勢漲),三支中最硬。
  - `breakout_v7` 趨勢:**p(net)=0.080 邊際** → 漂亮淨值相當部分是多頭 drift capture。
  - `night_v3` 突破:**p(net)=0.063 邊際** → 同 v7,主要捕捉 drift。

**誠實結論(不可宣稱純擇時 alpha)**:
1. `breakout_v7` / `night_v3` 的漂亮淨值**一部分搭多頭順風車**;PF 才是其技巧證據,net 不是。逐年 vs 買進持有多數年跑輸,只空頭年(2022)贏。
2. **前推必須跨越非牛市 regime** 才能真正確認 v7 / night_v3(牛市它們本來就會好看)。`day_orb` 相對最可信、可優先信任。
3. **MCPT 與前推互補**:MCPT 答「過去 edge 是否 > 噪音」(過了 PF 關)、前推答「未來在不在 + 非牛市撐不撐得住」。
4. 去漂移版三支 p≈0.003 看似全顯著,但去漂移代理零 drift、真實含 drift 必勝 → 主要反映 drift capture、discriminating 力弱,**不可拿去漂移 p 當 alpha 證據**。

---

## 1. MCPT 方法

> Monte Carlo Permutation Test(固定配置版):洗牌 K 棒形狀比率序列、重建價格(保留報酬分布與單根 OHLC 幾何、摧毀時間結構),N=300 條代理跑同一固定策略,看真實落在代理分布百分位 → p 值。

- 兩版:**保漂移**(代理含多頭 drift → 測「擇時 > drift」)、**去漂移**(代理零淨漂移 → 測「有無擇時結構」)。
- 代理保留每根 OHLC 幾何 + 報酬分布,只洗順序 → 摧毀自相關/趨勢;datetime/session 不變(時段結構保留)。
- 固定配置版(不重優化)→ 未涵蓋「選參自由度」;完整版應在每條代理上重跑 WFO(更重)。本版已足以回答「這組配置 vs 噪音」。
- N=300 → p 解析度 ~0.003(最小)。

### 保漂移 N=300(關鍵:擇時技巧是否超越多頭 drift)
| 策略 | 真實 net / PF | 代理 net 中位 / 95th | p(net) | p(PF) |
|---|---|---|--:|--:|
| day_v7 趨勢 (`breakout_v7`) | +348,620 / 1.80 | +59,404 / +439,001 | 0.080 | **0.040** |
| day_ORB 回歸 (`day_orb`) | +1,805,573 / 2.10 | +325,214 / +1,533,432 | **0.017** | **0.003** |
| night_v3 突破 (`night_v3`) | +1,053,913 / 1.58 | +349,628 / +1,093,791 | 0.063 | **0.007** |

(net/PF 為 MXF realism 全期口徑 —— MXF = 微台×5 等比例長歷史代理。口徑說明見 §2 與 §5 折算表。)

### 去漂移 N=300(測有無擇時結構)
三支 net 與 PF 皆 **p≈0.003(全顯著)**——但去漂移代理零 drift,真實含 drift 必勝,故主要反映 drift capture,discriminating 力弱(見上方警語第 4 點)。

---

## 2. 各策略 evidence(MCPT p / OOS PF / DD / trades / 凍結參數 / 漂移 caveat)

> ⚠️ **口徑警告(全段適用)**:同一策略在不同文件出現不同 PF/net,是**口徑不同非錯誤**——
> (a) **WFO 逐窗**(每窗重優化,報逐窗中位 PF,不報 DD);(b) **realism 全期單一配置**(凍結配置跑全期,報 net/PF/DD)。
> **MCPT 表用的一律是 realism 全期口徑。** 引用數字時務必標註是哪一種。

### 2.1 breakout_v7 — 日盤趨勢(BreakoutDualSlope,5m)
- **MCPT p**:保漂移 N=300:**p(PF)=0.040 顯著**、**p(net)=0.080 邊際**(僅捕捉多頭 drift)。去漂移版 p(PF)≈0.003 但 discriminating 力弱(代理零 drift、真實含 drift 必勝)。
- **OOS PF**(兩套口徑須區分):
  - (a) WFO 逐窗(`wfo_oos_v7_exit_ablation.json`):真實窗 PF 中位 **MXF 1.64 / TXF 2.07**。
  - (b) realism 全期單一配置:**MXF 1.80 / TXF 1.73**。MCPT 用 realism net **+348,620** / PF **1.80**(MXF)。
  - ⚠️ **cross-contract 反轉(未解釋)**:TXF 在 realism(1.73)反而**低於** WFO 中位(2.07),MXF 反而升(1.64→1.80)。
- **OOS DD**(realism 全期最大 DD,代理口徑):**MXF 6.4% / TXF 10.0%**;TMF 真微台 Phase0 **7.9%**。為三支中**最低 DD**。WFO JSON 不報 DD。
- **OOS trades**:WFO 各窗 n 極小(MXF 3,1,5,5,10,4,7,3,6,13;TXF 4,3,4,7,10,4,7,4,7,11);realism 全期 **MXF 76 / TXF 82**(~12 筆/年 ~1 筆/月)。**極低頻是核心 caveat**,2024 PF 50+ 是 7 筆噪音。
- **凍結參數**(forward_eval.py 凍結,時點 2026-05-31):`slope_thr=0.015` 釘死 + 雙水平對齊(EMA60/EMA200 同向) + kill-A-short;出場釘死 `trail_trigger_atr=1.2` / `trail_dist_atr=1.25` / `early_cut_bars=50`;只剩 3 進場閾值用各 OOS 窗最佳中位 `min_adx=21` / `afternoon_min_adx=34` / `expand_ratio=1.20`。
- **漂移 caveat**:帶多頭順風車。p(net)=0.08 邊際,漂亮淨值相當部分是 2020-26 牛市 drift capture,純擇時 alpha 真實但較小(顯現在 PF 多於 net)。逐年 vs 大盤多數年跑輸買進持有,只 2022 空頭年贏。唯一輸窗/輸年 2021-03 / 2020(早期)。**edge 為日盤專屬,套夜盤翻負(TXF 24h PF 0.91)。前推必須跨越非牛市 regime 才算數。**

### 2.2 day_orb — 日盤開盤區間回歸(DayORB,30m)
- **MCPT p**:保漂移 N=300:**p(net)=0.017 且 p(PF)=0.003 — 兩者皆顯著**,三支中最硬、最不依賴 drift(均值回歸不靠趨勢漲)。
- **OOS PF**(兩套口徑):
  - WFO 消融(`wfo_oos_day_orb_v2_ablation.json`):中位 PF 約 **MXF 2.13 / TXF 2.19**(LESSONS 引述)。
  - realism 全期單一配置:**MXF 2.10 / TXF 1.99 / TMF 1.82**。MCPT 用 realism net **+1,805,573** / PF **2.10**(MXF)。
- **OOS DD**(realism 全期最大 DD):**MXF 11.3% / TXF 18.6% / TMF 真微台 24.0%**。介於 v7(6%)與夜盤(20%)之間;**TMF caliber 24% 最高**。WFO JSON 不報 DD。
- **OOS trades**:WFO v2_ablation 各窗 n 充足(MXF 合計約 269,各窗 11-52);realism 全期 **MXF 261 / TXF 256 / TMF 92**(~3.0-3.5 筆/月)。**統計遠比 v7 扎實。**
- **凍結參數**(forward_eval.py 凍結,lockbox 2026-05-31):DayORB(fade)30m:結構釘死 `or_bars=7` / `buf=0.30` / `max_or=5.0` / `sl=1.0` / `max_hold=30`;選擇性旋鈕中位 `min_adx=17.5` / `min_or_atr=1.3`。
- **漂移 caveat**:最穩、最少 regime 依賴、MCPT 最顯著(net+PF 皆過)的一支 —— 均值回歸在震盪/空頭年特別強(TXF 2022 +75%/DD3.8%),與 v7 互補。但仍有 lockbox 選擇偏誤(全期 +289% 為樂觀值);個別輸窗 2021-03 與 2024-09~2025-03(MXF/TXF 雙負)。真實生死仍須前推,**唯相對最可信、可優先信任**。

### 2.3 night_v3 — 夜盤開盤區間突破(NightORB,60m)
- **MCPT p**:保漂移 N=300:**p(PF)=0.007 顯著**、**p(net)=0.063 邊際**(同 v7 主要捕捉 drift)。去漂移 p≈0.003 但 discriminating 力弱。
- **OOS PF**(兩套口徑):
  - WFO 零調參 seal(`wfo_oos_night_v3_seal.json`,`best={}`):中位 PF **MXF 1.65 / TXF 1.55**。
  - realism 全期:**MXF 1.58 / TXF 1.63 / TMF 真微台 1.43**。MCPT 用 realism net **+1,053,913** / PF **1.58**(MXF)。
- **OOS DD**(realism 全期最大 DD):**MXF 20.2% / TXF 18.1% / TMF 21.4%**。滑價穩健(slip2 仍 PF 1.55)。**DD 顯著高於日盤兩支(~20% vs 6-18%),部位需按高波動設計。** WFO JSON 不報 DD。
- **OOS trades**:WFO seal 各窗 n 17-29(MXF 合計 229 / TXF 234);realism 全期 **MXF 282 / TXF 288 / TMF 97**(~3.8-3.9 筆/月,命中 ~4/月目標)。低 WR ~36% + PF 1.5(動量形態,多次小虧少數大賺)。
- **凍結參數**(JSON `best={}` 證實零調參;forward_eval.py 凍結 2026-05-31):NightORB(breakout)60m,全參數釘死零 per-window 優化:`or_bars=8` / `buf_atr=0.35` / `max_or_atr=6.5` / `sl_atr=1.0` / `max_hold=24` / `min_adx=25` / `min_or_atr=2.4`。
- **漂移 caveat**:帶多頭順風車。p(net)=0.063 邊際,同 v7 漂亮淨值相當部分搭牛市 drift。早期弱(2020-21 微負)、近年強(2023-26),有 regime 依賴。零調參輸窗 2021-03(早期)與 **2025-09(最近,值得盯是否近期 regime 變)**。跳空條件化(v4)反而傷,純突破最強。**前推必須跨越非牛市 regime 才算數;夜盤特有 live 摩擦(薄流動性/真實滑價可能>2 點/SDK 斷線/04:55 強平)未含。**

---

## 3. ⚠️ LIVE 接線 PRE-FLIGHT 警告(動工前必讀)

> 以下 deps 全部 **byte-for-byte identical**(LAB == LIVE):`strategy/base.py`、`strategy/breakout.py`、`core/market_data.py`、`core/position.py`。
> import 乾淨、無命名衝突、無 blocking import 問題。**但有兩個非 import 的接線缺口必須先補,否則三支策略永遠不會被觸發或會跑錯 TF。**

### 🚩 GAP 1 — 三個新 strategy_type 尚未掛進 instrument spec(否則永遠落到 fallback)
`_create_strategy(spec.strategy_type)` 目前 `instrument_config.py` 的 `INSTRUMENT_SPECS` **只有 `'breakout'` 與 `'gold_trend'` 兩種 strategy_type 值存在**。
在某個 instrument 的 spec 把 `strategy_type` 設成 `'breakout_v7'` / `'day_orb'` / `'night_v3'` 之前,`_create_strategy` 永遠不會收到這三個新型別,會 fall through 到結尾的 `return AdaptiveMomentumStrategy()`(L136)。
**動工順序:先在 `instrument_config.py` 掛 strategy_type,再驗 `_create_strategy` 收得到。**

### 🚩 GAP 2 — 策略只在 ONE timeframe = `self.timeframe` 被驅動
經逐行 trace 確認:**5m/15m 的 kbar callback 不會驅動策略**。只有 `self.timeframe` 那個 callback(`_on_kbar_complete`,engine.py L767)會 enqueue `('kbar',...)` 事件 → `_engine_loop`(L804) → `_process_kbar` → `pipeline.strategy.on_kbar`(L1052) / `check_exit`(L1035)。
`_on_kbar_5m_complete`(L774) 與 `_on_kbar_15m_complete`(L783) **只更新 `snapshot_5m`/`snapshot_15m`(MTF 快照),不 enqueue 任何事件**。
- `self.timeframe = int(os.getenv('TIMEFRAME','1'))` 在啟動時讀一次(L226),且 **全域單一**,驅動所有 pipeline 的 primary callback。
- **單一進程限制**:無法在同一進程同時跑 `breakout_v7`(5m)與 `night_v3`(60m)於不同 primary TF。**每個 paper/live 進程只能跑一個 TIMEFRAME。** 必須拆成不同進程(各自 `INSTRUMENTS` + `TIMEFRAME` env),與既有 日盤breakout(5m) / 夜盤orb 的拆分一致。

> **無 blocking import / 無 breaking verdict**:deps 全 identical、`will_import_clean=true`、`collisions=[]`、`blocking_issues=[]`。上面兩個是 **接線 GAP(運維前置)**,不是程式碼破壞,但不補就不會生效。

---

## 4. LIVE 接線(可貼上的 code — 全部出自引擎 findings)

> 既有 `'orb'` / `'breakout'` / `'gold_trend'` 分支**不可動**;三個新分支插在結尾 `return AdaptiveMomentumStrategy()`(engine.py L136)**之前**。
> NightGapORBStrategy(v4)定義在 `night_orb.py` 但**未請求 → 不 import / 不註冊**。

### 4.1 imports to add
```python
from strategy.breakout_dualslope import BreakoutDualSlopeStrategy
from strategy.day_orb import DayORBStrategy
from strategy.night_orb import NightORBStrategy
```

### 4.2 `_create_strategy` 新增三個分支(插在 `return AdaptiveMomentumStrategy()` 之前)
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

> ⚠️ 上方 ctor 顯式參數(`or_bars=6`、`force_close=(13,25)`)是引擎 finding 釘死的 **ctor 額外參數 + money-stop backstop**,
> 與 §2 的「凍結 OOS 參數」(`or_bars=7` 等)分屬**不同層**:§2 是 forward_eval.py 的研究凍結配置,§4 是引擎建構子實際傳入的防漂移釘死值。
> 兩者皆按 finding 原文保留,**請勿擅自統一**——若要對齊,需先確認 strategy 內部預設與 forward_eval 凍結配置的對應關係。

### 4.3 TickAggregator intervals 變更(讓 30m/60m bar 真的被建出來)
```text
BEFORE (engine.py InstrumentPipeline.__post_init__ L77):
    self.aggregator = TickAggregator(intervals=[1, 5, 15])
AFTER:
    self.aggregator = TickAggregator(intervals=[1, 5, 15, 30, 60])
```
**這行單獨只建出 30m/60m bar,不會驅動任何策略於 30/60。** 原因(trace 自原始碼):
1. `on_kbar_complete` 註冊(engine.py L246-258)只為 `self.timeframe`、5、15 註冊 callback。**沒有 30/60 的註冊。** `market_data.py` 的 `on_kbar_complete`(L134-137)對「不在 `self._callbacks` 內的 interval」**靜默忽略**;`_callbacks` 以 `self.intervals` 為 key(L122),改 intervals 後 30/60 key 雖存在但**沒有任何 callback 被掛上**。
2. 即使有掛的 5m/15m callback 也**不驅動策略**:`_on_kbar_5m_complete`(L774)/ `_on_kbar_15m_complete`(L783)只刷新 `snapshot_5m`/`snapshot_15m`,從不 enqueue `('kbar',...)`。只有 `_on_kbar_complete`(L767,即 `self.timeframe` callback)enqueue → `_engine_loop`(L804-806) → `_process_kbar` → 唯一呼叫 `pipeline.strategy.on_kbar`(L1052)與 `check_exit`(L1035)之處。
   → **策略只在 ONE TF = `self.timeframe` 被驅動。** 要在 30m/60m 驅動策略,須 (a) 加 30/60 到 intervals(本變更) **且** (b) 設 `TIMEFRAME=30` 或 `60` 讓 `self.timeframe` 選到該 TF。**不需**為 30/60 額外註冊 callback——既有 L247-250 block 已 `pipeline.aggregator.on_kbar_complete(self.timeframe, ... _on_kbar_complete ...)`。額外的 30/60 callback 只在「策略跑於某 primary TF、同時要 30/60 當 SECONDARY MTF 快照」時才需要——**三支都不需要(全是 single-TF)**。
3. 暖身 synth(L427)迭代 `pipeline.aggregator.intervals`,把每個 `interval != 1` 從 1m 史合成。所以 `intervals=[1,5,15,30,60]` 後,L427 loop **已自動合成 30m/60m completed_bars,零額外程式碼**。`get_bars_dataframe(self.timeframe,...)`(L455)接著以 `self.timeframe`(30 或 60)暖 primary 指標引擎,day_orb/night_v3 正確暖機。

### 4.4 TIMEFRAME ↔ 策略 對應(每支跑各自進程)
| strategy_type | TIMEFRAME env | session | 說明 |
|---|---|---|---|
| `breakout_v7` | `TIMEFRAME=5` | 日盤 5m | 同現行 `'breakout'` 分支隱含的 TF |
| `day_orb` | `TIMEFRAME=30` | 日盤 30m | 日 ORB on 30m |
| `night_v3` | `TIMEFRAME=60` | 夜盤 60m | 夜 ORB on 60m |

選擇機制:引擎啟動讀一次 `self.timeframe = int(os.getenv('TIMEFRAME','1'))`(L226);對每個 instrument 以 `_create_strategy(spec.strategy_type)`(L240)建策略、於 `self.timeframe` 註冊 primary kbar callback(L247-250)。
**要 paper-run 某支於其 TF**:把 `INSTRUMENTS` 設為「spec.strategy_type == 目標」的 instrument **且** `TIMEFRAME` 設 5/30/60。因 TIMEFRAME 全域,**每支跑各自進程,不同 TF 的策略不可同進程共處**。

> money-stop / point_value 在三支都是 backstop:真實 P&L 用引擎 instrument spec 的 point_value(對齊研究 OOS)。保留釘死的 `max_loss_twd=4000` / `point_value=10`,使 in-strategy backstop 不會搶在 spec-based 邏輯前 fire。

---

## 5. 資金配置與部署(LIVE 口徑)

**實際上線標的 = TMF 微台**(MXF/TXF 為等比例長歷史代理:MXF = 微台×5 本金 625K;TXF = ×20 本金 2.5M;TMF realism 本金 125K)。

- **風控口徑** `max_loss_twd` = 本金 ×0.032:TMF 125K → **4,000 TWD/邊**(符合 micro-TX caliber);等比 MXF 625K → 20,000;100K → 3,200(~64 點)為破功線。`tmf_3x` 動態 1-3 口;commission 18 元/口/邊 + tax 0.00002 + slippage 1 點。
- **部署建議初始資金**:**TMF NT$150,000**(最低 ~100K)為主力最務實;MXF 600-750K(最低 500K);TXF 2.4-3.0M(最低 2M)。
- **須留 2 倍 DD 緩衝**(夜盤 ~20% MXF caliber / day-ORB ~24% TMF caliber)。資金過低破功:日盤 MXF <250K 反效果;夜盤 MXF <250K 危險、<100K 破功(PF 0.81)。
- **架構**:單 session 單次 login(1 連線,永豐上限 5/身分證) + Strategy Router + **position-lock 仲裁**(同帳戶同商品同時只允一支持倉;日 v7 與日 ORB 同跑日盤須仲裁,夜 v3 時段不重疊無衝突)。**不可開 3 process 各自 login**(部位淨額合併會平錯單)。
- 保證金上線前須查 **TAIFEX 當期公告**(會季調)。

---

## 6. 前推門檻(forward_eval.py 自動標示)

- **凍結時點 2026-05-31**,前推期 **2026-06-01 起**(乾淨 out-of-time)。
- **基線**(對照):PF v7~1.8 / day_ORB~2.0 / night_v3~1.6;DD% ~6/~12/~20;筆/月 ~1.0/~3.5/~4.0;合併 DD~5% 筆/月~10。
- **判定門檻**:
  - ✓ **符合** = PF≥1.0 且 DD≤基線×1.5
  - ⚠️ **偏離** = 0.9≤PF<1.0 或 DD 超基線 1.5x(觀察、不擴大部位)
  - ✗ **失效** = PF<0.9 持續 → 該支下架回研究(**不准再撞 2020-2026**,只能設計全新假設另走前推)
- **頻率偏離閘**:實際筆/月 <基線0.5x 或 >2x → 查資料/邏輯。
- **最少樣本/期間**:最少 6 個月(三支合併 ~10 筆/月 → 6 月 ~60 筆勉強夠看),建議 12 個月。低頻策略需時間,每月覆盤一次累積 ≥6 月再下結論。
- **升級門檻**:全期前推 ✓符合 且合併 DD 未爆(≤10%) → 才考慮 Phase 3 小量實單,從 TMF 微台最小資金起;任一支 ✗失效 → 該支不上線,其餘符合者單獨評估。
- **lockbox 紀律**:前推只觀察 + 通過/否決,**絕不調參**。

---

## 7. 已知 discrepancy(口徑/凍結來源不一致,引用時須標註)

1. **day_orb 凍結參數來源不符**:forward_test_protocol 與 deployment_plan 把 day_ORB 凍結為單一配置(or_bars7/buf0.30/max_or5.0/sl1.0/max_hold30/min_adx17.5/min_or1.3),但對應 sealed 數字的 `wfo_oos_day_orb_v2_ablation.json` 的 `best` **每窗都不同**(or_bars 2-8、min_adx 0-25、min_or 0.3-2.0、max_or 4-7.5、sl 1.0-2.25、max_hold 12-36)——JSON 並非用該凍結配置跑出,凍結值是**選定中位數**而非 WFO 實際優化值。`v1_fade_30m.json` 凍結的 2 旋鈕同樣每窗變動。
2. **day_orb PF 兩處不一致(口徑)**:LESSONS WFO 中位 MXF 2.13 / TXF 2.19(net +1.27M/+5.23M);realism 全期 MXF 2.10 / TXF 1.99(net +1.81M/+6.58M);MCPT 用 2.10 / net +1,805,573。三份文件引用不同 PF 未統一標註口徑。
3. **night_v3 PF 兩處不一致(口徑)**:LESSONS/seal 中位 MXF 1.65 / TXF 1.55;realism 全期 MXF 1.58 / TXF 1.63;MCPT 用 1.58 / net +1,053,913。seal JSON net(+928,015/+3,795,946)≠ realism net(+1,053,913/+4,537,038)。
4. **breakout_v7 PF 方向反轉**:WFO 中位 TXF 2.07 > MXF 1.64;realism 全期 TXF 1.73 < MXF 1.80。TXF 在 realism 反低於 WFO 中位、MXF 反升——文件未解釋此 cross-contract 反轉。
5. **deployment_plan DD 緩衝口徑混用**:「夜盤20%、day-ORB 24%」——夜盤 20% 是 MXF caliber(realism MXF night DD 20.2% / TMF night 21.4%),day-ORB 24% 是 TMF caliber(realism TMF DD 24.0% / MXF 僅 11.3%)。同句混用兩種本金 caliber 的 DD,易誤讀。
6. **TMF 本金不一致**:realism 全部用 TMF 125K 跑(max_loss 4,000 TWD),但 deployment_plan 建議初始 TMF 150K(最低 100K)。回測口徑(125K)與部署建議(150K)不同,前推基線 DD 是 125K 算出的。
7. **v7 net 數字三處不同(口徑差異,非錯誤但需標註)**:WFO JSON net MXF +202,995 / TXF +1,025,414(LESSONS 引)vs realism +348,620 / +1,496,674(MCPT 與 Phase0 引)。MCPT/realism/Phase0 用 realism 值,LESSONS v7 段用 WFO 值。

---

## 8. 資料來源(LIVE repo data/)
| 用途 | JSON |
|---|---|
| v7 WFO 逐窗(出場消融) | `data/wfo_oos_v7_exit_ablation.json` |
| day_orb WFO 消融 | `data/wfo_oos_day_orb_v2_ablation.json`(+ `v1_fade_30m.json`) |
| night_v3 WFO 零調參 seal | `data/wfo_oos_night_v3_seal.json`(`best={}`) |

> realism 全期口徑數字來自各策略 realism 跑批(本報告 §2 引用);MCPT p 值由 `scripts/mcpt.py`(向量化代理 + CPU 多進程,N=300)產生。
