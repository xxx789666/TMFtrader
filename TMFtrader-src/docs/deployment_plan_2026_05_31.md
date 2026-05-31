# 上線規劃 — Shioaji API 並發架構 + 資金配置 / 2026-05-31

> **交付聲明**：本文於 **2026-05-31 交付至 live repo**（`vps永豐微台指/TMFtrader-src`），內容**源自策略研究 repo `tmf-strategy-lab`** 的同名草案，並依**真實 live `core/engine.py` 接線查證結果改寫為自洽 (self-contained) 且 live-accurate**。凡是草案的接線/程式碼與 live engine 不符之處，**一律以 engine 查證結果為準**（已逐行對照 live engine.py / market_data.py / position_lock.py / instrument_config.py）。
>
> 三支已蓋章策略（**breakout_v7 / day_orb / night_v3**）上線前的工程與資金規劃。前提：已過 WFO + 消融 + MCPT（保漂移 N=300）；真實生死仍待**前推**（forward test，2026-06-01 起乾淨 out-of-time）。本文在投入實質資金前先把架構與資金口徑定死。

---

## ⛔ 0. 上線前阻斷項（PRE-FLIGHT BLOCKERS，必須先解，否則無法啟動）

本文描述的三支策略**目前無法在 live engine 啟動**。Verify 階段對 live repo 實際檔案查證，發現兩個硬性缺口：

> ### 🚨 BLOCKER #1 — 三支策略類別在 live repo 不存在
> `core/engine.py` L40-44 目前只 import：
> `BaseStrategy`、`AdaptiveMomentumStrategy`、`GoldTrendStrategy`、`BreakoutTrendStrategy`、`ORBStrategy`。
> `strategy/` 目錄下**沒有** `breakout_dualslope.py`、`day_orb.py`、`night_orb.py` 這三個檔（已 glob 確認 No files found）。
> 在新增這些檔並補上 import 之前，下方 `_create_strategy` 的三個新分支**無法 import、無法 return**。
> → **動作**：先把這三個策略類別從 `tmf-strategy-lab` port 進 live `strategy/`，再加 import。

> ### 🚨 BLOCKER #2 — 三個新 strategy_type 字串沒有對應到任何商品
> `core/instrument_config.py` 的 `INSTRUMENT_SPECS` 目前只有兩個 `strategy_type` 值：`"breakout"`（TMF）與 `"gold_trend"`（TGF）。
> engine 啟動時對每個商品呼叫 `_create_strategy(spec.strategy_type)`（engine.py L240）。
> 只要沒有任何 `InstrumentSpec` 把 `strategy_type` 設成 `"breakout_v7"` / `"day_orb"` / `"night_v3"`，`_create_strategy` 就**永遠收不到這三個字串**，會直接 fall through 到結尾的 `return AdaptiveMomentumStrategy()`（engine.py L136）——**靜默跑錯策略，不會報錯**。
> → **動作**：在 `INSTRUMENT_SPECS` 為要上線的標的（TMF）新增（或新建）對應 spec，把 `strategy_type` 設為目標字串。

依賴一致性（deps 查證）：`strategy/base.py`、`strategy/breakout.py`、`core/market_data.py`、`core/position.py` 四個底層檔 **LAB vs LIVE byte-for-byte 完全相同**，`will_import_clean=true`、無命名衝突、無 breaking。`BreakoutDualSlopeStrategy` 依賴的 `snapshot.ema60` / `snapshot.ema200` 在 live `MarketSnapshot` 都存在並由 `IndicatorEngine.update` 填值；`Signal.trail_dist_pts` 是真實宣告欄位。**唯二阻斷項就是上面兩個 — 都是「沒接線」而非「介面破壞」，補檔 + 補 spec 即可。**

---

## 一、Shioaji API 並發限制（官方）

| 官方限制 | 值 |
|---|---|
| 連線數 | **5 / 身分證（person ID）** |
| 每日登入 | 1000 次 |
| 報價查詢 | 50 次 / 5 秒 |
| 帳務查詢 | 25 次 / 5 秒 |

