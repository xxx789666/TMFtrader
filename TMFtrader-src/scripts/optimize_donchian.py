"""
Donchian Breakout 策略粗篩 Grid 優化器（P3）
=============================================
架構與 optimize_or_fade.py 相同：
  - GPU/JIT 一次預計算指標 -> Shared Memory -> ProcessPoolExecutor 並行
  - 4 touch-points（vs reference or_fade）：
    1. Worker 3-tuple: (params, allow_short, shm_meta) — allow_short 是 per-run 常數
    2. PARAM_GRID + combo builder: 3*3*3*2*2 = 108 combos（兩變體相同）
    3. 日期切分 SPLIT_DATE + 輸出至 data/donchian/，含 allow_short 欄位
    4. 不寫回任何策略檔案（純 JSON 輸出，無 _apply_best_params）

用法：
    python scripts/optimize_donchian.py --symbol MXF
    python scripts/optimize_donchian.py --symbol TXF --allow-short
    python scripts/optimize_donchian.py --symbol MXF --dry-run
    python scripts/optimize_donchian.py --symbol MXF --allow-short --dry-run
"""

# ── Windows multiprocessing 必須在 if __name__ == "__main__": 之前 ───────────
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

# touch-point 2: PARAM_GRID
# 3*3*3*2*2 = 108 combos（long-only 與 biside 皆相同；allow_short 不剪 grid 維度）
PARAM_GRID = {
    "entry_n":  [10, 20, 30],
    "exit_k":   [5, 10, 15],
    "sl_atr":   [1.5, 2.0, 2.5],
    "max_bars": [24, 48],
    "cooldown": [3, 5],
}


# ── 績效計算（直接 import from reference，保持一致）──────────────────────────
from scripts.optimize_strategy import _calc_metrics   # noqa: E402  (after ROOT insert)


# touch-point 1: strategy factory
def _make_strategy(params: dict, allow_short: bool):
    from strategy.donchian import DonchianStrategy
    return DonchianStrategy(allow_short=allow_short, **params)


def _build_param_combos(allow_short: bool) -> list:
    """
    touch-point 2: Donchian 的 allow_short 不剪任何 param 維度。
    allow_short=False -> long-only（108 combos）
    allow_short=True  -> biside（108 combos）
    兩變體相同 108 combos；allow_short=True 只是開放空方觸邊評估。
    """
    keys = list(PARAM_GRID.keys())
    return [dict(zip(keys, v)) for v in itertools.product(*PARAM_GRID.values())]


# ── _run_one：可供測試直接呼叫的無 shm 版本（Task 7 downstream 也使用）────────
def _run_one(params: dict, df, split_idx: int, allow_short: bool = False) -> dict:
    """
    Same as or_fade pattern + sample-size penalty for test_n < 100.
    params     : DonchianStrategy constructor kwargs（dict，不含 allow_short）
    df         : 完整 DataFrame（datetime/open/high/low/close/volume）
    split_idx  : train/test 分割 bar 索引
    allow_short: 對應策略 ctor kwarg
    """
    from core.gpu_indicators import precompute_all
    from backtest.fast_engine import FastBacktestEngine
    from strategy.donchian import DonchianStrategy
    from scripts.optimize_strategy import _calc_metrics

    ind = precompute_all(df, verbose=False)
    eng = FastBacktestEngine(initial_balance=200_000, instrument="TMF")

    strat_t = DonchianStrategy(allow_short=allow_short, **params)
    r_train = eng.run(df, ind, strat_t, "balanced", start_idx=0, end_idx=split_idx)
    m_train = _calc_metrics(r_train)

    if split_idx < len(df) - 10:
        strat_v = DonchianStrategy(allow_short=allow_short, **params)
        r_test = eng.run(df, ind, strat_v, "balanced", start_idx=split_idx, end_idx=len(df))
        m_test = _calc_metrics(r_test)
    else:
        m_test = {"n": 0, "wr": 0.0, "pf": 0.0, "ret": 0.0, "dd": 0.0, "sharpe": 0.0}

    # Sample-size penalty: avoid N=30 ranking via 9-bar-day fluke
    if m_test["n"] < 5:
        wf = -99.0
    elif m_test["n"] < 100:
        wf = -50.0   # 重 penalty、實際排不進 Top-10
    else:
        wf = (m_test["pf"] * 0.40
              + min(m_test["wr"] / 50.0, 2.0) * 0.25
              + max(m_test["sharpe"], -5.0) * 0.20
              + max(0.0, 1.0 - m_test["dd"] / 20.0) * 0.15)

    return {
        **{f"train_{k}": v for k, v in m_train.items()},
        **{f"test_{k}": v for k, v in m_test.items()},
        "wf_score": round(wf, 4), **params, "allow_short": allow_short,
    }


