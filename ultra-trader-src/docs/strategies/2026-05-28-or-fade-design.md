# OR Fade 策略 設計文件（`or_fade`）

- 日期：2026-05-28
- 狀態：設計（待回測驗證）
- 商品：微型臺指期貨 TMF（live）；回測用 MXF / TXF 長序列、TMF 2024-07+ 作 OOS
- 引擎：沿用 ultra-trader-src（`BaseStrategy` + `backtest/`）
- 前置：vwap_fade FAIL → connors_rsi2 FAIL → 本策略為 vwap_fade 設計文件 §9「備案 C」、**最後一次 MR 嘗試**

---

## 1. 背景與目標

兩個前任 MR 策略 FAIL 的證據：
- **vwap_fade**（連續 VWAP 錨點）：4-3 震盪日虧、OOS PF 0.797、全 Gate 不過
- **connors_rsi2**（RSI(2) 動能極端）：兩變體都 FAIL、**全三 regime 桶皆虧**、OOS PF 0.764 / 0.611

OR fade 是**第三類獨立的 MR alpha**：用**固定離散錨點（開盤後 N 根 OR-mid，鎖住不動）** + **「動能轉弱」過濾**（volume drop）。前兩個策略都沒測過「動能過濾」這個維度。

**目標**：再驗一次「TMF 2024-26 日盤 5m 是否真的對 MR 整類沒 edge」。動能過濾是與前兩個不同的關鍵 alpha，若它也 FAIL，三個獨立 alpha 都失敗，鐵證已足。

**判定原則**：若 OR fade 也 FAIL → **鎖死 MR、pivot C2（拿 pipeline 調優既有 breakout / orb）**。絕不再開第四個日盤 MR 策略。

---

## 2. 範圍 / 非目標

| In Scope | Out of Scope |
|---|---|
| 日盤 08:45–13:45、5 分 K | 夜盤、跨日持倉 |
| OR fade 雙變體並排（long-only / long+short） | ML meta-labeling（先過 Gate） |
| 沿用 PositionSizer / risk_manager / position_lock | 修改 core / 既有策略 |
| 重用 vwap_fade pipeline（資料、grid、三件套） | ADX / EMA200 regime filter（學 connors_rsi2 教訓） |

---

## 3. 策略邏輯

### 3.1 OR 區間管理（自維護）
- 每日第一根 K 開始累計 `_or_high = max`, `_or_low = min`
- 收完 `or_bars` 根後**鎖住** `_or_high / _or_low / _or_mid = (high + low) / 2`，整日不變
- 換日（datetime.date() 變）時清空 OR 狀態 + 重置 `_trades_today / _cooldown_until_bar / _session_bar`
- 提供 `or_locked: bool` 旗標，讓 entry 邏輯只在 lock 後觸發

### 3.2 進場（`on_kbar`，flat + entry_window 內、OR 已鎖）
**動能轉弱**：`vol_ratio = snapshot.volume / snapshot.volume_ma20`（snapshot 已預算欄位、見 `core/gpu_indicators.py:691`）；條件 `vol_ratio ≤ vol_ratio_max`。

```
# 多單（觸下緣 fade up）
if  kbar.low ≤ _or_low and vol_ratio ≤ vol_ratio_max:
    if wait_bars == 0:
        BUY @ close,  stop = close - sl_atr·atr
    elif wait_bars == 1:
        記錄 pending_long = {touch_bar_low}；
        下一根若 low > pending.touch_bar_low（未再創新低、視為拒絕下緣）→ BUY @ close
        否則（low ≤ pending.touch_bar_low，繼續探底）→ 丟棄 pending_long、不進場

# 空單（觸上緣 fade down，allow_short=True 才開）
if  allow_short and kbar.high ≥ _or_high and vol_ratio ≤ vol_ratio_max:
    對稱邏輯
```

- **無 ADX / EMA200 regime filter**（學 connors_rsi2 教訓，讓 4-3 Gate testable）。隱性 regime filter = `vol_ratio`：趨勢日 OR 突破伴隨放量、不會觸發
- 停損凍結於進場（給 PositionSizer 算口數，long stop < entry、short stop > entry——ATR stop 天生安全）
- `Signal.take_profit = round(_or_mid, 1)`（資訊用；實際出場由 check_exit 比對 price 與 _or_mid）
- 進場成功時 `_trades_today += 1`、若 wait=1 清空對應方向的 pending state
- `max_trades` / `cooldown` 守衛同 connors_rsi2