**結論：不要開 3 個 process 各自 `login()`。** 雖然 3 連線在 5 上限內，但同一期貨帳戶下：
- 部位**淨額合併** → 引擎分不清哪口屬哪支 → **平倉會平錯別支的單**；
- 委託/成交回報**廣播**到所有 session → 路由混亂；
- rate limit / CA / token 共用 → 互相干擾。

---

## 二、✅ 採用架構：每 session 單次 login + position-lock 跨策略互斥

> **live-accurate 修正（取代草案的「單 process 內 Router」圖）**：live 的互斥**不是**單一 process 內的 in-memory router，而是**跨 process 的檔案鎖** `core/position_lock.py`（共用 `data/active_position.json`）。同時，因為 `TIMEFRAME` 是**單一全域環境變數**（engine.py L226），**一個 process 只能跑一個主驅動 TF**，所以三支不同 TF 的策略**本來就得分 process 跑**，再靠檔案鎖做同帳戶/同商品互斥。

### 2.1 為什麼必須分 process（單一 TIMEFRAME 限制）

查證 live event path：
- engine 啟動時讀一次 `self.timeframe = int(os.getenv("TIMEFRAME", "1"))`（L226），**全域、所有 pipeline 共用**。
- 每個商品建好策略後，**只在 `self.timeframe` 這個 TF 註冊主回調**（L247-250：`pipeline.aggregator.on_kbar_complete(self.timeframe, ... _on_kbar_complete ...)`）。
- 只有 `_on_kbar_complete`（L767，即 `self.timeframe` 的回調）會 enqueue `("kbar", (instrument, kbar))` 事件，經 `_engine_loop`（L804-806）路由到 `_process_kbar`，那是**唯一**呼叫 `pipeline.strategy.on_kbar`（L1052）與 `check_exit`（L1035）的地方。
- L251-258 註冊的 **5m / 15m 回調不驅動策略**：`_on_kbar_5m_complete`（L774）與 `_on_kbar_15m_complete`（L783）只更新 `pipeline.snapshot_5m` / `snapshot_15m`（MTF 快照），不 enqueue 任何 kbar 事件。

⇒ **每個策略只在唯一一個 TF = `self.timeframe` 被驅動。** breakout_v7（5m）、day_orb（30m）、night_v3（60m）三個主 TF 不同，**不能塞進同一個 process**。各自開 process，各自設 `INSTRUMENTS` 與 `TIMEFRAME`，沿用既有「日盤 breakout(5m) / 夜盤 ORB」的分 process 既成模式。

### 2.2 跨 process 互斥（position_lock.py，live 既有機制）

```
  process A (日盤 breakout_v7, TIMEFRAME=5)          process B (日盤 day_orb, TIMEFRAME=30)
        │  api.login()  +  activate_ca()                    │  api.login()  +  activate_ca()
        │  (各自 1 連線；同身分證上限 5、足夠)                  │
        ▼                                                    ▼
   進場前: position_lock.is_blocked('breakout_v7')      進場前: position_lock.is_blocked('day_orb')
        │  若對方持有 → reject、跳過此訊號                     │
        ▼                                                    ▼
   進場後: acquire(owner=...)  ── 寫 ──►  data/active_position.json  ◄── 讀 ── is_blocked()
        │                                  (owner + entry_unix；>12h stale 自動 unlink)
        ▼
   出場後: release()  ── 刪鎖 ──►  釋放，對方才可進場
```

- **鎖檔**：`data/active_position.json`，欄位含 `owner` 與 `entry_unix`；`get_holder()` 回傳 `'breakout'`/`'orb'`/`None`；`is_blocked(my_owner)` 在**對方**持倉時回傳鎖內容、自己持有時回 `None`。
- **stale 保護**：`STALE_HOURS = 12`，超過 12h 未更新自動 `unlink`，避免 process crash 留殭屍鎖。
- **同帳戶同商品同時只允一支持倉**：誰先 `acquire` 誰鎖住，其餘策略 `is_blocked` 為真就 reject 進場訊號。
- **時段衝突判定**：
  - **日盤 breakout_v7 與 day_orb 同跑日盤 → 必須仲裁**（否則搶倉/平錯）。兩支都要在進場前查 `is_blocked` 並在進出場 `acquire`/`release`。
  - **夜盤 night_v3 與日盤兩支時段不重疊 → 天然無衝突**（夜盤 21:30 後 / 日盤白天），但仍應沿用同一把鎖，避免跨日邊界殘留部位重疊。
