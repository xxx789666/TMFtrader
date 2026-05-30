"""
Donchian P4+P5 OOS Experiments + Gate Summary (Task 7)
=======================================================
Orchestrates:
  Step 1: Run 4 grids (MXF/TXF x longonly/biside) -- skip if fresh non-dry-run exists
  Step 2: Pick BEST_PARAMS per variant (global top-1 across symbols by wf_score)
  Step 3: OOS backtest on TMF_oos_day_5m.parquet
  Step 4: Robustness three-pack (MC / Perturb / Regime)
  Step 5: Breakout correlation (MANDATORY -- no try/except)
  Step 6: Gate summary markdown report

Usage:
    python scripts/run_donchian_experiments.py
    python scripts/run_donchian_experiments.py --skip-grids   # if grids already done
"""

import sys
import os
import json
import subprocess
import warnings
warnings.filterwarnings("ignore")

from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

OUT_DIR = ROOT / "data" / "donchian"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _log(msg: str):
    print(f"[exp] {msg}", flush=True)


def _load_best_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _find_fresh_grids(symbol: str, variant: str) -> list:
    """Return non-dry-run best_params JSON files for given symbol+variant."""
    pattern = f"best_params_donchian_{symbol}_{variant}_*.json"
    files = []
    for p in OUT_DIR.glob(pattern):
        try:
            d = _load_best_json(p)
            if not d.get("dry_run", False):
                files.append(p)
        except Exception:
            pass
    return sorted(files)


# ─────────────────────────────────────────────────────────────────────────────
# Step 1: Run grids
# ─────────────────────────────────────────────────────────────────────────────

def step1_run_grids(skip_grids: bool = False):
    _log("=" * 60)
    _log("STEP 1: Grid optimization (4 variants)")
    _log("=" * 60)

    variants_to_run = []
    for symbol in ["MXF", "TXF"]:
        for allow_short, variant in [(False, "longonly"), (True, "biside")]:
            existing = _find_fresh_grids(symbol, variant)
            if existing:
                _log(f"  SKIP {symbol}/{variant}: found {existing[-1].name}")
            else:
                variants_to_run.append((symbol, allow_short, variant))

    if skip_grids:
        _log("  --skip-grids flag: skipping all grid runs")
        return

    for symbol, allow_short, variant in variants_to_run:
        cmd = [sys.executable, str(ROOT / "scripts" / "optimize_donchian.py"),
               "--symbol", symbol]
        if allow_short:
            cmd.append("--allow-short")
        _log(f"  Running grid: {symbol}/{variant} ...")
        _log(f"  CMD: {' '.join(cmd)}")
        t0 = datetime.now()
        try:
            result = subprocess.run(cmd, check=True, capture_output=False,
                                    cwd=str(ROOT))
            elapsed = (datetime.now() - t0).total_seconds()
            _log(f"  OK {symbol}/{variant} in {elapsed:.0f}s")
        except subprocess.CalledProcessError as e:
            _log(f"  ERROR {symbol}/{variant}: returncode={e.returncode}")
            raise


# ─────────────────────────────────────────────────────────────────────────────
# Step 2: Pick best params per variant
# ─────────────────────────────────────────────────────────────────────────────

def step2_pick_best() -> dict:
    """Returns {"longonly": best_dict, "biside": best_dict}"""
    _log("=" * 60)
    _log("STEP 2: Pick BEST_PARAMS per variant (global top-1)")
    _log("=" * 60)

    bests = {}
    for variant in ["longonly", "biside"]:
        candidates = []
        for symbol in ["MXF", "TXF"]:
            for p in _find_fresh_grids(symbol, variant):
                d = _load_best_json(p)
                d["_source_file"] = p.name
                candidates.append(d)

        if not candidates:
            raise RuntimeError(
                f"No valid (non-dry-run) best_params for variant={variant}. "
                "Run grids first (remove --skip-grids or run Step 1)."
            )

        # Sort by wf_score descending, take top-1
        # The -50 penalty for test_n<100 naturally filters those out
        candidates.sort(key=lambda x: x.get("wf_score", -999), reverse=True)
        best = candidates[0]
        bests[variant] = best
        _log(f"  {variant}: source={best['_source_file']}")
        _log(f"    wf_score={best['wf_score']:.4f}  allow_short={best['allow_short']}")
        _log(f"    params={best['best_params']}")

    return bests


