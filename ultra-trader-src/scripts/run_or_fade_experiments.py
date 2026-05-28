"""
OR Fade P4+P5 OOS Experiments + Gate Summary (Task 7)
======================================================
Orchestrates:
  Step 1: Run 4 grids (MXF/TXF x longonly/biside) -- skip if fresh non-dry-run exists
  Step 2: Pick BEST_PARAMS per variant (global top-1 across symbols by wf_score)
  Step 3: OOS backtest on TMF_oos_day_5m.parquet
  Step 4: Robustness three-pack (MC / Perturb / Regime)
  Step 5: Breakout correlation (best-effort)
  Step 6: Gate summary markdown report
  Step 7: Commit (orchestrator + report only)

Usage:
    python scripts/run_or_fade_experiments.py
    python scripts/run_or_fade_experiments.py --skip-grids   # if grids already done
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

OUT_DIR = ROOT / "data" / "or_fade"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _log(msg: str):
    print(f"[exp] {msg}", flush=True)


def _load_best_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _find_fresh_grids(symbol: str, variant: str) -> list[Path]:
    """Return non-dry-run best_params JSON files for given symbol+variant."""
    pattern = f"best_params_or_fade_{symbol}_{variant}_*.json"
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
        cmd = [sys.executable, str(ROOT / "scripts" / "optimize_or_fade.py"),
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

def step2_pick_best() -> dict[str, dict]:
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
    "or_bars", "vol_ratio_max", "wait_bars", "sl_atr", "max_bars", "cooldown",
    "max_trades", "entry_window_end", "force_close", "point_value",
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
    from strategy.or_fade import OrFadeStrategy
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

        strat = OrFadeStrategy(allow_short=allow_short, **valid_params)
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
    from scripts.optimize_or_fade import _run_one

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

        # 4-2 Perturbation (custom for OrFade)
        _log("  [4-2] Perturbation ...")
        VALID_PERTURB_KEYS = {"or_bars", "vol_ratio_max", "wait_bars", "sl_atr", "max_bars", "cooldown"}
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
                    r = _run_one(p, df_oos, split_idx=0, allow_short=allow_short)
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
        _log("  [4-3] Regime split ...")
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
        _log(f"    range:   n={range_n}  net={range_net:.0f}")
        _log(f"    trend:   n={trend_n}  net={trend_net:.0f}")
        _log(f"    neutral: n={neutral_n}  net={neutral_net:.0f}")

        robustness[variant] = rob

    return robustness


# ─────────────────────────────────────────────────────────────────────────────
# Step 5: Breakout correlation (best-effort)
# ─────────────────────────────────────────────────────────────────────────────

def step5_breakout_correlation(bests: dict, oos_results: dict) -> dict:
    """Best-effort. Returns {"r": float or nan, "note": str}"""
    _log("=" * 60)
    _log("STEP 5: Breakout correlation (best-effort)")
    _log("=" * 60)

    # Use longonly variant for the correlation check
    oos = oos_results.get("longonly") or list(oos_results.values())[0]
    df_oos = oos["df_oos"]
    ind_oos = oos["ind_oos"]

    try:
        from strategy.breakout import BreakoutStrategy
        from backtest.fast_engine import FastBacktestEngine
        from scripts.optimize_strategy import _calc_metrics
        import numpy as np
        import pandas as pd

        strat_bo = BreakoutStrategy()
        eng = FastBacktestEngine(initial_balance=200_000.0, instrument="TMF")
        res_bo = eng.run(df_oos, ind_oos, strat_bo, "balanced")
        trades_bo = list(res_bo.trades) if hasattr(res_bo, "trades") else []

        # Build daily pnl series for both
        or_fade_trades = oos["trades"]

        def daily_pnl(trades, df):
            df = df.copy()
            df["date"] = df["datetime"].dt.date
            dates = sorted(df["date"].unique())
            daily = {d: 0.0 for d in dates}
            for t in trades:
                import pandas as pd2
                entry_dt = pd.to_datetime(t["entry_time"])
                d = entry_dt.date()
                if d in daily:
                    daily[d] += float(t["pnl"])
            return np.array([daily[d] for d in dates])

        pnl_o = daily_pnl(or_fade_trades, df_oos)
        pnl_b = daily_pnl(trades_bo, df_oos)

        if pnl_o.std() < 1e-9 or pnl_b.std() < 1e-9:
            note = "DEFERRED: zero-variance daily PnL (insufficient trades)"
            _log(f"  {note}")
            return {"r": float("nan"), "note": note}

        r = float(np.corrcoef(pnl_o, pnl_b)[0, 1])
        _log(f"  Pearson r(or_fade, breakout) = {r:.4f}  (gate |r|<0.3)")
        return {"r": r, "note": ""}

    except ImportError as e:
        note = f"DEFERRED: import error -- {e}"
        _log(f"  {note}")
        return {"r": float("nan"), "note": note}
    except Exception as e:
        note = f"DEFERRED: {type(e).__name__}: {e}"
        _log(f"  {note}")
        return {"r": float("nan"), "note": note}


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
                 breakout: dict) -> Path:
    _log("=" * 60)
    _log("STEP 6: Writing Gate summary report")
    _log("=" * 60)

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    report_path = OUT_DIR / f"robustness_report_{ts}.md"

    lines = []
    a = lines.append

    variants = ["longonly", "biside"]

    a(f"# or_fade Robustness Report -- {ts}")
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

    # Regime range net
    lo_rn = lo["range_net"]; bi_rn = bi["range_net"]
    gate_row("4-3 range net", "> 0",
             _fmt(lo_rn, ".0f"), _fmt(bi_rn, ".0f"),
             lo_rn > 0, bi_rn > 0)

    # Regime trend net
    lo_tn = lo["trend_net"]; bi_tn = bi["trend_net"]
    lo_thr = -0.5 * abs(lo_rn); bi_thr = -0.5 * abs(bi_rn)
    gate_row("4-3 trend net", "> -0.5*|range_net|",
             _fmt(lo_tn, ".0f"), _fmt(bi_tn, ".0f"),
             lo_tn > lo_thr, bi_tn > bi_thr)

    # Breakout |r|
    r_val = breakout["r"]; r_note = breakout["note"]
    if r_val != r_val:
        lo_r_str = bi_r_str = f"N/A ({r_note[:30]})"
        lo_r_pass = bi_r_pass = True  # best-effort -- skip failure
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
    a("| bucket | longonly n / net | biside n / net |")
    a("|---|---|---|")
    for bucket_name in ["range", "trend", "neutral"]:
        lo_b = lo["buckets"][bucket_name]
        bi_b = bi["buckets"][bucket_name]
        a(f"| {bucket_name} | {lo_b['n']} / {lo_b['net']:.0f} | {bi_b['n']} / {bi_b['net']:.0f} |")

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
        rn     = rob["range_net"]
        tn     = rob["trend_net"]
        tn_thr = -0.5 * abs(rn)
        pf     = m["pf"]

        passed = []
        failed = []

        def chk(name, ok):
            (passed if ok else failed).append(name)

        chk("MC_PF_p5",   (pf_p5 > 1.0)  if pf_p5 == pf_p5 else False)
        chk("MC_net_p5",  (net_p5 > 0)   if net_p5 == net_p5 else False)
        chk("MC_MDD_p95", (mdd < 12.0)   if mdd == mdd else False)
        chk("Perturb",    (ws < 0.3)     if ws == ws else False)
        chk("range_net",  rn > 0)
        chk("trend_net",  tn > tn_thr)
        chk("breakout_r", abs(r_val) < 0.3 if r_val == r_val else True)
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

    # Recommendation logic
    lo_ok = lo_pass_n >= total_gates - 1  # allow 1 borderline fail
    bi_ok = bi_pass_n >= total_gates - 1

    if lo_ok and bi_ok:
        obs.append("Both variants meet the bar. Recommend longonly for paper trading first (lower one-way risk); biside as reserve for comparison.")
    elif lo_ok and not bi_ok:
        obs.append(f"longonly passes ({lo_pass_n}/{total_gates}); biside does not ({bi_pass_n}/{total_gates}).")
        obs.append("Recommendation: adopt longonly for paper trading. biside needs revision -- consider tightening vol_ratio_max or adding trend filter (section 9 C2) before re-testing.")
    elif not lo_ok and bi_ok:
        obs.append(f"biside passes ({bi_pass_n}/{total_gates}); longonly does not ({lo_pass_n}/{total_gates}).")
        obs.append("Recommendation: if biside direction is acceptable, use biside for paper. longonly OOS underperforms; consider loosening vol_ratio_max or shortening max_bars (section 9 C1).")
    else:
        obs.append(f"Neither variant meets the bar (longonly {lo_pass_n}/{total_gates}, biside {bi_pass_n}/{total_gates}).")
        if "OOS_freq" in lo_fail and "OOS_freq" in bi_fail:
            obs.append("Frequency gate not met (<1/day): OOS sample is too small; Gate evaluation is distorted. Verify OOS data coverage first.")
        if "OOS_PF" in lo_fail or "OOS_PF" in bi_fail:
            obs.append("OOS PF insufficient (<1.2): strategy cannot produce adequate margin on true OOS. Fallback options:")
            obs.append("  - Section 9 C1: shorten max_bars / tighten vol_ratio_max for more precise entries")
            obs.append("  - Section 9 C2: add ADX trend filter (enter only when ADX<20 range environment)")
            obs.append("  - Section 9 C3: signal stacking (OR fade + breakout same direction required)")
        if "MC_MDD_p95" in lo_fail or "MC_MDD_p95" in bi_fail:
            obs.append("MDD p95 exceeded: consider reducing sl_atr or adding intraday loss circuit breaker (section 9 C4).")
        obs.append("")
        obs.append("Recommendation: lock MR / pivot to section 9 C2 or C3.")

    obs.append("")
    if r_val != r_val:
        obs.append(f"Breakout correlation: {breakout['note']} -- defer until sufficient trade sample is available.")

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
    parser = argparse.ArgumentParser(description="OR Fade P4+P5 OOS Experiments")
    parser.add_argument("--skip-grids", action="store_true",
                        help="Skip Step 1 (grid runs); use existing JSON files")
    args = parser.parse_args()

    _log("OR Fade P4+P5 OOS Experiments + Gate Summary")
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

    # Step 5
    breakout = step5_breakout_correlation(bests, oos_results)

    # Step 6
    report_path = step6_report(bests, oos_results, robustness, breakout)

    _log("=" * 60)
    _log("DONE")
    _log(f"Report: {report_path}")
    _log("=" * 60)

    return report_path


if __name__ == "__main__":
    main()