- **`owner` 命名**：live 現有鎖只認 `'breakout'` 與 `'orb'`（見 position_lock.py docstring）。新增 day_orb / breakout_v7 / night_v3 上鎖時，務必為它們指派**各自唯一的 owner 字串**並確保 `is_blocked` 的對方判定涵蓋所有日盤 owner（否則兩支日盤策略會誤判彼此「不是對方」而同時持倉）。

> **替代方案**：若三支各跑**不同合約**（部位不淨額），同帳戶可並存、不需互斥；但實際上線標的只有 TMF 微台一個（MXF/TXF 是回測代理、不真下單），所以**同商品互斥是必經之路**。

---

## 三、Engine 接線改動（可直接貼，全部來自 live engine 查證）

> ⚠️ 下列改動的前提是 **BLOCKER #1 / #2 已解**（檔案已 port、import 已加、spec 已設 strategy_type）。否則新分支永遠不會被觸發。

### 3.1 加入 import（engine.py L40-44 區塊，於現有 import 後新增）

```python
from strategy.breakout_dualslope import BreakoutDualSlopeStrategy
from strategy.day_orb import DayORBStrategy
from strategy.night_orb import NightORBStrategy
```

### 3.2 在 `_create_strategy` 結尾 `return AdaptiveMomentumStrategy()`（L136）**之前**插入三個新分支

既有 `"orb"` / `"breakout"` / `"gold_trend"` 分支**完全不動**。`NightGapORBStrategy`(v4) 雖定義於 `night_orb.py` 但**本次不請求、不 import、不註冊**。

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

### 3.3 擴充 TickAggregator intervals（engine.py `InstrumentPipeline.__post_init__` L77）

```python
# BEFORE (L77):
self.aggregator = TickAggregator(intervals=[1, 5, 15])
# AFTER:
self.aggregator = TickAggregator(intervals=[1, 5, 15, 30, 60])
```

**這個改動本身只「建出」30m/60m K 棒，不會驅動任何策略在 30/60。** 它的必要性與邊界（逐行查證）：

1. `on_tick`（market_data.py）的 `for interval in self.intervals` 迴圈（L159）會據此**真的去組 30m/60m bar**；不加 30/60，這兩個 TF 根本不存在。
2. **暖身 (warmup) 合成迴圈** `for interval in pipeline.aggregator.intervals`（engine.py L427）會把 `!= 1` 的每個 interval 從 1m 歷史合成出來。所以 `intervals=[1,5,15,30,60]` 後，L427 **零額外程式碼**就會自動合成 30m/60m 的 `completed_bars`；接著 `get_bars_dataframe(self.timeframe, ...)`（L455）以 `self.timeframe`（30 或 60）暖身主 indicator engine，day_orb / night_v3 因而正確暖身。
3. **但 30/60 沒有任何回調**：engine 只在 L247-258 註冊 `self.timeframe` / 5 / 15 三個回調，**沒有 30 或 60 的 `on_kbar_complete` 註冊**。加 30/60 進 intervals 後，`market_data._callbacks`（以 `self.intervals` 為 key，L122）雖然會有 30/60 的 key，但 engine 從未對它們掛回調，而 `on_kbar_complete`（market_data.py L134-137）對「不在 `_callbacks` 的 interval」會**靜默忽略**。

⇒ 這對 day_orb（`TIMEFRAME=30`）與 night_v3（`TIMEFRAME=60`）**完全沒問題**：因為 L247-250 的「`self.timeframe` 主回調」會自動掛到 `self.timeframe` 上（即 30 或 60），策略就在它的主 TF 被驅動，**不需要**額外註冊 30/60 回調。

