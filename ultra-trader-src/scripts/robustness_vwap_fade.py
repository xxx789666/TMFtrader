"""
P4: vwap_fade 穩健性測試
  P4-1 Monte Carlo (trade-order shuffle)
  P4-2 參數擾動         <-- Task 9
  P4-3 Regime 切分      <-- Task 10
  P5   OOS + Gate 匯總   <-- Task 11
"""
import sys, warnings
warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, ".")

import numpy as np

# ── P4-1 Monte Carlo ─────────────────────────────────────────────────

def monte_carlo(pnl: np.ndarray, n: int = 5000, seed: int = 42, init_eq: float = 200_000.0):
    """
    Trade-order Monte Carlo: shuffle the realized pnl array `n` times,
    compute (PF, total net, MaxDD) per shuffle, return percentiles.

    Returns: (pf_p5, net_p5, mdd_p95)
      - pf_p5  : 5th percentile of Profit Factor distribution
      - net_p5 : 5th percentile of total net PnL
      - mdd_p95: 95th percentile of MaxDD (%)
    """
    rng = np.random.default_rng(seed)
    pfs, nets, mdds = [], [], []
    for _ in range(n):
        s = rng.permutation(pnl)
        gp = float(s[s > 0].sum())
        gl = float(abs(s[s < 0].sum()))
        pfs.append(gp / gl if gl > 1e-6 else 999.0)
        nets.append(float(s.sum()))
        eq = np.concatenate([[init_eq], init_eq + np.cumsum(s)])
        pk = np.maximum.accumulate(eq)
        mdds.append(float(((pk - eq) / pk * 100).max()))
    return (float(np.percentile(pfs, 5)),
            float(np.percentile(nets, 5)),
            float(np.percentile(mdds, 95)))


# ── P4-2 參數擾動 ─────────────────────────────────────────────────────
# <Task 9 will append here>

# ── P4-3 Regime 切分 ──────────────────────────────────────────────────
# <Task 10 will append here>

# ── P5 OOS + Gate 匯總 ────────────────────────────────────────────────
# <Task 11 will append here>
