# ORB B2 Volume Filter — Design Spec

**Date:** 2026-04-18
**Version:** B2
**Goal:** Raise TMF Layer 1 baseline WR from 48.4% → 55%+, then retrain B2 ML model targeting OOS WR 58%+

---

## Problem

B1 ML Filter (deployed) gives TMF OOS WR = 48.0% at threshold=0.45.
Goal is 55%+ baseline WR so the retrained ML model can push OOS WR to 58%+.

Data analysis (2,194 trades in ml_orb_dataset.parquet):
- TMF stop_loss trades: f_vol_ratio mean = **2.74**
- TMF trail_stop trades: f_vol_ratio mean = **1.99**
- Clear signal: high-volume breakouts are false breakouts (volume traps)

## Filter Design

**Single new parameter in `_MXF_ORB_CFG`:**

```python
"max_breakout_vol_ratio": 2.0,
```

**Logic in `detect_and_label_orb()`:**
After detecting breakout (`close_val > orb_high` or `close_val < orb_low`),
look up `f_vol_ratio` from `feat_lookup` at current bar timestamp.
If `f_vol_ratio > max_breakout_vol_ratio`, skip the trade (set `session_trade_done = True`).

**Scope:** Only `_MXF_ORB_CFG` (TMF night session). `_US_ORB_CFG` unchanged.

## Expected Impact (from simulation on training data)

| Metric | Before (B1 Layer 1) | After (B2 Layer 1) |
|--------|--------------------|--------------------|
| TMF WR | 48.4% | **55.6%** |
| TMF avg_R | -0.042 | **+0.129** |
| TMF N/月 | 8.3 | **5.0** |
| TMF stop_loss rate | 35.6% | **26%** |

## Pipeline Steps

1. **labels_orb.py**: Add `max_breakout_vol_ratio` param + filter logic → regenerate `ml_orb_dataset.parquet`
2. **cv.py**: Re-run cross-validation on new dataset
3. **models.py**: Train new model → `orb_filter_b2.pkl`
4. **optuna_search.py**: Find B2 optimal threshold
5. **robustness.py**: Re-run Stage 7 (target 3/4 gate)
6. **Deploy**: Copy B2 artifacts to `deployed_strategies/tmf_orb_night/`, update docs

## Files Changed

- `optimizer/ml/labels_orb.py` — add parameter + filter logic
- `optimizer/results_orb/orb_filter_b2.pkl` — new model artifact (generated)
- `deployed_strategies/tmf_orb_night/orb_filter_b2.pkl` — deployed model
- `deployed_strategies/tmf_orb_night/策略說明.md` — update params
- `deployed_strategies/tmf_orb_night/回測數據比對.md` — update metrics
- `deployed_strategies/tmf_orb_night/ML過濾器訓練報告.md` — B2 report
- `deployed_strategies/tmf_orb_night/_backtest_data.json` — add b2_ml_filter block
- `scripts/paper_night_orb.py` — update model path to B2

## Not Changed

- `features.py` (`f_vol_ratio` already exists)
- `_US_ORB_CFG` (US ETF config unchanged)
- ORB strategy parameters (orb_minutes=45, sl_atr=2.0, etc.)