> **只有**當你日後想讓某策略在主 TF 之外**同時把 30m/60m 當次要 MTF 快照**時，才需要手動加註冊與 handler，例如：
> ```python
> pipeline.aggregator.on_kbar_complete(30, lambda kbar, inst=code: self._on_kbar_30m_complete(inst, kbar))
> ```
> 並自寫 `_on_kbar_30m_complete` 用新的 `indicator_engine_30m` 更新 `pipeline.snapshot_30m`。**breakout_v7 / day_orb / night_v3 三支都是單一 TF、都不需要這個**。

### 3.4 TIMEFRAME 對應與啟動方式（每支一個 process）

| 策略 strategy_type | 主驅動 TF | 啟動環境變數 |
|---|---|---|
| `breakout_v7` | **5m** | `TIMEFRAME=5`，`INSTRUMENTS=<spec.strategy_type=breakout_v7 的商品>` |
| `day_orb` | **30m** | `TIMEFRAME=30`，`INSTRUMENTS=<spec.strategy_type=day_orb 的商品>` |
| `night_v3` | **60m** | `TIMEFRAME=60`，`INSTRUMENTS=<spec.strategy_type=night_v3 的商品>` |

機制：engine 啟動讀一次 `self.timeframe`（L226），對每個商品用 `_create_strategy(spec.strategy_type)`（L240）建策略，並在 `self.timeframe` 註冊主回調（L247-250）。**驅動 TF 純由 `TIMEFRAME` env 決定、策略類別純由商品 spec 的 `strategy_type` 決定。** 因為 `TIMEFRAME` 是全域、單值，**每支策略各跑自己的 process**（各自設 INSTRUMENTS + TIMEFRAME），不可把不同 TF 的策略塞進同一 process。

> **money-stop 提醒**：三支的 `max_loss_twd=4000` / `point_value=10` 只是**策略內 backstop**，真實 P&L 用引擎依商品 spec 的 `point_value` 計（對齊研究 OOS）。維持凍結值不要動，確保 in-strategy backstop 不會早於 spec-based 邏輯先觸發。

---

## 四、資金配置（依 evidence.capital_alloc）

### 4.1 標的與本金口徑

- **實際上線標的 = TMF 微台**。MXF / TXF 為**等比例長歷史代理**（MXF = 微台 × 5、本金 625K；TXF = ×20、本金 2.5M；TMF realism 本金 125K）——回測用、**不真下單**。
- **風控口徑** `max_loss_twd = 本金 × 0.032`：
  - TMF 125K → **4,000 TWD/邊**（符合 micro-TX caliber，即三支凍結的 `max_loss_twd=4000`）。
  - 等比 MXF 625K → 20,000；100K → 3,200（約 64 點）為破功線。
- TMF 採 `tmf_3x` 動態 1–3 口；commission 18 元/口/邊 + tax 0.00002 + slippage 1 點。

### 4.2 規格表（點值確定；**保證金以 TAIFEX 當期公告為準、會季調**）

| 商品 | 點值 | 原始保證金（近期約） | 3 口保證金 |
|---|--:|--:|--:|
| TXF 大台 | 200 | ~200,000–260,000 | ~60–78 萬 |
| MXF 小台 | 50 | ~50,000–65,000 | ~15–20 萬 |
| TMF 微台 | 10 | ~10,000–13,000（本 repo spec margin=20,600，2026-02-26 期交所） | ~3–4 萬 |

### 4.3 建議初始資金

| 商品 | 建議初始資金 | 最低 |
|---|--:|--:|
| **TMF 微台（主力）** | **NT$ 150,000** | ~100K |
| MXF 小台 | NT$ 600,000–750,000 | ~500K |
| TXF 大台 | NT$ 2,400,000–3,000,000 | ~2M |

- **以 TMF 15 萬為主**最務實（實際上線只下 TMF）。
- **務必留 2 倍 DD 緩衝**。實測最大 DD（注意 caliber 不同）：
  - **夜盤 night_v3 ~20%**（MXF caliber，realism MXF 20.2% / TMF 21.4%）；
  - **day_orb ~24%**（TMF caliber，realism TMF 24.0%；同策略 MXF 僅 11.3%）；
  - **breakout_v7 最低 DD**（realism MXF 6.4% / TXF 10.0% / TMF Phase0 7.9%）。