# ─────────────────────────────────────────────────────────────────────────────
# Step 3: OOS backtest on TMF
# ─────────────────────────────────────────────────────────────────────────────

VALID_CTOR_KEYS = {
    "entry_n", "exit_k", "sl_atr", "max_bars", "cooldown",
    "max_trades", "entry_window_end", "force_close", "point_value",
    "vol_mult",   # v2: 量能濾網 — 必須在此、否則 OOS 重建會靜默丟棄濾網跑 0.0(off)
}
# Keys to drop when building ctor kwargs from the JSON best dict (top-level)
DROP_KEYS = {
    "symbol", "variant", "allow_short", "optimized_at", "split_date", "dry_run",
    "best_params", "wf_score", "metrics", "_source_file",
    "train_n", "train_wr", "train_pf", "train_ret", "train_dd",
    "test_n", "test_wr", "test_pf", "test_ret", "test_dd", "test_sharpe",
}


def _extract_ctor_params(best: dict) -> dict:
    """Extract only strategy ctor-valid keys from the JSON best_params sub-dict."""
    raw = best.get("best_params", {})
    return {k: v for k, v in raw.items() if k in VALID_CTOR_KEYS}


def step3_oos(bests: dict) -> dict:
    """Run OOS on TMF for each variant. Returns metrics + trades per variant."""
    _log("=" * 60)
    _log("STEP 3: OOS backtest on TMF_oos_day_5m.parquet")
    _log("=" * 60)

    import pandas as pd
    from core.logger import setup_logger
    setup_logger(console_level="CRITICAL")

    from core.gpu_indicators import precompute_all
    from backtest.fast_engine import FastBacktestEngine
    from strategy.donchian import DonchianStrategy
    from scripts.optimize_strategy import _calc_metrics

    oos_path = ROOT / "data" / "vwap_fade" / "TMF_oos_day_5m.parquet"
    if not oos_path.exists():
        raise FileNotFoundError(f"OOS data not found: {oos_path}")

    _log(f"  Loading OOS data: {oos_path.name}")
    df_oos = pd.read_parquet(oos_path).reset_index(drop=True)
    _log(f"  OOS bars: {len(df_oos):,}  "
         f"({df_oos['datetime'].iloc[0].date()} ~ {df_oos['datetime'].iloc[-1].date()})")

    _log("  Precomputing indicators ...")
    ind_oos = precompute_all(df_oos, verbose=False)

    results = {}
    for variant, best in bests.items():
        allow_short = best["allow_short"]
        valid_params = _extract_ctor_params(best)
        _log(f"\n  --- {variant} (allow_short={allow_short}) ---")
        _log(f"    ctor params: {valid_params}")

        strat = DonchianStrategy(allow_short=allow_short, **valid_params)
        eng = FastBacktestEngine(initial_balance=200_000.0, instrument="TMF")
        res = eng.run(df_oos, ind_oos, strat, "balanced")
        metrics = _calc_metrics(res)
        trades = list(res.trades) if hasattr(res, "trades") else []

        _log(f"    n={metrics['n']}  WR={metrics['wr']:.1f}%  PF={metrics['pf']:.3f}  "
             f"ret={metrics['ret']:+.2f}%  dd={metrics['dd']:.2f}%  sharpe={metrics['sharpe']:.3f}")

        results[variant] = {
            "metrics": metrics,
            "trades": trades,
            "df_oos": df_oos,
            "ind_oos": ind_oos,
        }

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Step 4: Robustness three-pack
# ─────────────────────────────────────────────────────────────────────────────