# ── Worker（子進程）──────────────────────────────────────────────────────────
# touch-point 1: 3-tuple args (params, allow_short, shm_meta)
def _worker(args: tuple) -> dict:
    """
    args = (params_dict, allow_short, shm_meta)
    params_dict: DonchianStrategy constructor kwargs (NOT including allow_short)
    allow_short: per-run constant (bool) — NOT a grid param
    shm_meta   : 同 optimize_strategy.py 格式
    """
    params, allow_short, shm_meta = args   # touch-point 1

    try:
        sys.path.insert(0, str(ROOT))
        from core.logger import setup_logger
        setup_logger(console_level="CRITICAL")

        import numpy as np
        from multiprocessing import shared_memory as shm_mod
        from backtest.fast_engine import FastBacktestEngine
        from strategy.donchian import DonchianStrategy   # touch-point 1
        from scripts.optimize_strategy import _calc_metrics

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

        # Train — touch-point 1: allow_short passed to ctor
        strat_train = DonchianStrategy(allow_short=allow_short, **params)
        r_train = engine.run(df, indicators, strat_train, RISK_PROFILE,
                             start_idx=0, end_idx=train_end)
        m_train = _calc_metrics(r_train)

        # Test
        strat_test = DonchianStrategy(allow_short=allow_short, **params)
        r_test = engine.run(df, indicators, strat_test, RISK_PROFILE,
                            start_idx=test_start, end_idx=n)
        m_test = _calc_metrics(r_test)

        # Walk-forward score（測試集為主）+ sample-size penalty
        if m_test["n"] < 5:
            wf_score = -99.0
        elif m_test["n"] < 100:
            wf_score = -50.0   # 重 penalty、實際排不進 Top-10
        else:
            wf_score = (
                m_test["pf"] * 0.40
                + min(m_test["wr"] / 50.0, 2.0) * 0.25
                + max(m_test["sharpe"], -5.0) * 0.20
                + max(0.0, 1.0 - m_test["dd"] / 20.0) * 0.15
            )

        result = dict(params)
        result["allow_short"] = allow_short   # touch-point 3: include in output
        result.update({
            "train_n":    m_train["n"],  "train_wr":  m_train["wr"],
            "train_pf":   m_train["pf"], "train_ret": m_train["ret"],
            "train_dd":   m_train["dd"],
            "test_n":     m_test["n"],   "test_wr":   m_test["wr"],
            "test_pf":    m_test["pf"],  "test_ret":  m_test["ret"],
            "test_dd":    m_test["dd"],  "test_sharpe": m_test["sharpe"],
            "wf_score":   round(wf_score, 4),
        })
        return result

    except Exception as e:
        result = dict(params)
        result["allow_short"] = allow_short
        result["error"] = f"{type(e).__name__}: {e}"
        result["wf_score"] = -999.0
        return result


