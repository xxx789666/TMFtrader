# Robustness Report — Breakout Filter ML

**Test period:** 2025-07-01 onwards  |  **TMF test trades:** 17

---

## Test 7.1 — Monte Carlo Shuffle

- Original total R-multiple: `0.00`
- MC min-cumulative p5:  `0.00`
- MC min-cumulative p50: `0.00`
- MC min-cumulative p95: `0.00`
- **Criterion:** p5 > 0
- **Result: FAIL**

---

## Test 7.2 — Label Shuffle

- Real AUC:          `0.7048`
- Shuffled AUC mean: `0.4988` ± `0.0500` (n=20 runs, p=1.000)
- **Criterion:** shuffled < 0.55 AND one-sided t-test p>0.05 (not significantly above 0.50)
- **Result: PASS**

---

## Test 7.3 — Parameter Perturbation

Threshold multipliers tested on TMF OOS trades:

| Multiplier | Threshold | PF | WR | N | MaxDD | Pass |
|---|---|---|---|---|---|---|
| 0.85 | 0.425 | 99.00 | 1.000 | 2 | -0.00 | ✗ |
| 0.90 | 0.450 | 99.00 | 1.000 | 2 | -0.00 | ✗ |
| 0.95 | 0.475 | 0.00 | 0.000 | 0 | 0.00 | ✗ |
| 1.00 | 0.500 | 0.00 | 0.000 | 0 | 0.00 | ✗ |
| 1.05 | 0.525 | 0.00 | 0.000 | 0 | 0.00 | ✗ |
| 1.10 | 0.550 | 0.00 | 0.000 | 0 | 0.00 | ✗ |
| 1.15 | 0.575 | 0.00 | 0.000 | 0 | 0.00 | ✗ |

- **Criterion:** PF > 1.5 and N ≥ 10 (must pass ≥ 4/7 levels)
- Passed: 0/7

- **Result: FAIL**

---

## Test 7.4 — Leave-One-Instrument-Out CV

Train on all other instruments, test on held-out instrument (full dataset):

| Instrument | N | Baseline WR | Filtered WR | Lift | AUC | Pass |
|---|---|---|---|---|---|---|
| BTCUSDT | 119 | 0.580 | 0.598 | 1.031 | 0.6130 | ✓ |
| FNGS | 81 | 0.543 | 0.646 | 1.190 | 0.7383 | ✓ |
| IBB | 167 | 0.587 | 0.765 | 1.304 | 0.8027 | ✓ |
| IVV | 107 | 0.664 | 0.819 | 1.235 | 0.8251 | ✓ |
| QQQM | 155 | 0.587 | 0.708 | 1.206 | 0.8051 | ✓ |
| SOXX | 211 | 0.564 | 0.715 | 1.268 | 0.8397 | ✓ |
| SPXL | 98 | 0.643 | 0.829 | 1.289 | 0.9134 | ✓ |
| TECL | 170 | 0.576 | 0.742 | 1.287 | 0.8530 | ✓ |
| TMF | 36 | 0.750 | 0.957 | 1.275 | 0.8477 | ✓ |
| TQQQ | 22 | 0.727 | 0.800 | 1.100 | 0.8646 | ✓ |
| TX | 5 | 0.600 | 0.750 | 1.250 | 0.8333 | ✓ |
| XLK | 23 | 0.609 | 0.765 | 1.256 | 0.9603 | ✓ |

- Mean OOS AUC: `0.8247`
- Instruments AUC > 0.50: 12/12
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