**pending state 生命週期細節**：
- `pending_long` / `pending_short` 為各自獨立的可選 state（可同時存在不互斥；但實務上同一 5m bar 同時觸上下緣機率極低）
- 只活 1 根 K：在「下一根」評估後**必然**清空——無論進場成功或確認失敗
- 換日（`_maybe_daily_reset`）時也清空（不跨日保留）
- 進場成功時清空對應方向的 pending（避免重複觸發）

**ATR stop 與 OR-mid 的關係**：若 `sl_atr · atr` 足夠大，ATR stop 可能落在比 OR-mid 還遠的位置（風險 > 報酬目標）。v1 **不做 clamp**——接受這種情況，由 grid 自然調 `sl_atr` 找風險/報酬合理的組合。

### 3.3 出場（`check_exit` 每根；first-fires-wins）
1. **盤末強平**：`snapshot.timestamp.time() >= force_close` —— **不設 cooldown**（當日已結束、無意義）
2. **ATR 凍結停損**：`long: price ≤ position.stop_loss` / `short: price ≥ position.stop_loss` → 設 `cooldown_until_bar = _session_bar + cooldown`
3. **回到 OR-mid 停利**：`long: price ≥ _or_mid` / `short: price ≤ _or_mid`
4. **時間停損**：`position.bars_since_entry > max_bars`

**OR-mid 鎖住不動**——比 vwap_fade / connors_rsi2 簡單：沒有「持倉期間 update 指標」的需求（OR-mid 在 OR 鎖住之後就是常數）。check_exit 只讀 OR 狀態、不寫。

### 3.4 雙變體
建構子 `allow_short: bool`，per-run 固定（不進 grid）。跑兩輪 grid + 獨立 Gate：long-only / long+short。

---

## 4. 參數表

| 參數 | 預設 | grid 候選 | 說明 |
|---|---|---|---|
| `or_bars` | 6 | [3, 6, 12] | OR 收集 K 棒數（5m×6=30min 經典） |
| `vol_ratio_max` | 0.7 | [0.5, 0.7, 0.9] | 觸邊那根 vol/vol_ma20 上限；越小越嚴 |
| `wait_bars` | 0 | [0, 1] | 0=同根進、1=下根確認沒再創極值 |
| `sl_atr` | 2.0 | [1.5, 2.0, 2.5] | ATR 停損倍數 |
| `max_bars` | 18 | [12, 18, 24] | 時間停損上限 |
| `cooldown` | 3 | [3, 5] | 停損後冷卻根數 |
| `max_trades` | 6 | 固定（v1 不調） | 單日上限 |
| `entry_window_end` | "12:00" | 固定（v1 不調） | start = 08:45 + or_bars·5min（動態） |
| `force_close` | "13:25" | 固定 | |
| `allow_short` | per-run | False / True 各跑一輪 | 不進 grid |

**grid 大小**：3·3·2·3·3·2 = **324 combos** / 變體 / 商品（= 6 個 grid'd 參數的乘積；`max_trades` / `entry_window_end` / `force_close` / `allow_short` 不在 grid 內）。
兩商品 × 兩變體 = **4 runs**，預估 **30–40 分**。

`entry_window_end="12:00"` 給 `max_bars=24` 留滿 2hr 緩衝到 13:25 force_close（最後一筆進場 12:00 持滿 24 根 = 14:00 已 > 13:25，所以 13:25 force_close 必定先觸發）。

**`or_bars=12` 的次數差異**：entry_window 動態 start = 08:45 + or_bars×5min。`or_bars=12` 時 entry 從 09:45 才開始，只剩 09:45–12:00 = 2hr15min（27 根）可進場，明顯短於 `or_bars=3`（09:00 起 3hr = 36 根）。Gate 評估時要注意 `or_bars=12` 的樣本數可能偏少、wf_score 受隨機波動影響較大。

---

## 5. 系統整合

- 新檔 `strategy/or_fade.py`，實作 `BaseStrategy`
- 沿用 `risk.PositionSizer` + `risk_manager`
- 引擎 `FastBacktestEngine`、`instrument="TMF"`（MXF/TXF 當代理時也用 TMF spec）
- 與 vwap_fade / connors_rsi2 / breakout / orb 共存：`position_lock` 互斥
- **核心 0 改動**（snapshot.volume_ma20 已預算、無需擴 precompute）

---

## 6. 回測與驗證計畫（重用 pipeline）

