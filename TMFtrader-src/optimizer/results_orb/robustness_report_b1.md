# Robustness Report — Breakout Filter ML

**Test period:** 2025-07-01 onwards  |  **TMF test trades:** 77

---

## Test 7.1 — Monte Carlo Shuffle

- Original total R-multiple: `5.27`
- MC min-cumulative p5:  `-8.66`
- MC min-cumulative p50: `-3.24`
- MC min-cumulative p95: `0.43`
- **Criterion:** strict (n≥30): p5 > 0
- **Result: FAIL**

---

## Test 7.2 — Label Shuffle

- Real AUC:          `0.4686`
- Shuffled AUC mean: `0.4981` ± `0.0286` (n=20 runs, p=1.000)
- **Criterion:** shuffled < 0.55 AND one-sided t-test p>0.05 (not significantly above 0.50)
- **Result: PASS**

---

## Test 7.3 — Parameter Perturbation

Threshold multipliers tested on TMF OOS trades:

| Multiplier | Threshold | PF | WR | N | MaxDD | Pass |
|---|---|---|---|---|---|---|
| 0.85 | 0.425 | 1.42 | 0.471 | 51 | 6.00 | ✗ |
| 0.90 | 0.450 | 1.50 | 0.480 | 50 | 5.00 | ✓ |
| 0.95 | 0.475 | 1.46 | 0.432 | 44 | 5.15 | ✗ |
| 1.00 | 0.500 | 1.32 | 0.429 | 42 | 6.03 | ✗ |
| 1.05 | 0.525 | 1.25 | 0.400 | 40 | 6.03 | ✗ |
| 1.10 | 0.550 | 1.36 | 0.432 | 37 | 5.03 | ✗ |
| 1.15 | 0.575 | 1.46 | 0.441 | 34 | 4.20 | ✗ |

- **Criterion:** PF > 1.5 and N ≥ 10 (must pass ≥ 4/7 levels)
- Passed: 1/7

- **Result: FAIL**

---

## Test 7.4 — Leave-One-Instrument-Out CV

Train on all other instruments, test on held-out instrument (full dataset):

| Instrument | N | Baseline WR | Filtered WR | Lift | AUC | Pass |
|---|---|---|---|---|---|---|
| FNGS | 229 | 0.537 | 0.586 | 1.091 | 0.5629 | ✓ |
| IBB | 257 | 0.440 | 0.424 | 0.964 | 0.5021 | ✓ |
| IVV | 280 | 0.518 | 0.664 | 1.282 | 0.6438 | ✓ |
| QQQM | 296 | 0.520 | 0.551 | 1.058 | 0.5582 | ✓ |
| SOXX | 264 | 0.553 | 0.525 | 0.949 | 0.4978 | ✗ |
| SPXL | 264 | 0.473 | 0.526 | 1.112 | 0.5968 | ✓ |
| TECL | 290 | 0.500 | 0.544 | 1.088 | 0.5666 | ✓ |
| TMF | 225 | 0.484 | 0.516 | 1.064 | 0.5269 | ✓ |
| TQQQ | 55 | 0.527 | 0.593 | 1.124 | 0.5875 | ✓ |
| XLK | 34 | 0.647 | 0.800 | 1.236 | 0.7311 | ✓ |

- Mean OOS AUC: `0.5774`
- Instruments AUC > 0.50: 9/10
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