- **資金過低會破功**：日盤 MXF < 250K 反效果；夜盤 MXF < 250K 危險、< 100K 破功（PF 0.81）。
- 只跑 1–2 口可降低資金，但別低於破功線。
- **架構約束（重申）**：單 session 單次 login（1 連線，永豐上限 5/身分證）+ Strategy Router + position-lock 仲裁；**不可開 3 process 各自 login**（部位淨額合併會平錯單）。

---

## 五、各策略證據摘要（evidence，凍結時點 2026-05-31）

> PF / net 因**口徑不同**有多個版本（逐窗 WFO vs 全期 realism）。下表標明 MCPT 用的是哪個口徑，並在第六節列出全部已知不一致。

### breakout_v7（日盤 5m，BreakoutDualSlopeStrategy）
- **MCPT（保漂移 N=300）**：p(PF)=0.040 顯著、p(net)=0.080 **邊際**（主要捕捉多頭 drift）；去漂移版 p(PF)≈0.003 但鑑別力弱。來源 `docs/mcpt_significance_2026_05_31.md`（day_v7 列）。
- **OOS PF**：WFO 逐窗中位 MXF 1.64 / TXF 2.07；realism 全期 MXF 1.80 / TXF 1.73（MCPT 用 realism net +348,620 / PF 1.80 MXF）。注意 TXF 在 realism(1.73) 反而**低於** WFO 中位(2.07)。
- **OOS DD**：realism MXF 6.4% / TXF 10.0% / TMF Phase0 7.9% — **三支中最低**。
- **OOS 筆數**：WFO 各窗 n 極小（MXF 3~13）；realism 全期 MXF 76 / TXF 82（~1 筆/月）。**極低頻是核心 caveat**，2024 PF 50+ 是 7 筆噪音。
- **凍結參數**：5m；`slope_thr=0.015` + 雙水平對齊(EMA60/EMA200 同向) + kill-A-short；出場釘死 `trail_trigger_atr=1.2` / `trail_dist_atr=1.25` / `early_cut_bars=50`；進場閾值用各 OOS 窗最佳中位 `min_adx=21` / `afternoon_min_adx=34` / `expand_ratio=1.20`。
- **drift caveat**：帶多頭順風車（p(net)=0.08 邊際）；逐年多數年跑輸買進持有、僅 2022 空頭年贏；edge 為**日盤專屬**（套夜盤 TXF 24h PF 0.91）。**前推必須跨越非牛市 regime 才算數。**

### day_orb（日盤 30m，DayORBStrategy / fade→breakout 結構）
- **MCPT（保漂移 N=300）**：p(net)=0.017 且 p(PF)=0.003 — **兩者皆顯著、三支中最硬、最不依賴 drift**（均值回歸不靠趨勢漲）。來源同上（day_ORB 回歸 列）。
- **OOS PF**：WFO 中位 MXF 2.13 / TXF 2.19；realism 全期 MXF 2.10 / TXF 1.99 / TMF 1.82（MCPT 用 realism net +1,805,573 / PF 2.10 MXF）。
- **OOS DD**：realism MXF 11.3% / TXF 18.6% / **TMF 24.0%（最高）**。
- **OOS 筆數**：WFO 各窗 11~52（充足）；realism 全期 MXF 261 / TXF 256 / TMF 92（~3.0–3.5 筆/月）。統計遠比 v7 扎實。
- **凍結參數**：30m；結構釘死 `or_bars=7` / `buf=0.30` / `max_or=5.0` / `sl=1.0` / `max_hold=30`；選擇性旋鈕中位 `min_adx=17.5` / `min_or_atr=1.3`。
  > ⚠️ **凍結來源不一致**：上述凍結值是**選定中位數**，並非產生 sealed 數字的 JSON (`wfo_oos_day_orb_v2_ablation.json`) 實際每窗優化值（JSON best 每窗都不同：or_bars 2-8、min_adx 0-25 等）。
  > ⚠️ **engine 接線釘的是 `or_bars=6`**（第三節 ctor），與凍結文件的 `or_bars=7` 不一致 — 上線前須統一（建議以 forward_eval.py lockbox 凍結值為準）。
