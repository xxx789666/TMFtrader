# VWAP 帶狀均值回歸策略 設計文件（`vwap_fade`）

- 日期：2026-05-27
- 狀態：設計（待回測驗證）
- 商品：微型臺指期貨 TMF（live）；回測用同標的 MXF / TXF 長序列
- 引擎：沿用現有 ultra-trader-src（`BaseStrategy` + `backtest/`），不走 LEAN（理由見附錄 A）

---

## 1. 背景與目標

現有兩支 live 策略都是**趨勢/突破型**：

- 日盤 `breakout`（ATR 壓縮突破 + 強趨勢回調）
- 夜盤 `orb`（開盤區間突破 + B2 ML）

兩者在**盤整/震盪盤近乎零進場**。實測 2026-05-20～27 日盤 in_day 掃描數百次、breakout 訊號 0；夜盤 ORB 多晚因區間寬度不符或沒突破而 0 單。問題不是壞掉，是策略本質只吃趨勢。

**目標**：新增一支**日內均值回歸**策略，在趨勢策略閒置的**盤整時段頻繁進場**，補進場機會，且與現有策略**低相關**（不同盤勢、不同報酬來源）。

---

## 2. 範圍 / 非目標

| In Scope | Out of Scope（本次不做） |
|---|---|
| 日盤 08:45–13:45、5 分 K、多空雙向均值回歸 | 夜盤、跨日持倉 |
| 自維護 session VWAP 當均值錨點 | 修改現有 breakout / ORB 邏輯 |
| 沿用現有 PositionSizer / risk_manager / position_lock | 改用 LEAN（見附錄 A） |
| 用 MXF/TXF 長序列回測、TMF 做 OOS | 多商品/組合最佳化 |

---

## 3. 策略邏輯

### 3.1 均值錨點：session VWAP
- 每日 08:45 開盤 **reset**。
- `typical = (high + low + close) / 3`，`VWAP = Σ(typical × volume) / Σ(volume)`（當日累計）。
- 偏離度 `σ`＝(price − VWAP) 的滾動標準差（視窗 `sigma_window` 待定）。
- **量資料 fallback**：若 5 分量資料品質不穩，改用 anchored TWAP / SMA 當錨點（同邏輯、不加權）。

### 3.2 進場（僅 `entry_window` 內）
- 做多：`close ≤ VWAP − k·σ`
- 做空：`close ≥ VWAP + k·σ`
- **regime 過濾（命門）**：`ADX(14) ≤ adx_max` 才開新倉。強趨勢日不接刀，交給 breakout。
- `entry_window` 預設 09:00–13:00（避開開盤前 15 分噪音與盤末）。

### 3.3 出場
- **停利**：回到 VWAP（選配分批：一半回 VWAP、一半 VWAP ± 0.5σ）。
- **停損**：價格續走到 `VWAP ± k₂·σ` 或 ATR 停損，二擇緊者。
- **時間停損**：持倉 > `max_bars` 根。
- **盤末強制平倉**：13:25（不留倉）。

### 3.4 過度交易防護
- 單日最多 `max_trades` 筆。
- 停損後 `cooldown` 根 K 不再開新倉。

---

## 4. 參數表（預設值，待回測 + walk-forward 最佳化）

| 參數 | 預設 | 說明 |
|---|---|---|
| `k`（進場帶） | 2.0 | 偏離 σ 倍數，越大越少進場、越精 |
| `k2`（停損帶） | 3.0 | 續走出此帶停損 |
| `sigma_window` | 待定 | σ 滾動視窗（候選 20 / 全日累計） |
| `adx_max` | 35 | 超過視為強趨勢、不開新倉 |
| `entry_window` | 09:00–13:00 | 開新倉時間窗 |
| `max_bars` | 24（≈2hr） | 時間停損 |
| `force_close` | 13:25 | 盤末強制平倉 |
| `max_trades` | 6 | 單日上限 |
| `cooldown` | 3 | 停損後冷卻根數 |
| `scale_out` | off | 是否分批停利 |
| `sl_atr` | 待定 | ATR 停損倍數（與 k₂ 取緊者） |

---

## 5. 系統整合