def step4_robustness(bests: dict, oos_results: dict) -> dict:
    """Run MC / Perturb / Regime for each variant. Returns gate values per variant."""
    _log("=" * 60)
    _log("STEP 4: Robustness three-pack")
    _log("=" * 60)

    import numpy as np
    from scripts.robustness_vwap_fade import monte_carlo, stability, split_by_regime
    from scripts.optimize_donchian import _run_one as _run_one_d

    robustness = {}

    for variant, best in bests.items():
        _log(f"\n  === {variant} ===")
        oos = oos_results[variant]
        trades = oos["trades"]
        df_oos = oos["df_oos"]
        ind_oos = oos["ind_oos"]

        rob = {}

        # 4-1 Monte Carlo
        _log("  [4-1] Monte Carlo ...")
        if len(trades) >= 30:
            pnl = np.array([t["pnl"] for t in trades], dtype=float)
            pf_p5, net_p5, mdd_p95 = monte_carlo(pnl, n=5000, seed=42)
            rob["mc_pf_p5"]   = pf_p5
            rob["mc_net_p5"]  = net_p5
            rob["mc_mdd_p95"] = mdd_p95
            rob["mc_note"]    = ""
            _log(f"    pf_p5={pf_p5:.3f}  net_p5={net_p5:.0f}  mdd_p95={mdd_p95:.2f}%")
        else:
            rob["mc_pf_p5"]   = float("nan")
            rob["mc_net_p5"]  = float("nan")
            rob["mc_mdd_p95"] = float("nan")
            rob["mc_note"]    = f"INSUFFICIENT_TRADES ({len(trades)}<30)"
            _log(f"    SKIP: {rob['mc_note']}")

        # 4-2 Perturbation (using _run_one_d from optimize_donchian)
        _log("  [4-2] Perturbation ...")
        # v2: 加 vol_mult — 擾動濾網門檻測穩定性（若 best vol_mult=0.0 則退化為常數、stability 0、無害）
        VALID_PERTURB_KEYS = {"entry_n", "exit_k", "sl_atr", "max_bars", "cooldown", "vol_mult"}
        valid_params = _extract_ctor_params(best)
        perturb_base = {k: v for k, v in valid_params.items() if k in VALID_PERTURB_KEYS}
        allow_short = best["allow_short"]
        factors = (0.9, 0.95, 1.0, 1.05, 1.1, 1.2)

        perturb_results = {}
        for dim, base_val in perturb_base.items():
            if isinstance(base_val, bool):
                continue
            if not isinstance(base_val, (int, float)):
                continue
            runs = []
            for f in factors:
                p = dict(perturb_base)
                v = base_val * f
                if isinstance(base_val, int):
                    v = max(1, int(round(v)))
                p[dim] = v
                try:
                    r = _run_one_d(p, df_oos, split_idx=0, allow_short=allow_short)
                    runs.append(float(r.get("test_ret", 0.0)))
                except Exception as e:
                    _log(f"      perturb {dim}={v:.2f} error: {e}")
                    runs.append(0.0)
            perturb_results[dim] = runs
            _log(f"    {dim}: runs={[f'{x:.2f}' for x in runs]}")

        stabilities = {d: stability(r) for d, r in perturb_results.items()}
        worst_stability = max(stabilities.values()) if stabilities else float("inf")
        rob["perturb_stabilities"] = stabilities
        rob["worst_stability"]     = worst_stability
        _log(f"    stabilities={stabilities}")
        _log(f"    worst_stability={worst_stability:.4f}  (gate<0.3)")

        # 4-3 Regime split
        # Donchian is trend-following: trend day (ADX>25) = MAIN ARENA
        _log("  [4-3] Regime split (trend-following: ADX>25 = main arena) ...")
        bars_adx = df_oos.assign(adx=ind_oos["adx"])
        buckets = split_by_regime(trades, bars_adx, trend_thr=25, range_thr=20)
        range_net = sum(buckets["range"])
        trend_net = sum(buckets["trend"])
        neutral_net = sum(buckets["neutral"])
        range_n = len(buckets["range"])
        trend_n = len(buckets["trend"])
        neutral_n = len(buckets["neutral"])
        rob["buckets"] = {
            "range":   {"n": range_n,   "net": range_net},
            "trend":   {"n": trend_n,   "net": trend_net},
            "neutral": {"n": neutral_n, "net": neutral_net},
        }
        rob["range_net"] = range_net
        rob["trend_net"] = trend_net
        _log(f"    trend (main):   n={trend_n}  net={trend_net:.0f}  [MAIN ARENA]")
        _log(f"    range (sec):    n={range_n}  net={range_net:.0f}")
        _log(f"    neutral:        n={neutral_n}  net={neutral_net:.0f}")

        robustness[variant] = rob

    return robustness


