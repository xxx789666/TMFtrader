# Connors RSI(2) 策略 設計文件（`connors_rsi2`）

- 日期：2026-05-28
- 狀態：設計（待回測驗證）
- 商品：微型臺指期貨 TMF（live）；回測用 MXF / TXF 長序列、TMF 2024-07+ 作 OOS
- 引擎：沿用 TMFtrader-src（`BaseStrategy` + `backtest/`）
- 前置：vwap_fade 接受 FAIL 後的接續候選；設計文件 §9 備案 B

---

## 1. 背景與目標

`vwap_fade`（前一支 MR 嘗試）結果：P3 雙商品參數重疊 PASS、4-2 穩定度 PASS，但 4-1 MC（PF 0.797）、4-3 震盪日（淨虧）、P5 OOS（WR 38.8%、PF<1）全 FAIL。結論：在 TMF 2024-26 日盤 5m 用「移動 VWAP 錨點」做 fade 沒 edge。

**目標**：用一個**不同的 alpha 來源**（極短期 RSI(2) 超賣/超買，非均線錨點）再驗一次「TMF 日盤 MR 是否還有路」。Larry Connors 經典規則、跨市場文獻最厚、最 robust 的 MR 模板，最小可行 port 到 TMF。

**判定原則**：若 Connors RSI(2) 也 FAIL → 兩個不同錨點的 MR 都失敗，**TMF 2024-26 日盤 MR 整類沒 edge** 的證據已強，轉去做 trend/momentum 或加碼 breakout/orb 調優。

---

## 2. 範圍 / 非目標

| In Scope | Out of Scope |
|---|---|
| 日盤 08:45–13:45、5 分 K | 夜盤（ORB 占了）、跨日持倉 |
| RSI(2) 雙變體並排測試（long-only vs long+short） | ML meta-labeling（純規則先過 Gate 才考慮） |
| 沿用 PositionSizer / risk_manager / position_lock | 修改 breakout / orb / vwap_fade |
| 重用 vwap_fade pipeline（資料、grid、三件套） | 改 core/、改 IndicatorEngine |

---

## 3. 策略邏輯

### 3.1 RSI(2) 自維護
新 helper `_RsiState(period=2)`：Wilder smoothed avg gain/loss，公開 `update(close)` 與 property `rsi`。**在 `on_kbar` 與 `check_exit` 兩處都呼叫 `update`**（同 vwap_fade `_SessionVwap` 的 close-proxy 套路），避免持倉期間 RSI stale。`precompute_all` 不擴（snap.rsi 是 14 期，不夠用；不動 core 保持回測=live 同碼風險最低）。

**跨日不 reset**：Wilder EMA 連續累積（這與 vwap_fade 的 `_SessionVwap` 每日 reset 相反——Connors 經典 RSI(2) 是連續指標，跨日重設會破壞其 Wilder smoothing 半衰期）。換日的清零工作只發生在策略的 over-trade 計數 (`_trades_today`, `_cooldown_until_bar`)，不動 RSI 狀態。

### 3.2 進場（僅 `entry_window` 內、`flat` 才呼叫）
```
if rsi2 <= rsi_low:                         BUY,  stop = close - sl_atr·atr
if allow_short and rsi2 >= rsi_high:        SELL, stop = close + sl_atr·atr
```
- **無 ADX / EMA200 regime filter**（吸取 vwap_fade 4-3 Gate 無法評估的教訓——任何 regime filter 同時當部署過濾器與驗證篩選器都會讓 Gate 失效；純 RSI 信號讓 4-3 Gate 全 testable）
- 停損凍結於進場（給 PositionSizer 算口數；保 long stop < entry / short stop > entry 不變式）
- `Signal.take_profit = 0`（出場 100% 由 `check_exit` 驅動；無自然 TP 目標，留 0 表示「未設定，靠出場規則決定」）
- `max_trades`/日 + `cooldown` 守衛

### 3.3 出場（`check_exit` 每根呼叫；first-fires-wins）
1. **盤末強平**：`snapshot.timestamp.time() >= force_close`
2. **ATR 凍結停損**：`long: price ≤ position.stop_loss` / `short: price ≥ position.stop_loss` → 同時設 `cooldown_until_bar = session_bar + cooldown`
3. **RSI(2) 回中**：`long: rsi2 ≥ 50` / `short: rsi2 ≤ 50`
4. **時間停損**：`position.bars_since_entry > max_bars`

