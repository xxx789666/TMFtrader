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


# <Task 9 will append parameter perturbation tests here>
# <Task 10 will append regime split tests here>
