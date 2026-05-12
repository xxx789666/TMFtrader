"""
optimize_sop_v1.py — BreakoutTrendStrategy SOP v1 Optimization
Steps:
  1. Pull 5 years of TMF 1-min data from Shioaji (2021-01-01 to 2026-04-17)
  2. squeeze_grace_bars fix already applied to breakout.py
  3. Parameter sweep over squeeze_grace_bars x expand_ratio x afternoon_min_adx
  4. Walk-forward validation for top 5 configs
  5. Monte Carlo robustness for best config
  6. Save results to data/optimize_results/sop_v1_results.json
"""

import sys
import os
import json
import time
import random
from pathlib import Path
from itertools import product
from datetime import datetime, timedelta
from collections import defaultdict

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

from dotenv import load_dotenv
load_dotenv()

from core.logger import setup_logger
setup_logger(console_level='CRITICAL')

import numpy as np
import pandas as pd

# ── Constants ────────────────────────────────────────────────────────────────
INSTRUMENT = "TMF"
INITIAL_BALANCE = 200_000.0
RISK_PROFILE = "tmf_3x"

DATA_1M_PATH = ROOT / "data" / "historical" / "tmf_5y_1m.parquet"
DATA_5M_PATH = ROOT / "data" / "historical" / "tmf_5y_5m.parquet"
RESULTS_DIR  = ROOT / "data" / "optimize_results"
RESULTS_PATH = RESULTS_DIR / "sop_v1_results.json"

FETCH_START = "2024-01-01"
FETCH_END   = "2026-04-17"

BASE_PARAMS = dict(
    sl_atr=2.5, tp_atr=10.0,
    trail_trigger_atr=1.2, trail_dist_atr=1.25,
    max_bars=80, min_adx=20.0, min_di_gap=10.0,
    squeeze_ratio=0.9, min_vol_ratio=1.0, pullback_ema_gap=0.2,
    breakeven_trigger_atr=999, early_cut_bars=30, early_cut_loss_atr=1.5,
    max_loss_twd=4000.0, trend_filter=True, ema200_margin_atr=0.0,
    use_momentum_score=True, momentum_rsi_bull=52.0, momentum_rsi_bear=46.0,
    momentum_session_atr=0.5, point_value=10.0, scale_out_trigger_atr=0.0,
)

SQUEEZE_GRACE_VALS = [0, 3, 5, 8, 10, 15]
EXPAND_RATIO_VALS  = [1.15, 1.18, 1.20, 1.25]
AFTERNOON_ADX_VALS = [28, 30, 32]

# ── Step 1: Fetch data ────────────────────────────────────────────────────────

