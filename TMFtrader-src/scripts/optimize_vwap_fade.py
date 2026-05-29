"""
VwapFade 策略粗篩 Grid 優化器（P3）
=====================================
架構與 optimize_strategy.py 相同：
  - GPU/JIT 一次預計算指標 -> Shared Memory -> ProcessPoolExecutor 並行
  - 不同點（4 touch-points）：
    1. dict-based worker args（非 3-tuple）
    2. PARAM_GRID = k / k2 / sigma_window / adx_max / max_bars（162 combos）
    3. 日期切分（SPLIT_DATE 而非月份計數）
    4. 不寫回任何策略檔案（純 JSON 輸出）

用法：
    python scripts/optimize_vwap_fade.py --symbol MXF
    python scripts/optimize_vwap_fade.py --symbol TXF
    python scripts/optimize_vwap_fade.py --symbol MXF --dry-run
"""

# ── Windows multiprocessing 必須在 if __name__ == "__main__": 之前 ───────
import multiprocessing
multiprocessing.freeze_support()

import sys
import os
import time
import itertools
import csv
import json
import argparse
import traceback
from pathlib import Path
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed
from multiprocessing import shared_memory

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

# stdout 設定只在主進程 — 子進程可能無 reconfigure
if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


# ── 全局設定（子進程也需讀到）──────────────────────────────────────────────
INITIAL_BALANCE = 200_000.0
RISK_PROFILE    = "balanced"
SPLIT_DATE      = "2024-01-01"   # touch-point 3: date-based split

# touch-point 2: PARAM_GRID  3*3*2*3*3 = 162 combos
PARAM_GRID = {
    "k":            [1.5, 2.0, 2.5],
    "k2":           [2.5, 3.0, 3.5],
    "sigma_window": [20, 40],
    "adx_max":      [25, 30, 35],
    "max_bars":     [18, 24, 36],
}


# ── 績效計算（直接 import from reference，保持一致）──────────────────────────
from scripts.optimize_strategy import _calc_metrics   # noqa: E402  (after ROOT insert)


# ── _run_one：可供測試直接呼叫的無 shm 版本 ──────────────────────────────
def _run_one(params: dict, df, split_idx: int) -> dict:
    """
    不依賴 shared memory；測試用。
    params  : VwapFadeStrategy 的 constructor kwargs（dict）
    df      : 完整 DataFrame（datetime/open/high/low/close/volume）
    split_idx: train/test 分割 bar 索引（同 shm 版的 train_end/test_start）
    """
    from core.logger import setup_logger
    setup_logger(console_level="CRITICAL")

    from core.gpu_indicators import precompute_all
    from backtest.fast_engine import FastBacktestEngine
    from strategy.vwap_fade import VwapFadeStrategy

    indicators = precompute_all(df, verbose=False)
    n = len(df)
    train_end  = split_idx
    test_start = split_idx

    engine = FastBacktestEngine(initial_balance=INITIAL_BALANCE, instrument="TMF")

    strat_train = VwapFadeStrategy(**params)
    r_train = engine.run(df, indicators, strat_train, RISK_PROFILE,
                         start_idx=0, end_idx=train_end)
    m_train = _calc_metrics(r_train)

    strat_test = VwapFadeStrategy(**params)
    r_test = engine.run(df, indicators, strat_test, RISK_PROFILE,
                        start_idx=test_start, end_idx=n)
    m_test = _calc_metrics(r_test)

    if m_test["n"] < 5:
        wf_score = -99.0
    else:
        wf_score = (
            m_test["pf"] * 0.40
            + min(m_test["wr"] / 50.0, 2.0) * 0.25
            + max(m_test["sharpe"], -5.0) * 0.20
            + max(0.0, 1.0 - m_test["dd"] / 20.0) * 0.15
        )

    result = dict(params)
    result.update({
        "train_n":   m_train["n"],  "train_wr": m_train["wr"],
        "train_pf":  m_train["pf"], "train_ret": m_train["ret"],
        "train_dd":  m_train["dd"],
        "test_n":    m_test["n"],   "test_wr": m_test["wr"],
        "test_pf":   m_test["pf"],  "test_ret": m_test["ret"],
        "test_dd":   m_test["dd"],  "test_sharpe": m_test["sharpe"],
        "wf_score":  round(wf_score, 4),
    })
    return result


