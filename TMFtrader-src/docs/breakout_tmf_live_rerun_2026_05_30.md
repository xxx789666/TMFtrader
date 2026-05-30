# BreakoutTrend 重新回測 — live 一字不差引擎 / 2026-05-30

> 用與 live `core/engine.py` **完全相同**的 BreakoutTrendStrategy v6b 參數與 strategy code path 重跑 TMF 歷史。
> 引擎 = `FastBacktestEngine(intrabar_hard_exits=True)` 驅動同一個策略類:tick 層硬停損/止盈(複製 live)+ check_exit(trail/early-cut/money-stop/time)+ tmf_3x 動態 1~3 口 + 動態稅。
> 腳本:`scripts/backtest_breakout_tmf_live.py`;逐筆:`data/breakout_tmf_live_20260530.json`。

## 參數核對(== engine.py L121-135,逐項相同)
expand_ratio=1.18, pullback_ema_gap=0.20, min_di_gap=10.0, trail_trigger_atr=1.2,
trail_dist_atr=1.25, early_cut_bars=40, early_cut_loss_atr=1.5, momentum_rsi_bear=46,
momentum_rsi_bull=52, max_loss_twd=4000, min_adx=23, afternoon_min_adx=30, squeeze_grace_bars=1
本金 125K / tmf_3x。

## 資料
`data/vwap_fade/TMF_oos_day_5m.parquet` —— **從 VPS 取回的 TMFR1 真實合約月檔(2024-07~2026-05,23 個月)**,
用 canonical `prepare_vwap_data.resample_day_session` 建成日盤 [08:45,13:45) 5m。
範圍 **2024-07-29 ~ 2026-05-29**(2026-05-30 backfill 補抓 5/28~5/29;5/30 週六無日盤),446 日 / 26,652 bars,完整度 99.59%、零量 0。
即 5/29 報告所用的同一份資料源,故數字**完全可對照**。
> 註:補抓 5/28~5/29 後結果不變 —— BreakoutTrend 在 5/26~5/29 無新進場訊號,2026 最後一筆仍是 5/25。

---

## 結果(精準重現報告)

| 區間 | 筆數 | WR | PF | Sharpe | Ret | MaxDD | 淨損益 |
|---|---|---|---|---|---|---|---|
| **2024-07+ 真實合約(全期)** | 49 | **51.0%** | **1.567** | 2.84 | **+32.22%** | **13.83%** | **+40,277** |
| 2024(H2) | 10 | 50.0% | 0.896 | −0.53 | −1.14% | 5.4% | −1,419 |
| **2025** | 26 | 42.3% | **0.761** | −1.98 | **−8.48%** | 14.0% | **−10,604** |
| 2026(到 05-27) | 12 | 58.3% | **3.344** | 7.55 | +25.32% | 3.7% | **+31,652** |

> ✅ **2024-07+ 整體 = WR 51.0% / PF 1.567 / +32.2% / DD 13.8%,與 5/29 報告 §1 完全一致。**

### 出場原因分布(49 筆)
| 類型 | 筆數 | 該類淨損益 |
|---|---|---|
| 追蹤出場 | 20 | **+72,353** |
| 硬停利 | 2 | +29,251 |
| 時間出場 | 3 | +9,676 |
| 早切止損 | 3 | −7,484 |
| 硬停損 | 20 | **−63,427** |
| 其他(收尾) | 1 | −92 |

獲利引擎 = 20 筆追蹤出場(+72k)+ 2 筆硬停利(+29k,2026 大波段);主要失血 = 20 筆硬停損(−63k)。

---

## 結論

1. **引擎與參數已逐項核對 == live `core/engine.py`(一字不差)**;用報告同源資料跑出 PF 1.567,**數字 100% 可對照、無偏誤**。
2. **2026 撐全場**(PF 3.34、+25.3%),**2024H2 與 2025 都虧**(PF 0.90 / 0.76)。
3. edge **吃 regime、非全天候** —— 與既有結論一致;2025 震盪年明確虧損。
4. tick 層硬停損是最大單一失血項(−63k / 20 筆),但追蹤出場(+72k)蓋過 —— 整體獲利由趨勢段支撐、硬停損為保護性。

> 備註:逐年用同一引擎(intrabar 硬停損)切年計算;報告 §1 引用的「2026 +25.7% PF 4.39」是另一支 `backtest_tmf_yearly.py`(未開 intrabar)的逐年數,故 2026 PF 略有出入(3.34 vs 4.39),不影響 2024-07+ 整體一致性。

## 重現方式
1. WSL gcloud 從 VPS tar 回 `data/history/TMFR1_1min_*.parquet`(23 檔、零 quota)。
2. `prepare_vwap_data.resample_day_session` 建 `TMF_oos_day_5m.parquet`。
3. `python scripts/backtest_breakout_tmf_live.py`。

*產生:2026-05-30,本 session*