進場閾值 (≤10 / ≥90) 與出場閾值 (50) 無 overlap，無 instant-exit bug。

### 3.4 雙變體
**一個策略類別**，建構子帶 `allow_short: bool`，**per-run 固定**（不進 grid）。**跑兩輪 grid**：
- `allow_short=False`（long-only Connors 經典）
- `allow_short=True`（多空雙向）

獨立 Gate 結果並排比較，避免「最佳 long+short 組合其實是 long 那邊佔了便宜」的隱性偏見。

---

## 4. 參數表

| 參數 | 預設 | grid 候選 | 說明 |
|---|---|---|---|
| `rsi_period` | 2 | [2, 3] | Connors 經典 2；3 略平滑 |
| `rsi_low` | 10 | [5, 10, 15] | long 進場閾值 |
| `rsi_high` | 90 | [85, 90, 95] | short 進場閾值（僅 allow_short=True） |
| `sl_atr` | 2.0 | [1.5, 2.0, 2.5] | ATR 停損倍數（期貨 5m 必要） |
| `max_bars` | 24 | [12, 18, 24] | 時間停損上限根數 |
| `cooldown` | 3 | [3, 5] | 停損後冷卻 K 棒數 |
| `max_trades` | 6 | 固定（v1 不調） | 單日上限；若 Gate 過再考慮列入 v2 grid |
| `entry_window` | 09:00–13:00 | 固定（v1 不調） | 避開開盤前 15 分與盤末 |
| `force_close` | 13:25 | 固定（v1 不調） | 不留倉 |
| `allow_short` | per-run | False / True 各跑一輪 | 不進 grid（避免 long/short 混在同一 grid 產生 cherry-pick 偏見） |

- **long-only grid**：2·3·3·3·2 = **108 combos** / 商品
- **long+short grid**：2·3·3·3·3·2 = **324 combos** / 商品（多 `rsi_high` 一維）
- 兩商品 × 兩變體 = ~4 grid runs，預估 **總時間 20–30 分**

---

## 5. 系統整合

- 新檔 `strategy/connors_rsi2.py`，實作 `BaseStrategy`：
  - `on_kbar(kbar, snapshot)`：自維護 `_RsiState`、判進場、回 `Signal`
  - `check_exit(position, snapshot)`：update `_RsiState`（持倉維持）、四條出場規則
  - `get_parameters()` / `reset()`
- 風險：沿用 `risk.PositionSizer` + `risk_manager`
- 引擎：`FastBacktestEngine`，`instrument="TMF"`（MXF/TXF 當代理時也用 TMF spec）
- 與 breakout / vwap_fade / orb 共存：`position_lock` 互斥
- **核心 0 改動**

---

## 6. 回測與驗證計畫（重用 vwap_fade pipeline）

| 階段 | 動作 | 重用 | 新增 |
|---|---|---|---|
| **P0** 成本驗證 | 用動態稅模型驗 `net_pnl` | — | 新 test (≈ vwap_fade 的對應 test 換 commit `a95ae44` 後公式) |
| **P1** 資料 | MXF/TXF/TMF day-5m parquet | 已落地 `data/vwap_fade/` | 無 |
| **P2** 策略實作 | `strategy/connors_rsi2.py` + TDD | `_SessionVwap` 設計範本 | `_RsiState` 純邏輯 + 進出場 + cooldown |
| **P3** Grid | `scripts/optimize_connors_rsi2.py` | 抄 `optimize_vwap_fade.py` dict-based worker；改 PARAM_GRID + strategy class | 雙變體 × 雙商品共 4 runs |
| **P4-1 MC** | trade pnl shuffle | `scripts.robustness_vwap_fade.monte_carlo` 直接呼叫 | 無 |
| **P4-2 擾動** | best 參數 × [0.9..1.2] | `perturb_and_run` 直接呼叫 | 無 |
| **P4-3 Regime** | 趨勢/震盪日 PnL 切分 | `split_by_regime` 直接呼叫 | **無 filter→trend 桶有 trades→Gate 不卡關** |
| **P5** OOS | TMF 2024-07+ 跑 best params | 同 vwap_fade Task 11 流程 | 與 breakout 相關性、Gate 匯總報告 |

---

## 7. 成功標準（Gate）

