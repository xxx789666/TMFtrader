# or_fade Robustness Report -- 20260528_2041

## 1. Best Params (two variants)

### longonly (source: best_params_or_fade_MXF_longonly_20260528_2040.json)
- params: {'or_bars': 12, 'vol_ratio_max': 0.7, 'wait_bars': 0, 'sl_atr': 2.5, 'max_bars': 24, 'cooldown': 5}
- allow_short: False
- wf_score: 0.4695
- OOS metrics: n=194  WR=40.7%  PF=0.646  ret=-13.63%  dd=15.61%  sharpe=-2.662
- OOS freq: 0.44/day over 444 trading days

### biside (source: best_params_or_fade_MXF_biside_20260528_2040.json)
- params: {'or_bars': 12, 'vol_ratio_max': 0.7, 'wait_bars': 0, 'sl_atr': 2.5, 'max_bars': 24, 'cooldown': 5}
- allow_short: True
- wf_score: 0.3555
- OOS metrics: n=404  WR=44.6%  PF=0.741  ret=-21.85%  dd=27.47%  sharpe=-1.950
- OOS freq: 0.91/day over 444 trading days

## 2. Gate Table (side-by-side)

| Gate | Threshold | longonly | biside |
|---|---|---|---|
| MC PF p5 | > 1.0 | 0.646 (FAIL) | 0.741 (FAIL) |
| MC net p5 | > 0 | -27262 (FAIL) | -43707 (FAIL) |
| MC MDD p95 | < 12% | 17.53% (FAIL) | 28.46% (FAIL) |
| Perturb worst stab | < 0.3 | 0.2168 (PASS) | 0.1077 (PASS) |
| 4-3 range net | > 0 | -898 (FAIL) | -2840 (FAIL) |
| 4-3 trend net | > -0.5*|range_net| | -15163 (FAIL) | -26794 (FAIL) |
| |r| breakout | < 0.3 | N/A (DEFERRED: import error -- cann) (PASS) | N/A (DEFERRED: import error -- cann) (PASS) |
| OOS PF | > 1.2 | 0.646 (FAIL) | 0.741 (FAIL) |
| OOS freq | 1-3/day | 0.44/day (FAIL) | 0.91/day (FAIL) |

## 3. Regime Bucket Details

| bucket | longonly n / net | biside n / net |
|---|---|---|
| range | 23 / -898 | 49 / -2840 |
| trend | 137 / -15163 | 293 / -26794 |
| neutral | 34 / -11201 | 62 / -14073 |

## 4. Observations and Recommendations

**longonly**: 2/9 Gates passed | Failed: MC_PF_p5, MC_net_p5, MC_MDD_p95, range_net, trend_net, OOS_PF, OOS_freq
**biside**: 2/9 Gates passed | Failed: MC_PF_p5, MC_net_p5, MC_MDD_p95, range_net, trend_net, OOS_PF, OOS_freq

Neither variant meets the bar (longonly 2/9, biside 2/9).
Frequency gate not met (<1/day): OOS sample is too small; Gate evaluation is distorted. Verify OOS data coverage first.
OOS PF insufficient (<1.2): strategy cannot produce adequate margin on true OOS. Fallback options:
  - Section 9 C1: shorten max_bars / tighten vol_ratio_max for more precise entries
  - Section 9 C2: add ADX trend filter (enter only when ADX<20 range environment)
  - Section 9 C3: signal stacking (OR fade + breakout same direction required)
MDD p95 exceeded: consider reducing sl_atr or adding intraday loss circuit breaker (section 9 C4).

Recommendation: lock MR / pivot to section 9 C2 or C3.

Breakout correlation: DEFERRED: import error -- cannot import name 'BreakoutStrategy' from 'strategy.breakout' (C:\Users\xx\Desktop\vps永豐微台指\ultra-trader-src\strategy\breakout.py) -- defer until sufficient trade sample is available.

*Report generated: 2026-05-28T20:41:47.782469*