- **drift caveat**：最穩、最少 regime 依賴、MCPT 最顯著（net+PF 皆過）；與 v7 互補。仍有 lockbox 選擇偏誤（全期 +289% 為樂觀值）；輸窗 2021-03 與 2024-09~2025-03。**相對最可信、可優先信任，但生死仍須前推。**

### night_v3（夜盤 60m，NightORBStrategy breakout，零調參 seal）
- **MCPT（保漂移 N=300）**：p(PF)=0.007 顯著、p(net)=0.063 **邊際**（同 v7 主要捕捉 drift）；去漂移 p≈0.003 但鑑別力弱。來源同上（night_v3 突破 列）。
- **OOS PF**：WFO seal 中位 MXF 1.65 / TXF 1.55；realism 全期 MXF 1.58 / TXF 1.63 / TMF 1.43（MCPT 用 realism net +1,053,913 / PF 1.58 MXF）。
- **OOS DD**：realism MXF 20.2% / TXF 18.1% / TMF 21.4%（顯著高於日盤兩支，部位須按高波動設計）；滑價穩健（slip2 仍 PF 1.55）。
- **OOS 筆數**：WFO 各窗 17~29；realism 全期 MXF 282 / TXF 288 / TMF 97（~3.8–3.9 筆/月，命中 ~4/月目標）。低 WR ~36% + PF 1.5（多次小虧少數大賺）。
- **凍結參數**：60m，**全參數釘死、零 per-window 優化**（JSON best={} 證實）：`or_bars=8` / `buf_atr=0.35` / `max_or_atr=6.5` / `sl_atr=1.0` / `max_hold=24` / `min_adx=25` / `min_or_atr=2.4`。
- **drift caveat**：帶多頭順風車（p(net)=0.063 邊際）；早期弱(2020-21 微負)、近年強(2023-26)，有 regime 依賴；輸窗 2021-03 與 **2025-09（最近、值得盯近期 regime 是否變）**；跳空條件化(v4)反而傷、純突破最強。夜盤特有 live 摩擦（薄流動性/真實滑價可能 >2 點/SDK 斷線/04:55 強平）**未含於回測**。**前推必須跨越非牛市 regime 才算數。**

---

## 六、已知不一致 / caveat 清單（discrepancies，上線前須對齊）

1. **day_orb 凍結參數來源不符**：forward_test_protocol 與本 deployment_plan 都把 day_ORB 凍結為單一配置（or_bars7/buf0.30/.../min_adx17.5/min_or1.3），但對應 sealed 數字的 JSON 每窗 best 都不同 — 凍結值是**選定中位數而非 WFO 實際優化值**。（額外：engine ctor 釘的是 or_bars=6，見第三節 ⚠️。）
2. **day_orb PF 兩處不一致（不同口徑）**：LESSONS 引 WFO 中位 MXF 2.13 / TXF 2.19；realism 全期 MXF 2.10 / TXF 1.99；MCPT 用 2.10。三份文件引用不同 PF、未統一標註口徑（逐窗 WFO vs 全期 realism）。
3. **night_v3 PF 兩處不一致**：seal 中位 MXF 1.65 / TXF 1.55；realism 全期 MXF 1.58 / TXF 1.63；seal net 與 realism net 不同（seal +928,015/+3,795,946 vs realism +1,053,913/+4,537,038）。
4. **breakout_v7 PF 方向反轉**：WFO 中位 TXF 2.07 > MXF 1.64；realism 全期 TXF 1.73 < MXF 1.80 — TXF 在 realism 反而**降**、MXF 反而**升**，文件未解釋此 cross-contract 反轉。
5. **DD 緩衝口徑混用**：「夜盤 20%、day-ORB 24%」中，夜盤 20% 是 **MXF caliber**、day-ORB 24% 是 **TMF caliber** — 同句混用兩種本金 caliber 的 DD，易誤讀（本文第 4.3 已逐項標 caliber）。
6. **TMF 本金不一致**：realism 全部用 **TMF 125K**（max_loss 對應 4,000）跑出基線 DD；但部署建議初始 **TMF 150K**（最低 100K）。回測口徑(125K)與部署建議(150K)不同，**前推基線 DD 是 125K 算出的**。
7. **v7 net 三處不同（口徑差異）**：WFO JSON net MXF +202,995 / TXF +1,025,414（LESSONS 引）vs realism +348,620 / +1,496,674（MCPT/Phase0 引）。MCPT/realism/Phase0 用 realism 值、LESSONS v7 段用 WFO 值。