# ─────────────────────────────────────────────────────────────────────────────
# Step 5: Breakout correlation (MANDATORY -- no try/except)
# ─────────────────────────────────────────────────────────────────────────────

def step5_breakout_correlation(trades: list, df_oos) -> float:
    """Mandatory -- import errors are surfaced (no try/except)."""
    _log("=" * 60)
    _log("STEP 5: Breakout correlation (mandatory)")
    _log("=" * 60)

    from core.gpu_indicators import precompute_all
    from backtest.fast_engine import FastBacktestEngine
    from strategy.breakout import BreakoutTrendStrategy   # correct class name
    import pandas as pd
    import numpy as np

    ind = precompute_all(df_oos, verbose=False)
    res_b = FastBacktestEngine(initial_balance=200_000, instrument="TMF").run(
        df_oos, ind, BreakoutTrendStrategy(), "balanced")

    # Donchian daily PnL keyed by exit_time (consistent with engine res_b.daily_pnl)
    c_dp = {}
    for t in trades:
        d = pd.to_datetime(t["exit_time"]).date().isoformat()
        c_dp[d] = c_dp.get(d, 0) + t["pnl"]

    days = sorted(set(res_b.daily_pnl) | set(c_dp.keys()))
    b_series = np.array([res_b.daily_pnl.get(d, 0) for d in days], dtype=float)
    c_series = np.array([c_dp.get(d, 0) for d in days], dtype=float)
    if len(b_series) < 5 or b_series.std() < 1e-9 or c_series.std() < 1e-9:
        _log("  WARN: too few days or zero-variance -- returning nan")
        return float("nan")
    r = float(np.corrcoef(b_series, c_series)[0, 1])
    _log(f"  Pearson r(donchian, breakout) = {r:.4f}  (gate |r|<0.3)")
    return r


# ─────────────────────────────────────────────────────────────────────────────
# Step 6: Gate summary report
# ─────────────────────────────────────────────────────────────────────────────

def _pass_fail(condition: bool) -> str:
    return "PASS" if condition else "FAIL"


def _fmt(val, fmt=".3f") -> str:
    if val != val:  # nan
        return "N/A"
    return format(val, fmt)