# ── Worker（子進程）──────────────────────────────────────────────────────
# touch-point 1: dict-based args
def _worker(args: tuple) -> dict:
    """
    args = (params_dict, shm_meta)
    params_dict: VwapFadeStrategy constructor kwargs
    shm_meta   : 同 optimize_strategy.py 格式
    """
    params, shm_meta = args   # touch-point 1

    try:
        sys.path.insert(0, str(ROOT))
        from core.logger import setup_logger
        setup_logger(console_level="CRITICAL")

        import numpy as np
        from multiprocessing import shared_memory as shm_mod
        from core.gpu_indicators import snapshot_from_precomputed
        from backtest.fast_engine import FastBacktestEngine
        from strategy.vwap_fade import VwapFadeStrategy

        # ── 從 shared memory 重建指標 dict（zero-copy 讀取）
        indicators = {}
        for key, (shm_name, shape, dtype) in shm_meta["shm_names"].items():
            existing = shm_mod.SharedMemory(name=shm_name)
            arr = np.ndarray(shape, dtype=dtype, buffer=existing.buf).copy()
            existing.close()
            indicators[key] = arr

        # 從 shared memory 重建 datetime series
        import pandas as pd
        dt_name, dt_shape, _ = shm_meta["dt_shm"]
        dt_shm = shm_mod.SharedMemory(name=dt_name)
        dt_ns = np.ndarray(dt_shape, dtype="int64", buffer=dt_shm.buf).copy()
        dt_shm.close()
        datetimes = pd.to_datetime(dt_ns, unit="ns")

        n = shm_meta["n"]
        df = pd.DataFrame({
            "datetime": datetimes,
            "open":     indicators["open"],
            "high":     indicators["high"],
            "low":      indicators["low"],
            "close":    indicators["close"],
            "volume":   indicators["volume"].astype(int),
        })

        train_end  = shm_meta["train_end"]
        test_start = shm_meta["test_start"]

        engine = FastBacktestEngine(initial_balance=INITIAL_BALANCE, instrument="TMF")

        # Train
        strat_train = VwapFadeStrategy(**params)
        r_train = engine.run(df, indicators, strat_train, RISK_PROFILE,
                             start_idx=0, end_idx=train_end)
        m_train = _calc_metrics(r_train)

        # Test
        strat_test = VwapFadeStrategy(**params)
        r_test = engine.run(df, indicators, strat_test, RISK_PROFILE,
                            start_idx=test_start, end_idx=n)
        m_test = _calc_metrics(r_test)

        # Walk-forward score（測試集為主）
        if m_test["n"] < 5:
            wf_score = -99.0
        else:
            wf_score = (
                m_test["pf"] * 0.40
                + min(m_test["wr"] / 50.0, 2.0) * 0.25
                + max(m_test["sharpe"], -5.0) * 0.20
                + max(0.0, 1.0 - m_test["dd"] / 20.0) * 0.15
            )

        result = dict(params)
        result.update({
            "train_n":    m_train["n"],  "train_wr":  m_train["wr"],
            "train_pf":   m_train["pf"], "train_ret": m_train["ret"],
            "train_dd":   m_train["dd"],
            "test_n":     m_test["n"],   "test_wr":  m_test["wr"],
            "test_pf":    m_test["pf"],  "test_ret": m_test["ret"],
            "test_dd":    m_test["dd"],  "test_sharpe": m_test["sharpe"],
            "wf_score":   round(wf_score, 4),
        })
        return result

    except Exception as e:
        result = dict(params)
        result["error"] = f"{type(e).__name__}: {e}"
        result["wf_score"] = -999.0
        return result