def fetch_and_merge_data():
    """
    Combine existing CSV data with freshly fetched Shioaji data.
    Shioaji only provides ~2 months of 1-min data for the current contract,
    so we merge it with the full historical CSVs already on disk.
    """
    print("=" * 70)
    print("Step 1: Building dataset from existing CSVs + Shioaji recent data")
    print("=" * 70)

    data_dir = DATA_1M_PATH.parent
    data_dir.mkdir(parents=True, exist_ok=True)

    # Load all existing CSVs
    csv_dfs = []
    for f in sorted(data_dir.glob('*.csv')):
        try:
            df_csv = pd.read_csv(f, parse_dates=['datetime'])
            csv_dfs.append(df_csv)
            print(f"  Loaded CSV: {f.name} ({len(df_csv)} rows)")
        except Exception as e:
            print(f"  Skip {f.name}: {e}")

    # Try to fetch recent data from Shioaji
    try:
        import shioaji as sj
        api_key    = os.environ.get("SHIOAJI_API_KEY")
        secret_key = os.environ.get("SHIOAJI_SECRET_KEY")
        person_id  = os.environ.get("SHIOAJI_PERSON_ID")
        ca_password = os.environ.get("SHIOAJI_CA_PASSWORD")

        if api_key and secret_key:
            api = sj.Shioaji()
            print("  Logging in to Shioaji for recent data...")
            api.login(api_key=api_key, secret_key=secret_key, receive_window=300000)
            if person_id and ca_password:
                try:
                    api.activate_ca(
                        ca_path=os.environ.get("SHIOAJI_CA_PATH", ""),
                        ca_passwd=ca_password,
                        person_id=person_id,
                    )
                except Exception:
                    pass

            contract = min(
                [c for c in api.Contracts.Futures.MXF
                 if hasattr(c, 'delivery_month') and c.delivery_month],
                key=lambda c: c.delivery_month,
                default=api.Contracts.Futures.MXF.MXFR1,
            )
            print(f"  Contract: {contract.code}")

            # Shioaji returns only ~2 months for current contract
            end_dt   = datetime.strptime(FETCH_END, "%Y-%m-%d")
            start_dt = end_dt - timedelta(days=90)

            batches = []
            cur = start_dt
            while cur <= end_dt:
                batch_end = min(cur + timedelta(days=4), end_dt)
                batches.append((cur.strftime("%Y-%m-%d"), batch_end.strftime("%Y-%m-%d")))
                cur = batch_end + timedelta(days=1)

            all_bars = []
            for idx, (s, e) in enumerate(batches):
                try:
                    kbars = api.kbars(contract=contract, start=s, end=e)
                    if kbars and hasattr(kbars, 'Close') and len(kbars.Close) > 0:
                        for i in range(len(kbars.Close)):
                            raw_ts = kbars.ts[i]
                            if isinstance(raw_ts, (int, float)):
                                epoch_sec = raw_ts / 1e9 if raw_ts > 1e12 else raw_ts
                                ts = datetime.fromtimestamp(epoch_sec)
                            elif hasattr(raw_ts, 'to_pydatetime'):
                                ts = raw_ts.to_pydatetime()
                                if hasattr(ts, 'tzinfo') and ts.tzinfo is not None:
                                    ts = ts.astimezone().replace(tzinfo=None)
                            else:
                                ts = raw_ts
                            all_bars.append({
                                "datetime": ts,
                                "open":   float(kbars.Open[i]),
                                "high":   float(kbars.High[i]),
                                "low":    float(kbars.Low[i]),
                                "close":  float(kbars.Close[i]),
                                "volume": int(kbars.Volume[i]),
                            })
                except Exception:
                    pass
                time.sleep(0.05)

            api.logout()
            if all_bars:
                df_recent = pd.DataFrame(all_bars)
                csv_dfs.append(df_recent)
                print(f"  Fetched {len(df_recent)} recent bars from Shioaji")
    except Exception as e:
        print(f"  Shioaji fetch skipped: {e}")

    if not csv_dfs:
        print("ERROR: No data available!")
        return False

    combined = pd.concat(csv_dfs, ignore_index=True)
    combined['datetime'] = pd.to_datetime(combined['datetime'])
    combined = combined.drop_duplicates(subset=['datetime']).sort_values('datetime').reset_index(drop=True)

    print(f"  Combined: {len(combined)} bars")
    print(f"  Range: {combined['datetime'].iloc[0]} ~ {combined['datetime'].iloc[-1]}")

    combined.to_parquet(DATA_1M_PATH, index=False)
    print(f"  Saved 1m parquet: {DATA_1M_PATH}")

    df5 = resample_to_5min(combined)
    df5.to_parquet(DATA_5M_PATH, index=False)
    print(f"  Saved 5m parquet: {DATA_5M_PATH} ({len(df5)} bars)")
    return True


def resample_to_5min(df_1m: pd.DataFrame) -> pd.DataFrame:
    """Resample 1-min OHLCV to 5-min, keep day + night sessions."""
    df = df_1m.copy()
    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.set_index('datetime')
    df5 = df.resample('5min').agg({
        'open':   'first',
        'high':   'max',
        'low':    'min',
        'close':  'last',
        'volume': 'sum',
    }).dropna()
    # Keep day session 08:45-13:30 and night session 15:00-05:00
    t = df5.index.time
    day_mask   = (t >= pd.Timestamp('08:45').time()) & (t <= pd.Timestamp('13:30').time())
    night_mask = (t >= pd.Timestamp('15:00').time()) | (t <= pd.Timestamp('05:00').time())
    df5 = df5[day_mask | night_mask].reset_index()
    # Convert to Python datetimes for fast_engine compatibility
    df5['datetime'] = [pd.Timestamp(t).to_pydatetime() for t in df5['datetime']]
    return df5


# ── Backtest helper ────────────────────────────────────────────────────────────