def step6_report(bests: dict, oos_results: dict, robustness: dict,
                 r_val: float) -> "Path":
    _log("=" * 60)
    _log("STEP 6: Writing Gate summary report")
    _log("=" * 60)

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    report_path = OUT_DIR / f"robustness_report_{ts}.md"

    lines = []
    a = lines.append

    variants = ["longonly", "biside"]

    a(f"# donchian Robustness Report -- {ts}")
    a("")
    a("## 1. Best Params (two variants)")
    a("")

    for variant in variants:
        best = bests[variant]
        src  = best.get("_source_file", "unknown")
        bp   = best.get("best_params", {})
        wf   = best.get("wf_score", float("nan"))
        m    = oos_results[variant]["metrics"]
        n_trades = m["n"]
        df_oos = oos_results[variant]["df_oos"]
        n_days = df_oos["datetime"].dt.date.nunique()
        freq_per_day = n_trades / n_days if n_days > 0 else 0.0

        a(f"### {variant} (source: {src})")
        a(f"- params: {bp}")
        a(f"- allow_short: {best['allow_short']}")
        a(f"- wf_score: {wf:.4f}")
        a(f"- OOS metrics: n={n_trades}  WR={m['wr']:.1f}%  PF={m['pf']:.3f}  "
          f"ret={m['ret']:+.2f}%  dd={m['dd']:.2f}%  sharpe={m['sharpe']:.3f}")
        a(f"- OOS freq: {freq_per_day:.2f}/day over {n_days} trading days")
        a("")

    a("## 2. Gate Table (side-by-side)")
    a("")
    a("| Gate | Threshold | longonly | biside |")
    a("|---|---|---|---|")

    def gate_row(label: str, threshold: str, lo_val, bi_val,
                 lo_pass: bool, bi_pass: bool):
        lo_str = f"{lo_val} ({_pass_fail(lo_pass)})"
        bi_str = f"{bi_val} ({_pass_fail(bi_pass)})"
        a(f"| {label} | {threshold} | {lo_str} | {bi_str} |")

    # --- pre-gather per-variant stats ---
    for variant in variants:
        rob = robustness[variant]
        m   = oos_results[variant]["metrics"]
        df_oos = oos_results[variant]["df_oos"]
        n_days = df_oos["datetime"].dt.date.nunique()
        n_trades = m["n"]
        freq_per_day = n_trades / n_days if n_days > 0 else 0.0
        rob["_metrics"] = m
        rob["_freq_per_day"] = freq_per_day

    lo = robustness["longonly"]
    bi = robustness["biside"]

    # MC PF p5
    lo_pf_p5 = lo["mc_pf_p5"]; bi_pf_p5 = bi["mc_pf_p5"]
    gate_row("MC PF p5", "> 1.0",
             _fmt(lo_pf_p5), _fmt(bi_pf_p5),
             (lo_pf_p5 > 1.0) if lo_pf_p5 == lo_pf_p5 else False,
             (bi_pf_p5 > 1.0) if bi_pf_p5 == bi_pf_p5 else False)

    # MC net p5
    lo_net = lo["mc_net_p5"]; bi_net = bi["mc_net_p5"]
    gate_row("MC net p5", "> 0",
             _fmt(lo_net, ".0f"), _fmt(bi_net, ".0f"),
             (lo_net > 0) if lo_net == lo_net else False,
             (bi_net > 0) if bi_net == bi_net else False)

    # MC MDD p95
    lo_mdd = lo["mc_mdd_p95"]; bi_mdd = bi["mc_mdd_p95"]
    gate_row("MC MDD p95", "< 12%",
             _fmt(lo_mdd, ".2f") + "%", _fmt(bi_mdd, ".2f") + "%",
             (lo_mdd < 12.0) if lo_mdd == lo_mdd else False,
             (bi_mdd < 12.0) if bi_mdd == bi_mdd else False)

    # Perturb worst stability
    lo_ws = lo["worst_stability"]; bi_ws = bi["worst_stability"]
    gate_row("Perturb worst stab", "< 0.3",
             _fmt(lo_ws, ".4f"), _fmt(bi_ws, ".4f"),
             (lo_ws < 0.3) if lo_ws == lo_ws else False,
             (bi_ws < 0.3) if bi_ws == bi_ws else False)

    # 4-3 Regime (Donchian trend-following: trend day = MAIN ARENA, range = secondary)
    # Gate: trend day net > 0 (main arena must be profitable)
    # Gate: range day net > -0.5 * |trend day net| (secondary must not bleed too badly)
    lo_tn = lo["trend_net"]; bi_tn = bi["trend_net"]
    gate_row("4-3 trend net (main)", "> 0",
             _fmt(lo_tn, ".0f"), _fmt(bi_tn, ".0f"),
             lo_tn > 0, bi_tn > 0)

    lo_rn = lo["range_net"]; bi_rn = bi["range_net"]
    lo_thr = -0.5 * abs(lo_tn); bi_thr = -0.5 * abs(bi_tn)
    gate_row("4-3 range net (sec)", "> -0.5*|trend_net|",
             _fmt(lo_rn, ".0f"), _fmt(bi_rn, ".0f"),
             lo_rn > lo_thr, bi_rn > bi_thr)

    # Breakout |r| -- mandatory (no skip on nan)
    if r_val != r_val:
        lo_r_str = bi_r_str = "N/A (nan)"
        lo_r_pass = bi_r_pass = False  # FAIL if nan (mandatory step)
    else:
        lo_r_str = bi_r_str = _fmt(abs(r_val), ".4f")
        lo_r_pass = bi_r_pass = abs(r_val) < 0.3
    gate_row("|r| breakout", "< 0.3",
             lo_r_str, bi_r_str,
             lo_r_pass, bi_r_pass)

    # OOS PF
    lo_pf = lo["_metrics"]["pf"]; bi_pf = bi["_metrics"]["pf"]
    gate_row("OOS PF", "> 1.2",
             _fmt(lo_pf), _fmt(bi_pf),
             lo_pf > 1.2, bi_pf > 1.2)

    # OOS freq
    lo_freq = lo["_freq_per_day"]; bi_freq = bi["_freq_per_day"]
    gate_row("OOS freq", "1-3/day",
             _fmt(lo_freq, ".2f") + "/day", _fmt(bi_freq, ".2f") + "/day",
             1.0 <= lo_freq <= 3.0, 1.0 <= bi_freq <= 3.0)

    a("")
    a("## 3. Regime Bucket Details")
    a("")
    a("NOTE: Donchian is trend-following -- trend day (ADX>25) = main arena (home turf).")
    a("")
    a("| bucket | role | longonly n / net | biside n / net |")
    a("|---|---|---|---|")
    for bucket_name, role in [("trend", "main arena"), ("range", "secondary"), ("neutral", "neutral")]:
        lo_b = lo["buckets"][bucket_name]
        bi_b = bi["buckets"][bucket_name]
        a(f"| {bucket_name} | {role} | {lo_b['n']} / {lo_b['net']:.0f} | {bi_b['n']} / {bi_b['net']:.0f} |")

    a("")
    a("## 4. Observations and Recommendations")
    a("")

    # Collect gate results for narrative
    gate_results = {
        "longonly": {},
        "biside":   {},
    }
    for variant, rob in [("longonly", lo), ("biside", bi)]:
        m = rob["_metrics"]
        freq = rob["_freq_per_day"]
        pf_p5  = rob["mc_pf_p5"]
        net_p5 = rob["mc_net_p5"]
        mdd    = rob["mc_mdd_p95"]
        ws     = rob["worst_stability"]
        tn     = rob["trend_net"]
        rn     = rob["range_net"]
        rn_thr = -0.5 * abs(tn)
        pf     = m["pf"]

        passed = []
        failed = []

        def chk(name, ok):
            (passed if ok else failed).append(name)

        chk("MC_PF_p5",   (pf_p5 > 1.0)  if pf_p5 == pf_p5 else False)
        chk("MC_net_p5",  (net_p5 > 0)   if net_p5 == net_p5 else False)
        chk("MC_MDD_p95", (mdd < 12.0)   if mdd == mdd else False)
        chk("Perturb",    (ws < 0.3)     if ws == ws else False)
        chk("trend_net",  tn > 0)
        chk("range_net",  rn > rn_thr)
        chk("breakout_r", (abs(r_val) < 0.3) if r_val == r_val else False)
        chk("OOS_PF",     pf > 1.2)
        chk("OOS_freq",   1.0 <= freq <= 3.0)

        gate_results[variant]["passed"] = passed
        gate_results[variant]["failed"] = failed
        gate_results[variant]["n_pass"] = len(passed)
        gate_results[variant]["n_fail"] = len(failed)

    total_gates = 9
    lo_pass_n = gate_results["longonly"]["n_pass"]
    bi_pass_n = gate_results["biside"]["n_pass"]
    lo_fail   = gate_results["longonly"]["failed"]
    bi_fail   = gate_results["biside"]["failed"]

    obs = []
    obs.append(f"**longonly**: {lo_pass_n}/{total_gates} Gates passed | Failed: {', '.join(lo_fail) if lo_fail else '(none)'}")
    obs.append(f"**biside**: {bi_pass_n}/{total_gates} Gates passed | Failed: {', '.join(bi_fail) if bi_fail else '(none)'}")
    obs.append("")

    # Breakout correlation context
    if r_val == r_val:
        obs.append(f"Breakout correlation r = {r_val:.4f}  |r| = {abs(r_val):.4f}")
        if abs(r_val) >= 0.6:
            obs.append("**Type B**: |r| >= 0.6 -- highly correlated with BreakoutTrendStrategy.")
            obs.append("  Donchian and Breakout trade the same regime. Not additive.")
        elif abs(r_val) < 0.3:
            obs.append("**Low correlation** |r| < 0.3 -- Donchian adds diversity vs Breakout.")
        else:
            obs.append(f"Moderate correlation 0.3 <= |r| < 0.6. Partial overlap with Breakout.")
    else:
        obs.append("Breakout correlation: nan (insufficient trade overlap days).")
    obs.append("")

    # Type A / B / PASS classification
    lo_all_fail = lo_pass_n < total_gates - 1
    bi_all_fail = bi_pass_n < total_gates - 1
    r_high = (r_val == r_val) and abs(r_val) >= 0.6
    lo_pass_gates = lo_pass_n >= total_gates - 1
    bi_pass_gates = bi_pass_n >= total_gates - 1
    r_low = (r_val == r_val) and abs(r_val) < 0.3

    if lo_all_fail and bi_all_fail:
        obs.append("**Decision: Type A -- Both variants FAIL all Gates.**")
        obs.append("")
        obs.append("Donchian breakout does not achieve adequate risk-adjusted OOS performance.")
        obs.append("Recommendation: Try C1.b momentum approach (e.g., EMA crossover trend filter)")
        obs.append("  before re-testing Donchian. Key failures suggest the N-bar lookback is")
        obs.append("  too short to filter whipsaw on TMF 5m. Options:")
        obs.append("  - Increase entry_n (try 40-60 range) for more structural breaks")
        obs.append("  - Add ADX > 25 entry filter (enter only on confirmed trend days)")
        obs.append("  - Combine with volume spike filter to reduce false breakouts")
        if "OOS_freq" in lo_fail and "OOS_freq" in bi_fail:
            obs.append("  - OOS frequency < 1/day: widen entry_window_end or relax cooldown")
    elif r_high:
        obs.append("**Decision: Type B -- |r| >= 0.6 with BreakoutTrendStrategy.**")
        obs.append("")
        obs.append("Donchian correlates strongly with the live BreakoutTrendStrategy.")
        obs.append("Adding Donchian would NOT improve portfolio diversity.")
        obs.append("Recommendation: Do NOT paper trade as a separate strategy.")
        obs.append("  Instead, consider C2 -- 'wrap' Donchian inside the existing Breakout:")
        obs.append("  use Donchian channel as dynamic SL/TP for BreakoutTrendStrategy entries,")
        obs.append("  or increase BreakoutTrendStrategy position sizing on confirmed Donchian agreement.")
    elif (lo_pass_gates or bi_pass_gates) and r_low:
        obs.append("**Decision: PASS -- Gates passed + low correlation with Breakout.**")
        obs.append("")
        if lo_pass_gates and bi_pass_gates:
            obs.append("Both variants pass. Recommend longonly for paper first (lower one-way risk).")
        elif lo_pass_gates:
            obs.append(f"longonly passes ({lo_pass_n}/{total_gates}). Recommend paper trading longonly.")
            obs.append(f"biside fails ({bi_pass_n}/{total_gates}) -- hold biside until more data.")
        else:
            obs.append(f"biside passes ({bi_pass_n}/{total_gates}). Recommend paper trading biside.")
            obs.append(f"longonly fails ({lo_pass_n}/{total_gates}) -- continue monitoring.")
        obs.append("Low breakout correlation suggests genuine diversification value.")
        obs.append("Run 30-day paper with 1 contract to validate live fill assumptions.")
    else:
        # Mixed / borderline
        obs.append(f"**Decision: Mixed -- longonly {lo_pass_n}/{total_gates}, biside {bi_pass_n}/{total_gates}.**")
        obs.append("")
        obs.append("Neither variant clears all gates but results are not a clean failure.")
        obs.append("Consider targeted parameter search:")
        if "OOS_PF" in lo_fail or "OOS_PF" in bi_fail:
            obs.append("  - OOS PF < 1.2: increase entry_n (reduce false breakouts)")
        if "MC_MDD_p95" in lo_fail or "MC_MDD_p95" in bi_fail:
            obs.append("  - MDD p95 exceeded: reduce sl_atr from 2.0 to 1.5")
        if "Perturb" in lo_fail or "Perturb" in bi_fail:
            obs.append("  - Perturb unstable: strategy is overfit -- widen entry_n/exit_k range")
        obs.append("Hold -- do not paper trade until at least 8/9 gates pass.")

    obs.append("")
    obs.append(f"### Context (prior strategies on same TMF OOS)")
    obs.append(f"- vwap_fade:     OOS PF 0.797 (FAIL)")
    obs.append(f"- connors_rsi2:  OOS PF 0.764/0.611 (FAIL)")
    obs.append(f"- or_fade:       OOS PF 0.646/0.741 (FAIL)")
    obs.append(f"- BreakoutTrend: OOS PF 1.513  WR 54.5%  Sharpe 2.474 (LIVE REFERENCE)")
    obs.append(f"- Donchian is the first trend-following candidate. |r| vs Breakout is key.")

    for line in obs:
        a(line)

    a("")
    a(f"*Report generated: {datetime.now().isoformat()}*")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    _log(f"  Report written: {report_path}")
    return report_path


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Donchian P4+P5 OOS Experiments")
    parser.add_argument("--skip-grids", action="store_true",
                        help="Skip Step 1 (grid runs); use existing JSON files")
    args = parser.parse_args()

    _log("Donchian P4+P5 OOS Experiments + Gate Summary")
    _log(f"Working dir: {ROOT}")
    _log(f"Timestamp: {datetime.now().isoformat()}")

    # Step 1
    step1_run_grids(skip_grids=args.skip_grids)

    # Step 2
    bests = step2_pick_best()

    # Step 3
    oos_results = step3_oos(bests)

    # Step 4
    robustness = step4_robustness(bests, oos_results)

    # Step 5 -- mandatory, uses longonly trades for correlation
    oos_lo = oos_results.get("longonly") or list(oos_results.values())[0]
    r_val = step5_breakout_correlation(oos_lo["trades"], oos_lo["df_oos"])

    # Step 6
    report_path = step6_report(bests, oos_results, robustness, r_val)

    _log("=" * 60)
    _log("DONE")
    _log(f"Report: {report_path}")
    _log("=" * 60)

    return report_path


if __name__ == "__main__":
    main()
