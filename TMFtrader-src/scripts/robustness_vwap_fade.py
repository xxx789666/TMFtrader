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

def stability(returns) -> float:
    """std/|mean|; 衡量一組績效對參數擾動的散度。穩 < 0.3，崩 > 0.5。
    回 inf 表示均值為 0（無法歸一化）。"""
    arr = np.asarray(returns, dtype=float)
    if arr.size == 0:
        return float("inf")
    m = float(arr.mean())
    if abs(m) < 1e-9:
        return float("inf")
    return float(arr.std(ddof=0) / abs(m))


def perturb_and_run(best_params: dict, df, split_idx: int,
                    factors=(0.9, 0.95, 1.0, 1.05, 1.1, 1.2)) -> dict:
    """Per-dimension perturbation of `best_params`. Returns {dim_name: [test_ret per factor]}.

    Non-numeric / boolean params are skipped. Integer params are rounded and clamped to >=1.

    Aggregate convention (for Gate 4-2):
        max(stability(runs) for runs in results.values())
    The Task 11 / runner calls this; here just the building blocks.
    """
    from scripts.optimize_vwap_fade import _run_one
    results = {}
    for dim, base in best_params.items():
        if isinstance(base, bool):
            continue
        if not isinstance(base, (int, float)):
            continue
        runs = []
        for f in factors:
            params = dict(best_params)
            v = base * f
            if isinstance(base, int):
                v = max(1, int(round(v)))
            params[dim] = v
            res = _run_one(params, df, split_idx=split_idx)
            runs.append(float(res.get("test_ret", 0.0)))
        results[dim] = runs
    return results

# ── P4-3 Regime 切分 ──────────────────────────────────────────────────

def split_by_regime(trades, bars_adx_df,
                    trend_thr: float = 25.0,
                    range_thr: float = 20.0) -> dict:
    """
    Split trade pnls by ADX regime AT ENTRY TIME.

    Args:
      trades:        list[dict] with keys 'entry_time' (isoformat str or Timestamp)
                                       and 'pnl' (float)
      bars_adx_df:   DataFrame with at least columns 'datetime' and 'adx'
                     (caller responsibility — typically df.assign(adx=indicators['adx']))
      trend_thr:     ADX > this  → trend day  (default 25)
      range_thr:     ADX < this  → range day  (default 20)
                     (trend_thr > range_thr; the in-between band is 'neutral')

    Returns:
      {"trend":   [pnl, ...],   # trades entered when ADX > trend_thr
       "range":   [pnl, ...],   # trades entered when ADX < range_thr
       "neutral": [pnl, ...]}   # in-between band

    Trades whose entry_time has no exact match in bars_adx_df['datetime']
    are skipped (with no error). For 5m bars entered on bar timestamps,
    exact match is the normal case.
    """
    import pandas as pd
    if trend_thr <= range_thr:
        raise ValueError(f"trend_thr ({trend_thr}) must be > range_thr ({range_thr})")
    bars = bars_adx_df.copy()
    bars["datetime"] = pd.to_datetime(bars["datetime"])
    adx_by_dt = bars.set_index("datetime")["adx"]
    out = {"trend": [], "range": [], "neutral": []}
    for t in trades:
        ts = pd.to_datetime(t["entry_time"])
        if ts not in adx_by_dt.index:
            continue
        adx = float(adx_by_dt.loc[ts])
        pnl = float(t["pnl"])
        if adx > trend_thr:
            out["trend"].append(pnl)
        elif adx < range_thr:
            out["range"].append(pnl)
        else:
            out["neutral"].append(pnl)
    return out

# ── P5 OOS + Gate 匯總 ────────────────────────────────────────────────
# <Task 11 will append here>