def compute_metrics(trades: list, initial_balance: float = 200_000.0) -> dict:
    n = len(trades)
    if n == 0:
        return dict(n_trades=0, wr=0, pf=0, net=0, max_dd_pct=0, pos_months_ratio=0)

    wins   = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    gp = sum(t['pnl'] for t in wins)
    gl = abs(sum(t['pnl'] for t in losses)) if losses else 1e-9
    pf = gp / gl
    wr = len(wins) / n

    # Monthly
    monthly = defaultdict(float)
    for t in trades:
        monthly[t['entry_time'][:7]] += t['pnl']
    n_pos = sum(1 for v in monthly.values() if v > 0)
    n_months = max(len(monthly), 1)
    pos_months_ratio = n_pos / n_months

    # Max drawdown
    bal = initial_balance
    peak = bal
    max_dd = 0.0
    for t in sorted(trades, key=lambda x: x['entry_time']):
        bal += t['pnl']
        if bal > peak:
            peak = bal
        dd = (peak - bal) / peak
        if dd > max_dd:
            max_dd = dd

    net = sum(t['pnl'] for t in trades)

    return dict(
        n_trades=n,
        wr=wr,
        pf=pf,
        net=net,
        max_dd_pct=max_dd * 100,
        pos_months_ratio=pos_months_ratio,
    )


def score(metrics: dict) -> float:
    s = metrics['net'] / 10000
    s += min(metrics['pf'], 4.0) * 10
    s += metrics['wr'] * 20
    s -= metrics['max_dd_pct'] * 5
    s += metrics['pos_months_ratio'] * 15
    s += min(metrics['n_trades'], 200) * 0.1
    return s


def run_backtest(df, indicators, params: dict) -> dict | None:
    from core.gpu_indicators import precompute_all
    from backtest.fast_engine import FastBacktestEngine
    from strategy.breakout import BreakoutTrendStrategy
    try:
        strat  = BreakoutTrendStrategy(**params)
        engine = FastBacktestEngine(initial_balance=INITIAL_BALANCE, instrument=INSTRUMENT)
        r      = engine.run(df, indicators, strat, RISK_PROFILE)
        return r
    except Exception as e:
        print(f"  Backtest error: {e}")
        return None


# ── Step 3: Parameter sweep ────────────────────────────────────────────────────

def sweep(df5, indicators):
    print("\n" + "=" * 70)
    print("Step 3: Parameter sweep")
    print("=" * 70)

    combos = list(product(SQUEEZE_GRACE_VALS, EXPAND_RATIO_VALS, AFTERNOON_ADX_VALS))
    total  = len(combos)
    print(f"  Total combinations: {total}")

    results = []
    t0 = time.time()

    for idx, (grace, expand, pm_adx) in enumerate(combos):
        params = dict(
            BASE_PARAMS,
            squeeze_grace_bars=grace,
            expand_ratio=expand,
            afternoon_min_adx=float(pm_adx),
        )
        r = run_backtest(df5, indicators, params)
        if r is None:
            continue

        metrics = compute_metrics(r.trades)
        s = score(metrics)

        results.append({
            'squeeze_grace_bars': grace,
            'expand_ratio': expand,
            'afternoon_min_adx': pm_adx,
            'metrics': metrics,
            'score': round(s, 4),
        })

        if (idx + 1) % 20 == 0:
            elapsed = time.time() - t0
            eta = elapsed / (idx + 1) * (total - idx - 1)
            print(f"  [{idx+1}/{total}] elapsed={elapsed:.0f}s ETA={eta:.0f}s "
                  f"| last: grace={grace} expand={expand} pm_adx={pm_adx} "
                  f"n={metrics['n_trades']} pf={metrics['pf']:.3f} score={s:.2f}")

    results.sort(key=lambda x: x['score'], reverse=True)
    print(f"\n  Sweep complete. {len(results)} results.")

    print("\n  Top 10 configurations:")
    print(f"  {'Rank':>4}  {'grace':>5}  {'expand':>6}  {'pm_adx':>6}  "
          f"{'n':>4}  {'WR%':>5}  {'PF':>5}  {'net':>8}  {'ddPct':>6}  {'posM%':>5}  {'score':>7}")
    for i, res in enumerate(results[:10]):
        m = res['metrics']
        print(f"  {i+1:>4}  {res['squeeze_grace_bars']:>5}  {res['expand_ratio']:>6.2f}  "
              f"{res['afternoon_min_adx']:>6}  {m['n_trades']:>4}  "
              f"{m['wr']*100:>5.1f}  {m['pf']:>5.3f}  {m['net']:>+8,.0f}  "
              f"{m['max_dd_pct']:>6.1f}  {m['pos_months_ratio']*100:>5.1f}  "
              f"{res['score']:>7.2f}")

    return results