| Gate | 門檻 |
|---|---|
| P0 成本 | net_pnl 確實扣 (18 + dyn_tax)×2 per 口 |
| P3 雙商品 | MXF/TXF Top-10 每維參數區間重疊（per 變體獨立判定） |
| 4-1 MC | PF p5 > 1.0、淨利 p5 > 0、MDD p95 < 12% |
| 4-2 擾動 | worst-dim stability < 0.3 |
| 4-3 Regime | 震盪日 (ADX<20) 淨利 > 0；趨勢日 (ADX>25) 淨利 > −50% × \|震盪日淨利\| |
| P5 OOS | PF > 1.2、頻率 1–3 筆/日、\|r\| breakout < 0.3 |

兩變體獨立評分，最後選 Gate 通過數較多 / 整體表現較好的那一變體進 paper（若都過）。若都不過 → §9 備案。

---

## 8. 風險與緩解

| 風險 | 緩解 |
|---|---|
| 期貨 5m RSI(2) 訊號太頻繁 → 過度交易 | `max_trades`/日 + `cooldown` 守衛、`max_bars` 時間停損 |
| 期貨無 SL 致極端走勢吃光本金 | **必設 ATR 停損**（vwap_fade 也有，這次保持） |
| 與 vwap_fade 同樣的 regime 問題（TMF 整體 MR 沒 edge） | 接受可能 FAIL，由 §9 決定下一步 |
| 雙變體比較產生偏見（cherry-pick 偏好者） | 分開獨立 grid + 獨立 Gate 報告 |

---

## 9. 備案（若 Connors RSI(2) 也 FAIL）

兩個不同錨點的 MR（VWAP / RSI(2)）都 FAIL → 證據強烈指向 **TMF 2024-26 日盤 5m 對 MR 整類沒 edge**。建議轉向：
- **C1**. trend / momentum 策略（例如 short-term momentum + Donchian）
- **C2**. 加碼既有 breakout / orb 的參數調優與 ML filter（既有 edge 加深）
- **C3**. 換時段（夜盤外的縫隙時段、ex. 13:45-15:00 收盤前波動）

不再嘗試日盤新 MR 策略。

---

## 10. 工作量預估

比 vwap_fade **少 ~60%**：
- 策略本體 ~100 行（無 VWAP 雙路徑更新複雜度，只有 RSI state）
- ~7 個 task（vs vwap_fade 11 task），主要省去：資料 prep、robustness module 創建、設計-grid mismatch debug
- 預估 **4–6 小時**到 Gate 評估完成

---

## 11. 開發紀律提醒（取自 vwap_fade 經驗）

**已驗證的引擎事實全部繼承**自 `2026-05-28-vwap-fade-backtest-plan.md` §1 表（`FastBacktestEngine.run` 簽名、出場 100% 由 `check_exit` 驅動、`snapshot.timestamp/adx/atr` 可用且 snapshot 無 high/low/open、引擎跨日不呼叫 `strategy.reset()`、成本動態稅模型、`_calc_metrics` 重用、並行骨架、`MeanReversionStrategy` 範本）——寫實作計畫時直接引用該表，不重覆。本節只列本策略**新增**的紀律提醒：

1. **進場 stop 必在進場價的保護側**（vwap_fade Task 4 抓到的 bug）：long stop < entry、short stop > entry。深訊號時若 σ-band 越界要 fallback（這策略只用 ATR stop，天然安全）。
2. **`snapshot.timestamp` 為時間源**（不可用 `self._current_bar_time`，會 stale）。
3. **持倉期間引擎不呼叫 `on_kbar`**——所有持倉期間狀態（RSI(2)）必須在 `check_exit` 也 update。
4. **`MeanReversionStrategy` 的 SL/TP 結構**仍是最近的範本，新策略沿用。
5. **不自動寫回策略檔**（不抄 `_apply_best_params`）；最佳參數只輸出 JSON，手動帶入。
6. **`split_idx` clamp** 防越界（vwap_fade Task 7 抓到的邊角）。
7. **regime filter 若日後加，必拆 deploy filter / validation filter**（vwap_fade 4-3 Gate 教訓）。

---

## 後續步驟

1. 本設計經 spec 審查 + 你複核。
2. 進 `writing-plans` 產出實作計畫（建檔、回測、grid、Gate 評估的逐步任務）。
3. 依計畫實作 `strategy/connors_rsi2.py` + grid + Gate；再決定是否上 paper。
