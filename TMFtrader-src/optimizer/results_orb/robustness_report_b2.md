# Robustness Report — Breakout Filter ML

**Test period:** 2025-07-01 onwards  |  **TMF test trades:** 46

---

## Test 7.1 — Monte Carlo Shuffle

- Original total R-multiple: `2.55`
- MC min-cumulative p5:  `-4.84`
- MC min-cumulative p50: `-2.10`
- MC min-cumulative p95: `0.61`
- **Criterion:** lenient (n<30): p5 > -13.0
- **Result: PASS**

---

## Test 7.2 — Label Shuffle

- Real AUC:          `0.4540`
- Shuffled AUC mean: `0.5038` ± `0.0328` (n=20 runs, p=0.129)
- **Criterion:** shuffled < 0.55 AND one-sided t-test p>0.05 (not significantly above 0.50)
- **Result: PASS**

---

## Test 7.3 — Parameter Perturbation

Threshold multipliers tested on TMF OOS trades:

| Multiplier | Threshold | PF | WR | N | MaxDD | Pass |
|---|---|---|---|---|---|---|
| 0.85 | 0.425 | 1.11 | 0.469 | 32 | 5.21 | ✗ |
| 0.90 | 0.450 | 1.23 | 0.484 | 31 | 4.21 | ✗ |
| 0.95 | 0.475 | 1.23 | 0.483 | 29 | 4.21 | ✗ |
| 1.00 | 0.500 | 1.32 | 0.500 | 26 | 4.76 | ✗ |
| 1.05 | 0.525 | 1.32 | 0.500 | 26 | 4.76 | ✗ |
| 1.10 | 0.550 | 1.30 | 0.480 | 25 | 4.90 | ✗ |
| 1.15 | 0.575 | 1.30 | 0.480 | 25 | 4.90 | ✗ |

- **Criterion:** PF > 1.5 and N ≥ 10 (must pass ≥ 4/7 levels)
- Passed: 0/7

- **Result: FAIL**

---

## Test 7.4 — Leave-One-Instrument-Out CV

Train on all other instruments, test on held-out instrument (full dataset):

| Instrument | N | Baseline WR | Filtered WR | Lift | AUC | Pass |
|---|---|---|---|---|---|---|
| FNGS | 229 | 0.537 | 0.554 | 1.031 | 0.5301 | ✓ |
| IBB | 257 | 0.440 | 0.435 | 0.990 | 0.4969 | ✗ |
| IVV | 280 | 0.518 | 0.646 | 1.247 | 0.6951 | ✓ |
| QQQM | 296 | 0.520 | 0.543 | 1.044 | 0.5564 | ✓ |
| SOXX | 264 | 0.553 | 0.574 | 1.037 | 0.5156 | ✓ |
| SPXL | 264 | 0.473 | 0.571 | 1.205 | 0.6916 | ✓ |
| TECL | 290 | 0.500 | 0.548 | 1.095 | 0.5718 | ✓ |
| TMF | 135 | 0.556 | 0.644 | 1.159 | 0.6211 | ✓ |
| TQQQ | 55 | 0.527 | 0.708 | 1.343 | 0.7042 | ✓ |
| XLK | 34 | 0.647 | 0.778 | 1.202 | 0.6288 | ✓ |

- Mean OOS AUC: `0.6012`
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