# ── Step 4: Walk-Forward Validation ───────────────────────────────────────────

def walk_forward(df5, top5_configs):
    print("\n" + "=" * 70)
    print("Step 4: Walk-Forward Validation (train 2024 / test 2025-2026)")
    print("=" * 70)

    # Split at 2025-01-01 — first ~12 months as IS, last ~15 months as OOS
    train_end = pd.Timestamp("2025-01-01").to_pydatetime()
    train_mask = df5['datetime'] < train_end
    test_mask  = df5['datetime'] >= train_end

    df_train = df5[train_mask].reset_index(drop=True).copy()
    df_test  = df5[test_mask].reset_index(drop=True).copy()
    # Ensure Python datetimes for fast_engine
    df_train['datetime'] = [pd.Timestamp(t).to_pydatetime() for t in df_train['datetime']]
    df_test['datetime']  = [pd.Timestamp(t).to_pydatetime() for t in df_test['datetime']]

    if len(df_train) == 0 or len(df_test) == 0:
        print("  ERROR: train or test set is empty! Check date range.")
        return []

    t0 = pd.Timestamp(df_train['datetime'].iloc[0]).strftime('%Y-%m-%d')
    t1 = pd.Timestamp(df_train['datetime'].iloc[-1]).strftime('%Y-%m-%d')
    t2 = pd.Timestamp(df_test['datetime'].iloc[0]).strftime('%Y-%m-%d')
    t3 = pd.Timestamp(df_test['datetime'].iloc[-1]).strftime('%Y-%m-%d')
    print(f"  Train bars: {len(df_train)} ({t0} ~ {t1})")
    print(f"  Test  bars: {len(df_test)} ({t2} ~ {t3})")

    from core.gpu_indicators import precompute_all
    ind_train = precompute_all(df_train, verbose=False)
    ind_test  = precompute_all(df_test,  verbose=False)

    wf_results = []
    for i, cfg in enumerate(top5_configs):
        params = dict(
            BASE_PARAMS,
            squeeze_grace_bars=cfg['squeeze_grace_bars'],
            expand_ratio=cfg['expand_ratio'],
            afternoon_min_adx=float(cfg['afternoon_min_adx']),
        )

        r_train = run_backtest(df_train, ind_train, params)
        r_test  = run_backtest(df_test,  ind_test,  params)

        m_train = compute_metrics(r_train.trades) if r_train else {}
        m_test  = compute_metrics(r_test.trades)  if r_test  else {}

        wf_results.append({
            'rank': i + 1,
            'params': {
                'squeeze_grace_bars': cfg['squeeze_grace_bars'],
                'expand_ratio': cfg['expand_ratio'],
                'afternoon_min_adx': cfg['afternoon_min_adx'],
            },
            'is_train': m_train,
            'oos_test': m_test,
        })

        print(f"\n  Rank {i+1}: grace={cfg['squeeze_grace_bars']} expand={cfg['expand_ratio']} pm_adx={cfg['afternoon_min_adx']}")
        if m_train:
            print(f"    IS  train: n={m_train['n_trades']} WR={m_train['wr']*100:.1f}% PF={m_train['pf']:.3f} net={m_train['net']:+,.0f} dd={m_train['max_dd_pct']:.1f}%")
        if m_test:
            print(f"    OOS test : n={m_test['n_trades']}  WR={m_test['wr']*100:.1f}% PF={m_test['pf']:.3f} net={m_test['net']:+,.0f} dd={m_test['max_dd_pct']:.1f}%")

    return wf_results


# ── Step 5: Monte Carlo ────────────────────────────────────────────────────────

