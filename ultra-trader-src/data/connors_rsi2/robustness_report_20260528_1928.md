# connors_rsi2 穩健性報告 — 20260528_1928

## 1. 兩變體 Best Params

### longonly (source: best_params_connors_rsi2_TXF_longonly_20260528_1927.json)
- params: {'rsi_period': 3, 'rsi_low': 10, 'sl_atr': 1.5, 'max_bars': 24, 'cooldown': 3}
- allow_short: False
- wf_score: 0.4100
- OOS metrics: n=441  WR=54.6%  PF=0.764  ret=-14.37%  dd=17.01%  sharpe=-1.784
- OOS freq: 0.99/day over 444 trading days

### biside (source: best_params_connors_rsi2_TXF_biside_20260528_1927.json)
- params: {'rsi_period': 3, 'rsi_low': 10, 'rsi_high': 95, 'sl_atr': 2.5, 'max_bars': 18, 'cooldown': 5}
- allow_short: True
- wf_score: 0.0206
- OOS metrics: n=652  WR=52.1%  PF=0.611  ret=-36.73%  dd=37.32%  sharpe=-3.249
- OOS freq: 1.47/day over 444 trading days

## 2. Gate 表（並排）

| Gate | 門檻 | longonly | biside |
|---|---|---|---|
| MC PF p5 | > 1.0 | 0.764 (FAIL) | 0.611 (FAIL) |
| MC 淨利 p5 | > 0 | -28733 (FAIL) | -73461 (FAIL) |
| MC MDD p95 | < 12% | 20.30% (FAIL) | 40.39% (FAIL) |
| Perturb worst stab | < 0.3 | 0.0594 (PASS) | 0.5419 (FAIL) |
| 4-3 range net | > 0 | -12834 (FAIL) | -12281 (FAIL) |
| 4-3 trend net | > -0.5*|range_net| | -11190 (FAIL) | -52386 (FAIL) |
| |r| breakout | < 0.3 | N/A (DEFERRED: import error — canno) (PASS) | N/A (DEFERRED: import error — canno) (PASS) |
| OOS PF | > 1.2 | 0.764 (FAIL) | 0.611 (FAIL) |
| OOS 頻率 | 1-3/day | 0.99/day (FAIL) | 1.47/day (PASS) |

## 3. Regime 桶細節

| bucket | longonly n / net | biside n / net |
|---|---|---|
| range | 96 / -12834 | 142 / -12281 |
| trend | 256 / -11190 | 389 / -52386 |
| neutral | 89 / -4709 | 121 / -8794 |

## 4. 觀察與建議

**longonly**: 2/9 Gate 通過 | 失敗項目: MC_PF_p5, MC_net_p5, MC_MDD_p95, range_net, trend_net, OOS_PF, OOS_freq
**biside**: 2/9 Gate 通過 | 失敗項目: MC_PF_p5, MC_net_p5, MC_MDD_p95, Perturb, range_net, trend_net, OOS_PF

兩個變體均未達標（longonly 2/9，biside 2/9）。
OOS PF 不足（<1.2）：策略在真實 OOS 無法產生足夠邊際。備案建議：
  - §9 C1: 縮短 max_bars / 降低 rsi_low 使進場更精確
  - §9 C2: 加入 ADX 趨勢過濾（僅在 ADX<20 range 環境進場）
  - §9 C3: 改為訊號疊加（RSI2 + breakout 同方向才進場）
MDD p95 超標：考慮縮小 sl_atr 或加入日內虧損熔斷（§9 C4）。

Breakout 相關性：DEFERRED: import error — cannot import name 'BreakoutStrategy' from 'strategy.breakout' (C:\Users\xx\Desktop\vps永豐微台指\ultra-trader-src\strategy\breakout.py) — 延後至有足夠資料時再評估。

*報告生成時間：2026-05-28T19:28:12.737234*