# ════════════════════════════════════════════════════════════════════════════
# 主程式
# ════════════════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="VwapFade Grid Optimizer")
    parser.add_argument("--symbol", choices=["MXF", "TXF"], default="MXF",
                        help="Symbol to optimize (MXF or TXF)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run only 4 combos on first 5000 bars (sanity check)")
    args = parser.parse_args()

    symbol   = args.symbol
    dry_run  = args.dry_run

    print("\n" + "=" * 70)
    print(f"  VwapFade Grid Optimizer  (symbol={symbol}{'  DRY-RUN' if dry_run else ''})")
    print("=" * 70)

    n_workers = min(os.cpu_count() or 4, 12)
    print(f"  CPU: {os.cpu_count()} cores, using {n_workers} workers")

    # ── load data
    print(f"\n[1/5] Loading {symbol} data...")
    DATA_PATH = ROOT / "data" / "vwap_fade" / f"{symbol}_day_5m.parquet"
    if not DATA_PATH.exists():
        print(f"  ERROR: {DATA_PATH} not found")
        sys.exit(1)

    import pandas as pd
    df = pd.read_parquet(DATA_PATH)
    df = df.sort_values("datetime").reset_index(drop=True)

    if dry_run:
        df = df.head(5000).reset_index(drop=True)
        print(f"  DRY-RUN: truncated to {len(df):,} bars")

    n = len(df)
    print(f"  OK: {n:,} bars  ({df['datetime'].iloc[0].date()} ~ {df['datetime'].iloc[-1].date()})")

    # touch-point 3: date-based split
    split_mask = df["datetime"] >= SPLIT_DATE
    if split_mask.any():
        split_idx = int(df[split_mask].index[0])
    else:
        # Dry-run on early data or all bars before split date:
        # fall back to 60/40 midpoint so test period is non-empty.
        split_idx = int(n * 0.6)
        print(f"  NOTE: all bars before {SPLIT_DATE}; using 60/40 mid-split (bar {split_idx})")
    train_end  = split_idx
    test_start = split_idx

    if train_end > 0:
        print(f"  Train: bar 0~{train_end:,}  "
              f"({df['datetime'].iloc[0].date()} ~ {df['datetime'].iloc[min(train_end-1,n-1)].date()})")
    if test_start < n:
        print(f"  Test:  bar {test_start:,}~{n:,}  "
              f"({df['datetime'].iloc[test_start].date()} ~ {df['datetime'].iloc[-1].date()})")
    else:
        print("  WARNING: no test bars (all data is before SPLIT_DATE)")

    # ── GPU precompute all indicators
    print(f"\n[2/5] GPU precompute all indicators (once, shared by all workers)...")
    from core.gpu_indicators import precompute_all
    indicators = precompute_all(df, verbose=True)

    # ── put in Shared Memory (zero-copy cross-process)
    print(f"\n[3/5] Building Shared Memory...")
    import numpy as np
    shm_list = []
    shm_names = {}
    total_mb = 0
    for key, arr in indicators.items():
        arr_c = np.ascontiguousarray(arr)
        shm = shared_memory.SharedMemory(create=True, size=arr_c.nbytes)
        shared_arr = np.ndarray(arr_c.shape, dtype=arr_c.dtype, buffer=shm.buf)
        shared_arr[:] = arr_c
        shm_list.append(shm)
        shm_names[key] = (shm.name, arr_c.shape, str(arr_c.dtype))
        total_mb += arr_c.nbytes / 1024 / 1024

    # datetime -> shared memory (int64 nanoseconds)
    dt_ns = df["datetime"].values.astype("datetime64[ns]").view("int64")
    dt_ns_c = np.ascontiguousarray(dt_ns)
    shm_dt = shared_memory.SharedMemory(create=True, size=dt_ns_c.nbytes)
    np.ndarray(dt_ns_c.shape, dtype="int64", buffer=shm_dt.buf)[:] = dt_ns_c
    shm_list.append(shm_dt)
    total_mb += dt_ns_c.nbytes / 1024 / 1024
    print(f"  Total shared memory: {total_mb:.1f} MB  ({len(shm_names)+1} arrays)")

    shm_meta = {
        "shm_names":  shm_names,
        "dt_shm":     (shm_dt.name, dt_ns_c.shape, "int64"),
        "train_end":  train_end,
        "test_start": test_start,
        "n":          n,
    }

    # ── Build combo list (touch-point 1: dict-based)
    param_keys = list(PARAM_GRID.keys())
    all_combos = list(itertools.product(*PARAM_GRID.values()))

    if dry_run:
        all_combos = all_combos[:4]
        print(f"\n  DRY-RUN: limiting to {len(all_combos)} combos")

    total = len(all_combos)
    combo_counts = " x ".join(str(len(v)) for v in PARAM_GRID.values())
    print(f"\n[4/5] Grid Search: {combo_counts} = {total} combos")
    print(f"  {n_workers} worker parallel (shared memory, no IO bottleneck)...")

    # touch-point 1: dict-based args_list
    args_list = [
        (dict(zip(param_keys, v)), shm_meta)
        for v in all_combos
    ]

    results = []
    errors  = 0
    t0 = time.time()

    try:
        with ProcessPoolExecutor(max_workers=n_workers) as executor:
            futures = {executor.submit(_worker, a): a for a in args_list}
            done = 0
            for future in as_completed(futures):
                done += 1
                future_raised = False
                try:
                    r = future.result(timeout=120)
                except Exception as e:
                    future_raised = True
                    errors += 1
                    r = {"wf_score": -999.0, "error": str(e)}
                if "error" in r:
                    if not future_raised:
                        errors += 1   # worker's own except returned error dict
                else:
                    results.append(r)
                elapsed = time.time() - t0
                eta = elapsed / done * (total - done) if done > 0 else 0
                print(f"\r  Progress: {done}/{total} ({done/total*100:.0f}%)  "
                      f"OK: {len(results)}  Err: {errors}  ETA: {eta:.0f}s   ",
                      end="", flush=True)
    finally:
        for shm in shm_list:
            try:
                shm.close()
                shm.unlink()
            except Exception:
                pass

    elapsed = time.time() - t0
    print(f"\n  Done: {elapsed:.1f}s ({elapsed/60:.1f} min), valid: {len(results)}")

    if not results:
        print("\n  ERROR: no valid results")
        return

    results.sort(key=lambda x: x["wf_score"], reverse=True)

    print("\n[5/5] Results")
    header = (f"\n  {'#':>3}  {'k':>4}  {'k2':>4}  {'sw':>3}  {'adx':>4}  {'mb':>3}  "
              f"{'Tr-N':>5}  {'Tr-PF':>6}  "
              f"{'Te-N':>5}  {'Te-WR':>6}  {'Te-PF':>6}  {'Te-DD':>6}  {'WF':>7}")
    print(header)
    print("  " + "-" * 100)
    for i, r in enumerate(results[:15], 1):
        mark = " <-- BEST" if i == 1 else ""
        print(
            f"  {i:>3}  {r['k']:>4.1f}  {r['k2']:>4.1f}  "
            f"{r['sigma_window']:>3}  {r['adx_max']:>4}  {r['max_bars']:>3}  "
            f"{r['train_n']:>5}  {r['train_pf']:>6.3f}  "
            f"{r['test_n']:>5}  {r['test_wr']:>5.1f}%  "
            f"{r['test_pf']:>6.3f}  {r['test_dd']:>5.1f}%  "
            f"{r['wf_score']:>7.4f}{mark}"
        )

    # ── Output files (touch-point 4: output to data/vwap_fade/, NO write-back to strategy)
    OUT_DIR = ROOT / "data" / "vwap_fade"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")

    csv_path = OUT_DIR / f"grid_{symbol}_{ts}.csv"
    keys = [k for k in results[0].keys() if k != "error"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    print(f"\n  Saved grid: {csv_path.name}  ({len(results)} combos)")

    best = results[0]
    json_path = OUT_DIR / f"best_params_{symbol}_{ts}.json"
    # touch-point 4: params are constructor args — just dump JSON, NO regex file patching
    best_params = {k: best[k] for k in param_keys}
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "symbol":        symbol,
            "optimized_at":  ts,
            "split_date":    SPLIT_DATE,
            "dry_run":       dry_run,
            "best_params":   best_params,
            "wf_score":      best["wf_score"],
            "metrics": {
                "train": {k: best.get(k) for k in
                          ["train_n", "train_wr", "train_pf", "train_ret", "train_dd"]},
                "test":  {k: best.get(k) for k in
                          ["test_n", "test_wr", "test_pf", "test_ret", "test_dd",
                           "test_sharpe", "wf_score"]},
            },
        }, f, indent=2, ensure_ascii=False)
    print(f"  Saved best: {json_path.name}")

    print(f"\n  BEST params (WF Score: {best['wf_score']:.4f})")
    for k in param_keys:
        print(f"     {k:15s} = {best[k]}")
    print(f"     Train: {best['train_n']} trades  WR={best['train_wr']}%  PF={best['train_pf']:.3f}  Ret={best['train_ret']:+.1f}%")
    print(f"     Test:  {best['test_n']} trades  WR={best['test_wr']}%  PF={best['test_pf']:.3f}  Ret={best['test_ret']:+.1f}%  DD={best['test_dd']:.1f}%")
    print()


if __name__ == "__main__":
    main()