- 新檔 `strategy/vwap_fade.py`，實作 `BaseStrategy`：
  - `on_kbar(kbar, snapshot)`：更新 session VWAP/σ、判進場、回 `Signal`。
  - `check_exit(position, snapshot)`：停利/停損/時間/盤末（同 breakout 介面）。
  - `get_parameters()` / `reset()`（每日 reset session 狀態）。
- 指標：`MarketSnapshot` 已提供 `adx / atr / close ...`；**VWAP/σ 由策略自維護**（依日期偵測 reset）。
- 風控：沿用 `risk.PositionSizer`（風險法算口數）+ `risk_manager`（熔斷、日虧上限）。
- **與 breakout 共存**：沿用 `core.position_lock`——日盤同時只有一個策略持 TMF。兩者鎖定相反盤勢（趨勢 vs 震盪），很少同時觸發，衝突低且避免雙重曝險。
- 上線掛載方式（日盤 pipeline / start.py）細節留待「實作計畫」。

---

## 6. 回測與驗證計畫

- **資料**：MXF / TXF 1-min 2020-03～2026-05（聚 5 分、只取日盤）解 TMF 史料僅 23 個月的過擬合問題；TMF 2024-07+ 留作最終 out-of-sample。
- **流程**：`backtest/fast_engine.py` 粗篩參數空間 → `backtest/engine.py` 精算 Top 候選。
- **Walk-forward**：2020–2023 調參 → 2024–2026 驗證；**MXF 與 TXF 雙商品都跑、要求結果一致**（不一致＝過擬合）。
- **成本納入**：期貨交易稅 + 手續費 + 滑價（務必算進淨利，MR 是高頻率、對成本敏感）。

---

## 7. 成功標準

- **頻率**：明顯多於 breakout（目標 ~1–3 筆/日，vs breakout 近期 ~0）。
- 成本後**正期望**、Profit Factor > 1.2。
- 與 breakout / ORB 報酬**低相關**（驗證分散效果）。
- **趨勢日回撤可控**（MR 最該驗的尾部風險；用 regime 過濾 + 停損 + 單日上限壓住）。

---

## 8. 風險與緩解

| 風險 | 緩解 |
|---|---|
| 接刀：強趨勢日連續逆勢虧損 | `adx_max` regime 過濾、`k₂`/ATR 停損、`max_trades` 上限 |
| 過擬合 | walk-forward、MXF+TXF 雙驗、參數數量克制 |
| 5 分量資料品質 | TWAP/SMA 錨點 fallback |
| 與 breakout 撞單 / 雙重曝險 | `position_lock` 互斥 |
| TMF 保證金近期調升（原始 28,900、見前次查證） | PositionSizer 口數 × 保證金 ≤ 可用；上線前對齊 `INITIAL_BALANCE` |

---

## 9. 備案（若 A 回測不過）

- **B. Connors RSI(2)**：RSI(2)<5~10 做多、>90~95 做空，站回 MA5 / RSI 回中出場。最簡單 robust、勝率高、文件最多。
- **C. 開盤後區間 fade**：前 N 根定當日均值/區間，觸邊緣且動能轉弱 fade 回中。（概念上較接近 ORB。）

A → B → C 依回測表現切換。

---

## 附錄 A：引擎決策（為何用自有引擎、不用 LEAN）

評估結論：對「在既有 live 台指系統加一支策略」，**自有引擎優於 LEAN**：

1. **回測=live 同碼**：現有引擎回測/paper/live 跑同一份策略碼，是最大資產；LEAN 接不了永豐 Shioaji（台灣 live 仍得搬回自有引擎），會重新製造回測≠實盤分歧。
2. **台灣市場非 LEAN 原生**：TAIFEX 合約、雙時段行事曆、稅費、保證金、連續月轉倉都要大量客製。
3. 自有引擎已具備 `backtest/engine.py` + `fast_engine.py`，足以驗證本策略。

LEAN 保留為**純研究沙盒**選項（若日後要擴美股/全球或要 walk-forward/Monte-Carlo 等工具，再評估補進自有引擎或用 LEAN 做研究）。

---

## 後續步驟

1. 本設計經 spec 審查 + 你複核。
2. 進 `writing-plans` 產出實作計畫（建檔、回測、walk-forward、上線掛載的逐步任務）。
3. 依計畫實作 `strategy/vwap_fade.py` + 回測 + OOS，再決定是否上 paper / live。
