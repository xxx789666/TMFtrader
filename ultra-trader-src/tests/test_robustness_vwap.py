# tests/test_robustness_vwap.py
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
from scripts.robustness_vwap_fade import monte_carlo


def test_monte_carlo_returns_three_finite_floats():
    """Positive-expectancy sample -> pf_p5 > 0, net_p5 not NaN, mdd_p95 > 0."""
    rng = np.random.default_rng(0)
    pnl = rng.normal(50, 200, size=120)   # 正期望
    pf_p5, net_p5, mdd_p95 = monte_carlo(pnl, n=2000, seed=42)
    assert np.isfinite(pf_p5) and pf_p5 > 0
    assert np.isfinite(net_p5)
    assert np.isfinite(mdd_p95) and mdd_p95 > 0


def test_monte_carlo_deterministic_with_seed():
    """Same seed -> identical result (regression guard for any rng leak)."""
    pnl = np.array([100, -50, 80, -30, 60, -40, 90, -70, 50, -20], dtype=float)
    r1 = monte_carlo(pnl, n=500, seed=7)
    r2 = monte_carlo(pnl, n=500, seed=7)
    assert r1 == r2


def test_monte_carlo_negative_expectancy_flags():
    """Negative-expectancy sample -> net_p5 < 0 (the gate the Stage 7 test exists to catch)."""
    rng = np.random.default_rng(1)
    pnl = rng.normal(-30, 100, size=200)
    _, net_p5, _ = monte_carlo(pnl, n=2000, seed=42)
    assert net_p5 < 0


# ── Task 10: P4-3 Regime split tests ────────────────────────────────

def test_split_by_regime_basic():
    """Trend trade goes to trend bucket; range trade to range bucket."""
    import pandas as pd
    from scripts.robustness_vwap_fade import split_by_regime
    bars = pd.DataFrame({
        "datetime": pd.to_datetime(["2024-01-02 09:00", "2024-01-02 09:05"]),
        "adx": [15.0, 30.0],
    })
    trades = [
        {"entry_time": "2024-01-02T09:00:00", "pnl": 100.0},   # adx=15 -> range
        {"entry_time": "2024-01-02T09:05:00", "pnl": -50.0},   # adx=30 -> trend
    ]
    out = split_by_regime(trades, bars)
    assert out["range"] == [100.0]
    assert out["trend"] == [-50.0]
    assert out["neutral"] == []


def test_split_by_regime_neutral_band():
    """ADX in [range_thr, trend_thr] goes to neutral."""
    import pandas as pd
    from scripts.robustness_vwap_fade import split_by_regime
    bars = pd.DataFrame({
        "datetime": pd.to_datetime(["2024-01-02 09:00"]),
        "adx": [22.0],   # 20 <= 22 <= 25
    })
    trades = [{"entry_time": "2024-01-02T09:00:00", "pnl": 77.0}]
    out = split_by_regime(trades, bars)
    assert out["neutral"] == [77.0]
    assert out["trend"] == [] and out["range"] == []


def test_split_by_regime_missing_entry_time_skipped():
    """Trade whose entry_time is not in bars is silently skipped (defensive)."""
    import pandas as pd
    from scripts.robustness_vwap_fade import split_by_regime
    bars = pd.DataFrame({
        "datetime": pd.to_datetime(["2024-01-02 09:00"]),
        "adx": [15.0],
    })
    trades = [
        {"entry_time": "2024-01-02T09:00:00", "pnl": 100.0},
        {"entry_time": "2024-01-02T10:00:00", "pnl": 999.0},   # absent from bars
    ]
    out = split_by_regime(trades, bars)
    assert out["range"] == [100.0]   # 999 silently dropped


def test_split_by_regime_invalid_thresholds():
    """trend_thr <= range_thr -> ValueError (guards against accidental swap)."""
    import pandas as pd, pytest
    from scripts.robustness_vwap_fade import split_by_regime
    bars = pd.DataFrame({"datetime": pd.to_datetime(["2024-01-02 09:00"]), "adx": [20.0]})
    with pytest.raises(ValueError):
        split_by_regime([], bars, trend_thr=15, range_thr=25)


# ── Task 9: P4-2 parameter perturbation tests ────────────────────────

def test_stability_low_variance_is_stable():
    from scripts.robustness_vwap_fade import stability
    assert stability([100, 102, 98, 101, 99]) < 0.3        # 穩


def test_stability_high_variance_unstable():
    from scripts.robustness_vwap_fade import stability
    assert stability([100, 10, 200, -50, 300]) > 0.5       # 不穩


def test_stability_zero_mean_returns_inf():
    """Edge: 均值 0 應回 inf (避免 divide-by-zero NaN)。"""
    import math
    from scripts.robustness_vwap_fade import stability
    assert math.isinf(stability([5, -5, 10, -10]))


def test_perturb_and_run_returns_dim_dict():
    """Real-data integration: per-dim runs over a tiny slice; check shape, not values."""
    import pandas as pd
    from pathlib import Path
    p = Path("data/vwap_fade/MXF_day_5m.parquet")
    if not p.exists():
        import pytest; pytest.skip("data missing")
    from scripts.robustness_vwap_fade import perturb_and_run
    df = pd.read_parquet(p).head(2000).reset_index(drop=True)
    best = {"k": 2.0, "k2": 3.0, "sigma_window": 20, "adx_max": 30, "max_bars": 24}
    out = perturb_and_run(best, df, split_idx=1000, factors=(0.9, 1.0, 1.1))
    # All 5 numeric dims present; each list length == len(factors)
    assert set(out.keys()) == set(best.keys())
    for dim, runs in out.items():
        assert len(runs) == 3, f"{dim}: expected 3 runs, got {len(runs)}"
