# Robustness Report — Breakout Filter ML

**Test period:** 2025-07-01 onwards  |  **TMF test trades:** 170

---

## Test 7.1 — Monte Carlo Shuffle

- Original total R-multiple: `8.71`
- MC min-cumulative p5:  `-10.30`
- MC min-cumulative p50: `-3.55`
- MC min-cumulative p95: `0.31`
- **Criterion:** strict (n≥30): p5 > 0
- **Result: FAIL**

---

## Test 7.2 — Label Shuffle

- Real AUC:          `0.5249`
- Shuffled AUC mean: `0.5017` ± `0.0201` (n=20 runs, p=0.197)
- **Criterion:** shuffled < 0.55 AND one-sided t-test p>0.05 (not significantly above 0.50)
- **Result: PASS**

---

## Test 7.3 — Parameter Perturbation

Threshold multipliers tested on TMF OOS trades:

| Multiplier | Threshold | PF | WR | N | MaxDD | Pass |
|---|---|---|---|---|---|---|
| 0.85 | 0.425 | 1.10 | 0.461 | 115 | 11.37 | ✗ |
| 0.90 | 0.450 | 1.13 | 0.453 | 106 | 10.79 | ✗ |
| 0.95 | 0.475 | 1.12 | 0.469 | 98 | 13.13 | ✗ |
| 1.00 | 0.500 | 1.26 | 0.494 | 89 | 11.91 | ✗ |
| 1.05 | 0.525 | 1.28 | 0.494 | 79 | 11.97 | ✗ |
| 1.10 | 0.550 | 1.43 | 0.468 | 62 | 12.41 | ✗ |
| 1.15 | 0.575 | 1.64 | 0.519 | 52 | 7.34 | ✓ |

- **Criterion:** PF > 1.5 and N ≥ 10 (must pass ≥ 4/7 levels)
- Passed: 1/7

- **Result: FAIL**

---

## Test 7.4 — Leave-One-Instrument-Out CV

Train on all other instruments, test on held-out instrument (full dataset):

| Instrument | N | Baseline WR | Filtered WR | Lift | AUC | Pass |
|---|---|---|---|---|---|---|
| FNGS | 832 | 0.512 | 0.519 | 1.013 | 0.5178 | ✓ |
| IBB | 988 | 0.457 | 0.493 | 1.077 | 0.5310 | ✓ |
| IVV | 1130 | 0.488 | 0.615 | 1.261 | 0.6535 | ✓ |
| QQQM | 1153 | 0.482 | 0.553 | 1.146 | 0.6107 | ✓ |
| SOXX | 1032 | 0.496 | 0.502 | 1.013 | 0.5190 | ✓ |
| SPXL | 1113 | 0.473 | 0.600 | 1.269 | 0.6553 | ✓ |
| TECL | 1069 | 0.477 | 0.554 | 1.161 | 0.5958 | ✓ |
| TMF | 507 | 0.465 | 0.566 | 1.216 | 0.5783 | ✓ |
| TQQQ | 162 | 0.426 | 0.667 | 1.565 | 0.7175 | ✓ |
| XLK | 122 | 0.467 | 0.606 | 1.297 | 0.6432 | ✓ |

- Mean OOS AUC: `0.6022`
- Instruments AUC > 0.50: 10/10
- **Criterion:** mean AUC > 0.50 AND ≥ half of instruments pass

- **Result: PASS**

---

## Summary

| Test | Result |
|------|--------|
| 7.1 Monte Carlo | FAIL |
| 7.2 Label Shuffle | PASS |
| 7.3 Perturbation | FAIL |
| 7.4 LOIO CV | PASS |