---

## 七、上線順序（對齊 lockbox + 前推）

1. **Phase 0 — 解 BLOCKER**：把 `breakout_dualslope.py` / `day_orb.py` / `night_orb.py` port 進 live `strategy/`、加 engine.py import；在 `instrument_config.py` 為 TMF（上線標的）設對應 `strategy_type`（`breakout_v7`/`day_orb`/`night_v3`）。**未解前 engine 會靜默 fall back 到 AdaptiveMomentum。**
2. **Phase 1 — port 接線**：套用第三節三個 `_create_strategy` 新分支（不覆蓋舊 orb/breakout/gold_trend）+ intervals 改 `[1,5,15,30,60]`；確認 position-lock 涵蓋兩支日盤 owner。
3. **Phase 2 — paper 前推**：每支獨立 process（各自 `INSTRUMENTS` + `TIMEFRAME`=5/30/60）、`simulation=True`/`TRADING_MODE=paper`，2026-06-01 起跑，每月用 `forward_eval.py` 對基線。
4. **Phase 3 — 小量實單**：前推 ≥6 月 ✓ 且**合併 DD ≤ 10%** → production（CA + 實單）、**從 TMF 微台最小資金起**。

---

## 八、前推判定門檻（forward_eval.py，凍結 2026-05-31、前推 2026-06-01 起）

對照基線 PF：v7 ~1.8 / day_ORB ~2.0 / night_v3 ~1.6；DD%：~6 / ~12 / ~20；筆/月：~1.0 / ~3.5 / ~4.0；**合併 DD ~5%、合併筆/月 ~10**。

- **✓ 符合**：PF ≥ 1.0 且 DD ≤ 基線 × 1.5。
- **⚠️ 偏離**：0.9 ≤ PF < 1.0，或 DD 超基線 1.5x（觀察、**不擴大部位**）。
- **✗ 失效**：PF < 0.9 持續 → 該支**下架回研究**（不准再撞 2020-2026，只能設計全新假設另走前推）。
- **頻率偏離閘**：實際筆/月 < 基線 0.5x 或 > 2x → 查資料/邏輯。
- **最少樣本/期間**：最少 6 個月（三支合併 ~10 筆/月 → 6 月 ~60 筆勉強夠看），建議 12 個月；低頻策略需時間，每月覆盤一次、累積 ≥6 月再下結論。
- **升級門檻**：全期前推 ✓ 且合併 DD ≤ 10% → 才考慮 Phase 3；任一支 ✗ → 該支不上線，其餘符合者單獨評估。
- **lockbox 紀律**：前推**只觀察 + 通過/否決，絕不調參**；不再 fit 2020-2026。

---

## 九、風險與紀律（重申）

- MCPT 顯示 **breakout_v7 / night_v3 總報酬含相當 drift capture（p(net) 0.08 / 0.063 邊際）→ 前推須跨非牛市才算數**；**day_orb 最穩**（net+PF 皆顯著、最不依賴 drift）。
- 夜盤 night_v3 的 live 摩擦（薄流動性/滑價 >2 點/SDK 斷線/04:55 強平）**回測未含**，實單須加觀察。
- 保證金數字上線前**務必查 TAIFEX 官網當期公告**（會季調）。
- 三支 `max_loss_twd=4000` 為 in-strategy backstop，真實 P&L 以 engine 依 spec `point_value` 計，**勿改凍結值**。
