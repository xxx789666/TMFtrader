# Robustness Report — Breakout Filter ML

**Test period:** 2025-07-01 onwards  |  **TMF test trades:** 18

---

## Test 7.1 — Monte Carlo Shuffle

- Original total R-multiple: `-1.01`
- MC min-cumulative p5:  `-2.21`
- MC min-cumulative p50: `-1.34`
- MC min-cumulative p95: `-1.01`
- **Criterion:** lenient (n<30): p5 > -4.0
- **Result: PASS**

---

## Test 7.2 — Label Shuffle

- Real AUC:          `0.5282`
- Shuffled AUC mean: `0.4974` ± `0.0328` (n=20 runs, p=1.000)
- **Criterion:** shuffled < 0.55 AND one-sided t-test p>0.05 (not significantly above 0.50)
- **Result: PASS**

---

## Test 7.3 — Parameter Perturbation

Threshold multipliers tested on TMF OOS trades:

| Multiplier | Threshold | PF | WR | N | MaxDD | Pass |
|---|---|---|---|---|---|---|
| 0.85 | 0.425 | 0.31 | 0.333 | 9 | 3.71 | ✗ |
| 0.90 | 0.450 | 0.40 | 0.375 | 8 | 2.71 | ✗ |
| 0.95 | 0.475 | 0.57 | 0.429 | 7 | 2.00 | ✗ |
| 1.00 | 0.500 | 0.57 | 0.429 | 7 | 2.00 | ✗ |
| 1.05 | 0.525 | 0.50 | 0.333 | 6 | 2.00 | ✗ |
| 1.10 | 0.550 | 0.50 | 0.333 | 6 | 2.00 | ✗ |
| 1.15 | 0.575 | 0.50 | 0.333 | 6 | 2.00 | ✗ |

- **Criterion:** PF > 1.5 and N ≥ 10 (must pass ≥ 4/7 levels)
- Passed: 0/7

- **Result: FAIL**

---

## Test 7.4 — Leave-One-Instrument-Out CV

Train on all other instruments, test on held-out instrument (full dataset):

| Instrument | N | Baseline WR | Filtered WR | Lift | AUC | Pass |
|---|---|---|---|---|---|---|
| FNGS | 229 | 0.537 | 0.577 | 1.075 | 0.5720 | ✓ |
| IBB | 257 | 0.440 | 0.479 | 1.088 | 0.5473 | ✓ |
| IVV | 280 | 0.518 | 0.708 | 1.368 | 0.7317 | ✓ |
| QQQM | 296 | 0.520 | 0.583 | 1.121 | 0.6071 | ✓ |
| SOXX | 264 | 0.553 | 0.579 | 1.047 | 0.5434 | ✓ |
| SPXL | 264 | 0.473 | 0.629 | 1.329 | 0.7415 | ✓ |
| TECL | 290 | 0.500 | 0.595 | 1.190 | 0.6361 | ✓ |
| TMF | 73 | 0.466 | 0.421 | 0.904 | 0.4585 | ✗ |
| TQQQ | 55 | 0.527 | 0.760 | 1.441 | 0.7215 | ✓ |
| XLK | 34 | 0.647 | 0.842 | 1.301 | 0.8371 | ✓ |

- Mean OOS AUC: `0.6396`
- Instruments AUC > 0.50: 9/10
- **Criterion:** mean AUC > 0.50 AND ≥ half of instruments pass

- **Result: PASS**

---

## Summary

| Test | Result |
|------|--------|
| 7.1 Monte Carlo | PASS |
| 7.2 Label Shuffle | PASS |
| 7.3 Perturbation | FAIL |
| 7.4 LOIO CV | PASS |