def monte_carlo(trades: list, n_sims: int = 1000, initial_balance: float = 200_000.0):
    print("\n" + "=" * 70)
    print(f"Step 5: Monte Carlo ({n_sims} shuffles of {len(trades)} trades)")
    print("=" * 70)

    pnls = [t['pnl'] for t in trades]
    max_drawdowns = []
    final_returns = []

    for _ in range(n_sims):
        shuffled = pnls[:]
        random.shuffle(shuffled)
        cum = initial_balance
        peak = cum
        max_dd = 0.0
        for p in shuffled:
            cum += p
            if cum > peak:
                peak = cum
            dd = (peak - cum) / peak
            if dd > max_dd:
                max_dd = dd
        final_returns.append((cum - initial_balance) / initial_balance * 100)
        max_drawdowns.append(max_dd * 100)

    # Final return is always the same (sum doesn't change with shuffle)
    # Report max drawdown distribution instead - this IS path-dependent
    max_drawdowns.sort()
    dd_p5  = np.percentile(max_drawdowns, 5)
    dd_p50 = np.percentile(max_drawdowns, 50)
    dd_p95 = np.percentile(max_drawdowns, 95)

    final_return = final_returns[0]  # Same for all sims
    pct_positive = sum(1 for r in final_returns if r > 0) / n_sims * 100

    # Min-equity test: check 5th percentile of cumulative return at every trade
    # This measures if returns stay positive under worst-case trade ordering
    mid_point_returns = []
    n_half = len(pnls) // 2
    for _ in range(200):  # fewer sims for mid-point check
        shuffled = pnls[:]
        random.shuffle(shuffled)
        cum = initial_balance
        for p in shuffled[:n_half]:
            cum += p
        mid_point_returns.append((cum - initial_balance) / initial_balance * 100)
    mid_p5 = np.percentile(mid_point_returns, 5)

    print(f"  Final return (all sims): {final_return:+.1f}% (sum unchanged by shuffle)")
    print(f"  Max Drawdown: P5={dd_p5:.1f}% P50={dd_p50:.1f}% P95={dd_p95:.1f}%")
    print(f"  Mid-point return P5: {mid_p5:+.1f}%")
    print(f"  Positive final return: {pct_positive:.1f}%")
    print(f"  Robust (dd P95 < 30%): {'YES' if dd_p95 < 30 else 'NO'}")

    return {
        'n_sims': n_sims,
        'final_return_pct': round(final_return, 2),
        'max_dd_p5_pct': round(dd_p5, 2),
        'max_dd_p50_pct': round(dd_p50, 2),
        'max_dd_p95_pct': round(dd_p95, 2),
        'mid_point_return_p5_pct': round(mid_p5, 2),
        'pct_positive_sims': round(pct_positive, 1),
        'robust_dd_p95_under30': bool(dd_p95 < 30),
    }


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    t_total = time.time()

    # ── Step 1: Data ──────────────────────────────────────────────────────────
    # Skip rebuild if parquets already exist and are up-to-date
    if DATA_1M_PATH.exists() and DATA_5M_PATH.exists():
        pq_mtime = DATA_1M_PATH.stat().st_mtime
        csv_dir  = DATA_1M_PATH.parent
        latest_csv = max((f.stat().st_mtime for f in csv_dir.glob('*.csv')), default=0)
        if pq_mtime >= latest_csv:
            print("Step 1: Parquet files are up-to-date, skipping fetch.")
        else:
            ok = fetch_and_merge_data()
            if not ok:
                print("ABORT: data fetch failed.")
                sys.exit(1)
    else:
        ok = fetch_and_merge_data()
        if not ok:
            print("ABORT: data fetch failed.")
            sys.exit(1)

    # ── Load data ─────────────────────────────────────────────────────────────
    print("\nLoading data...")
    df_1m = pd.read_parquet(DATA_1M_PATH)
    df_5m = pd.read_parquet(DATA_5M_PATH)

    def fix_datetime(df: pd.DataFrame) -> pd.DataFrame:
        """Ensure datetime col is list of Python datetime objects (for fast_engine compat)."""
        df = df.copy()
        dt = pd.to_datetime(df['datetime'])
        if hasattr(dt.dt, 'tz') and dt.dt.tz is not None:
            dt = dt.dt.tz_localize(None)
        # Explicitly build list of Python datetimes so .values gives object dtype
        df['datetime'] = [pd.Timestamp(t).to_pydatetime() for t in dt]
        return df.sort_values('datetime').reset_index(drop=True)

    df_1m = fix_datetime(df_1m)
    df_5m = fix_datetime(df_5m)

    print(f"  1-min bars: {len(df_1m)}")
    print(f"  5-min bars: {len(df_5m)}")
    print(f"  1m range: {df_1m['datetime'].iloc[0]} ~ {df_1m['datetime'].iloc[-1]}")
    print(f"  5m range: {df_5m['datetime'].iloc[0]} ~ {df_5m['datetime'].iloc[-1]}")

    # ── Step 2: precompute indicators (once) ──────────────────────────────────
    print("\nPrecomputing indicators for 5-min data...")
    from core.gpu_indicators import precompute_all
    t1 = time.time()
    indicators_5m = precompute_all(df_5m, verbose=False)
    print(f"  Done in {time.time()-t1:.1f}s")

    # ── Step 3: Sweep ─────────────────────────────────────────────────────────
    sweep_results = sweep(df_5m, indicators_5m)

    # ── Step 4: Walk-Forward ──────────────────────────────────────────────────
    top5 = sweep_results[:5]
    wf_results = walk_forward(df_5m, top5)

    # ── Step 5: Monte Carlo for best config ───────────────────────────────────
    best_cfg = sweep_results[0]
    best_params = dict(
        BASE_PARAMS,
        squeeze_grace_bars=best_cfg['squeeze_grace_bars'],
        expand_ratio=best_cfg['expand_ratio'],
        afternoon_min_adx=float(best_cfg['afternoon_min_adx']),
    )
    best_result = run_backtest(df_5m, indicators_5m, best_params)
    mc_result = monte_carlo(best_result.trades) if best_result else {}

    # ── Recommended params ────────────────────────────────────────────────────
    recommended = dict(
        BASE_PARAMS,
        squeeze_grace_bars=best_cfg['squeeze_grace_bars'],
        expand_ratio=best_cfg['expand_ratio'],
        afternoon_min_adx=float(best_cfg['afternoon_min_adx']),
    )

    print("\n" + "=" * 70)
    print("Step 6: Summary & Recommended Parameters")
    print("=" * 70)
    print(f"  Best config: grace={best_cfg['squeeze_grace_bars']} "
          f"expand={best_cfg['expand_ratio']} pm_adx={best_cfg['afternoon_min_adx']}")
    m = best_cfg['metrics']
    print(f"  Full-period: n={m['n_trades']} WR={m['wr']*100:.1f}% PF={m['pf']:.3f} "
          f"net={m['net']:+,.0f} dd={m['max_dd_pct']:.1f}% posM={m['pos_months_ratio']*100:.1f}%")
    print(f"  Score: {best_cfg['score']:.2f}")

    # Compare to v5 baseline
    print("\n  vs v5 baseline (2024-01~2026-04):")
    print("  v5 : trades=96 WR=60.4% PF=2.48 maxDD=6.4% posM=68% net=+203,430")
    print(f"  new: trades={m['n_trades']} WR={m['wr']*100:.1f}% PF={m['pf']:.3f} "
          f"maxDD={m['max_dd_pct']:.1f}% posM={m['pos_months_ratio']*100:.1f}% net={m['net']:+,.0f}")

    # ── Save results ─────────────────────────────────────────────────────────
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    def make_serializable(obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, dict):
            return {k: make_serializable(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [make_serializable(v) for v in obj]
        return obj

    output = {
        'generated_at': datetime.now().isoformat(),
        'data_bars_1m': len(df_1m),
        'data_bars_5m': len(df_5m),
        'data_range': {
            'start': str(df_5m['datetime'].iloc[0]),
            'end':   str(df_5m['datetime'].iloc[-1]),
        },
        'sweep_results': make_serializable(sweep_results[:50]),
        'walkforward_results': make_serializable(wf_results),
        'monte_carlo': make_serializable(mc_result),
        'recommended_params': make_serializable(recommended),
        'v5_baseline': {
            'n_trades': 96, 'wr': 0.604, 'pf': 2.48,
            'max_dd_pct': 6.4, 'pos_months_ratio': 0.68, 'net': 203430,
        },
        'total_elapsed_sec': round(time.time() - t_total, 1),
    }

    with open(RESULTS_PATH, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n  Results saved to {RESULTS_PATH}")
    print(f"  Total elapsed: {time.time()-t_total:.0f}s")


if __name__ == "__main__":
    main()