| 階段 | 動作 | 重用 | 新增 |
|---|---|---|---|
| **P0** 成本驗證 | 動態稅 net_pnl 驗證 | 同 connors_rsi2 | 新 test 檔（與 connors_rsi2 內容相同、放在獨立 test 檔） |
| **P1** 資料 | MXF/TXF/TMF day-5m parquet | 已落地 `data/vwap_fade/` | 無 |
| **P2** 策略實作 | `strategy/or_fade.py` + TDD | `connors_rsi2.py` 結構範本（daily_reset, signal, check_exit pattern） | OR session 狀態 + 觸邊+量縮邏輯 + wait_bars 機制 |
| **P3** Grid | `scripts/optimize_or_fade.py` | 抄 `optimize_connors_rsi2.py`（也是 dict-based + `--allow-short` CLI） | 改 PARAM_GRID + 策略 import |
| **P4-1 MC / P4-2 stability / P4-3 regime** | trades pnl 處理 | `scripts.robustness_vwap_fade` 完全重用 | 無 |
| **P5** OOS + Gate 報告 | TMF OOS + 三件套匯總 | 抄 `scripts/run_connors_rsi2_experiments.py` orchestrator | 改策略 import + 輸出路徑 |

---

## 7. 成功標準（Gate）

| Gate | 門檻 |
|---|---|
| P0 成本 | `net_pnl` 確實扣 `(18 + dyn_tax) × 2` per 口 |
| P3 雙商品（per 變體） | MXF/TXF Top-10 每維參數區間重疊 |
| 4-1 MC | PF p5 > 1.0、淨利 p5 > 0、MDD p95 < 12% |
| 4-2 擾動 | worst-dim stability < 0.3 |
| 4-3 Regime | 震盪日 (ADX<20) 淨利 > 0；趨勢日 (ADX>25) 淨利 > −50% × \|震盪日淨利\| |
| P5 OOS | PF > 1.2、頻率 1–3 筆/日、\|r\| breakout < 0.3 |

兩變體獨立評分。若某變體全 Gate 過 → 進 paper；若都不過 → §9 鎖死 MR。

---

## 8. 風險與緩解

| 風險 | 緩解 |
|---|---|
| `vol_ratio_max=0.7` 太嚴 / 太鬆 → 零交易或過度交易 | grid 候選 [0.5, 0.7, 0.9] 跨幅夠；P2d 冒煙會抓 |
| OR 收集期 K 太少 → OR-mid 不穩 | grid `or_bars ∈ [3, 6, 12]` 比較 |
| wait_bars=1 邏輯實作錯（pending state） | TDD 專測「觸邊後 wait=1 + 下根未確認 → 不進場」這條 |
| 與 vwap_fade / connors_rsi2 同類失敗 | 接受可能 FAIL，由 §9 決定下一步 |

---

## 9. 失敗判定（明確退場條件）

若 OR fade 雙變體都 FAIL（任何兩個或以上 Gate 不過、特別是 MC PF p5 < 1.0 或 4-3 兩桶以上虧錢）→ **三個獨立 MR alpha（VWAP / RSI / OR）全 FAIL，鎖死 MR**。

下一步絕對是 **C2：拿這套 pipeline（資料 + grid + 三件套）調優既有 breakout / orb，加深既有 edge**。**絕不**開第四個日盤 MR 策略。

---

## 10. 工作量預估

比 connors_rsi2 **略多**：
- 策略本體 ~120 行（OR 狀態管理 + 觸邊偵測 + wait_bars pending state）
- grid 大 3 倍（324 vs 108 combos / 變體），grid 時間從 ~10s 變 ~30s
- 預估 **5-6 小時**到 Gate 評估完成

---

## 11. 開發紀律提醒（取自 vwap_fade + connors_rsi2 經驗）

已驗證的引擎事實全部繼承自 `2026-05-28-vwap-fade-backtest-plan.md` §1 表，本節只列本策略新增的紀律：

1. **OR 鎖住後不動**——比前兩個簡單，無「持倉期間 update」問題。`check_exit` 只**讀** OR 狀態。
2. **`vol_ratio` 用 snapshot 預算的 `volume_ratio` 或自算 `volume/volume_ma20`**——前者已在 snapshot，優先用。
3. **wait_bars=1 的 pending state 必須在進場成功 / 換日 / 取消後清空**——別讓 pending 跨天保留造成誤觸發。
4. **進場 stop 天然安全**（ATR-only，同 connors_rsi2）——不會像 vwap_fade σ-band 越界。
5. **`_session_bar` 只在 `on_kbar` 增量**（不在 `check_exit`）——維持 connors_rsi2 那個乾淨 off-by-zero cooldown 語意。
6. **`entry_window` start 動態 = 08:45 + or_bars·5min**——必須在 OR 鎖住之後才開始。
7. **per-run `allow_short`**——grid 不放這維、避免 cherry-pick 偏見（同 connors_rsi2）。

---

## 後續步驟

1. 本設計經 spec 審查 + 你複核。
2. 進 `writing-plans` 產出實作計畫。
3. 依計畫實作 → grid → Gate。
4. **若 FAIL → C2**。
