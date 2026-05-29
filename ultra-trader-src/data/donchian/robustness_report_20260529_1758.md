# donchian Robustness Report -- 20260529_1758

## 1. Best Params (two variants)

### longonly (source: best_params_donchian_TXF_longonly_20260529_1757.json)
- params: {'entry_n': 20, 'exit_k': 10, 'sl_atr': 2.0, 'max_bars': 24, 'cooldown': 3, 'vol_mult': 0.0}
- allow_short: False
- wf_score: 0.8992
- OOS metrics: n=163  WR=53.4%  PF=1.151  ret=+3.70%  dd=3.20%  sharpe=0.828
- OOS freq: 0.37/day over 444 trading days

### biside (source: best_params_donchian_MXF_biside_20260529_1757.json)
- params: {'entry_n': 10, 'exit_k': 15, 'sl_atr': 2.5, 'max_bars': 48, 'cooldown': 3, 'vol_mult': 0.0}
- allow_short: True
- wf_score: 0.7944
- OOS metrics: n=471  WR=51.2%  PF=1.082  ret=+9.63%  dd=10.30%  sharpe=0.485
- OOS freq: 1.06/day over 444 trading days

## 2. Gate Table (side-by-side)

| Gate | Threshold | longonly | biside |
|---|---|---|---|
| MC PF p5 | > 1.0 | 1.151 (PASS) | 1.082 (PASS) |
| MC net p5 | > 0 | 7407 (PASS) | 19261 (PASS) |
| MC MDD p95 | < 12% | 6.75% (PASS) | 18.27% (FAIL) |
| Perturb worst stab | < 0.3 | 0.7796 (FAIL) | 0.4160 (FAIL) |
| 4-3 trend net (main) | > 0 | 1461 (PASS) | 14232 (PASS) |
| 4-3 range net (sec) | > -0.5*|trend_net| | -686 (PASS) | 9042 (PASS) |
| |r| breakout | < 0.3 | 0.0660 (PASS) | 0.0660 (PASS) |
| OOS PF | > 1.2 | 1.151 (FAIL) | 1.082 (FAIL) |
| OOS freq | 1-3/day | 0.37/day (FAIL) | 1.06/day (PASS) |

## 3. Regime Bucket Details

NOTE: Donchian is trend-following -- trend day (ADX>25) = main arena (home turf).

| bucket | role | longonly n / net | biside n / net |
|---|---|---|---|
| trend | main arena | 100 / 1461 | 293 / 14232 |
| range | secondary | 42 / -686 | 102 / 9042 |
| neutral | neutral | 21 / 6632 | 76 / -4013 |

## 4. Observations and Recommendations

**longonly**: 6/9 Gates passed | Failed: Perturb, OOS_PF, OOS_freq
**biside**: 6/9 Gates passed | Failed: MC_MDD_p95, Perturb, OOS_PF

Breakout correlation r = 0.0660  |r| = 0.0660
**Low correlation** |r| < 0.3 -- Donchian adds diversity vs Breakout.

**Decision: Type A -- Both variants FAIL all Gates.**

Donchian breakout does not achieve adequate risk-adjusted OOS performance.
Recommendation: Try C1.b momentum approach (e.g., EMA crossover trend filter)
  before re-testing Donchian. Key failures suggest the N-bar lookback is
  too short to filter whipsaw on TMF 5m. Options:
  - Increase entry_n (try 40-60 range) for more structural breaks
  - Add ADX > 25 entry filter (enter only on confirmed trend days)
  - Combine with volume spike filter to reduce false breakouts

### Context (prior strategies on same TMF OOS)
- vwap_fade:     OOS PF 0.797 (FAIL)
- connors_rsi2:  OOS PF 0.764/0.611 (FAIL)
- or_fade:       OOS PF 0.646/0.741 (FAIL)
- BreakoutTrend: OOS PF 1.513  WR 54.5%  Sharpe 2.474 (LIVE REFERENCE)
- Donchian is the first trend-following candidate. |r| vs Breakout is key.

*Report generated: 2026-05-29T17:58:11.282790*