# ════════════════════════════════════════════════════════════════════════════
# 主程式
# ════════════════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(description="Donchian Grid Optimizer (P3)")
    parser.add_argument("--symbol", choices=["MXF", "TXF"], required=True,
                        help="Symbol to optimize (MXF or TXF)")
    parser.add_argument("--allow-short", action="store_true",
                        help="若給 -> 跑雙向變體（biside）；否則 long-only（皆 108 combos）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只跑 4 combos / 前 5000 bars，pipeline smoke test")
    args = parser.parse_args()

    symbol      = args.symbol
    allow_short = args.allow_short
    dry_run     = args.dry_run

    # touch-point 3: variant label for output filenames
    variant = "biside" if allow_short else "longonly"

    print("\n" + "=" * 70)
    print(f"  Donchian Grid Optimizer  "
          f"(symbol={symbol}  variant={variant}{'  DRY-RUN' if dry_run else ''})")
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

    # ── Build combo list (touch-point 2: both variants get full 108 combos)
    all_combos = _build_param_combos(allow_short)

    if dry_run:
        all_combos = all_combos[:4]
        print(f"\n  DRY-RUN: limiting to {len(all_combos)} combos")

    total = len(all_combos)
    combo_counts = " x ".join(str(len(v)) for v in PARAM_GRID.values())
    print(f"\n[4/5] Grid Search ({variant}): {combo_counts} = {total} combos")
    print(f"  {n_workers} worker parallel (shared memory, no IO bottleneck)...")

    # touch-point 1: 3-tuple args_list (params, allow_short, shm_meta)
    args_list = [
        (p, allow_short, shm_meta)
        for p in all_combos
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
    # Grid-aware column header
    header = (f"\n  {'#':>3}  {'en':>3}  {'ek':>3}  {'sl':>4}  "
              f"{'mb':>3}  {'cd':>3}  "
              f"{'Tr-N':>5}  {'Tr-PF':>6}  "
              f"{'Te-N':>5}  {'Te-WR':>6}  {'Te-PF':>6}  {'Te-DD':>6}  {'WF':>7}")
    print(header)
    print("  " + "-" * 100)

    for i, r in enumerate(results[:15], 1):
        mark = " <-- BEST" if i == 1 else ""
        print(
            f"  {i:>3}  {r.get('entry_n', 0):>3}  {r.get('exit_k', 0):>3}  "
            f"{r.get('sl_atr', 0):>4.1f}  "
            f"{r.get('max_bars', 0):>3}  {r.get('cooldown', 0):>3}  "
            f"{r['train_n']:>5}  {r['train_pf']:>6.3f}  "
            f"{r['test_n']:>5}  {r['test_wr']:>5.1f}%  "
            f"{r['test_pf']:>6.3f}  {r['test_dd']:>5.1f}%  "
            f"{r['wf_score']:>7.4f}{mark}"
        )

    # ── Output files (touch-point 3: data/donchian/, variant in name, allow_short in JSON)
    OUT_DIR = ROOT / "data" / "donchian"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")

    csv_path = OUT_DIR / f"grid_donchian_{symbol}_{variant}_{ts}.csv"
    keys = [k for k in results[0].keys() if k != "error"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    print(f"\n  Saved grid: {csv_path.name}  ({len(results)} combos)")

    best = results[0]
    json_path = OUT_DIR / f"best_params_donchian_{symbol}_{variant}_{ts}.json"

    # touch-point 3: best_params are ctor args (no _apply_best_params patch)
    # touch-point 4: include allow_short in JSON so Task 7 can reconstruct strategy
    param_keys = [k for k in best.keys()
                  if k not in ("error", "allow_short",
                               "train_n", "train_wr", "train_pf", "train_ret", "train_dd",
                               "test_n",  "test_wr",  "test_pf",  "test_ret",  "test_dd",
                               "test_sharpe", "wf_score")]
    best_params = {k: best[k] for k in param_keys if k in best}
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "symbol":        symbol,
            "variant":       variant,
            "allow_short":   allow_short,   # touch-point 3: downstream reconstruction
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

    print(f"\n  BEST params (variant={variant}  WF Score: {best['wf_score']:.4f})")
    for k in param_keys:
        print(f"     {k:15s} = {best[k]}")
    print(f"     allow_short    = {allow_short}")
    print(f"     Train: {best['train_n']} trades  WR={best['train_wr']}%  "
          f"PF={best['train_pf']:.3f}  Ret={best['train_ret']:+.1f}%")
    print(f"     Test:  {best['test_n']} trades  WR={best['test_wr']}%  "
          f"PF={best['test_pf']:.3f}  Ret={best['test_ret']:+.1f}%  DD={best['test_dd']:.1f}%")
    print()


if __name__ == "__main__":
    main()
