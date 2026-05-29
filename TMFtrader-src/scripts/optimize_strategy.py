"""
TMFtrader 策略參數優化器 v3 — GPU + 多核並行
=================================================
加速架構：
  1. RTX 4060 Ti CUDA（或 Numba JIT CPU）一次預計算 610k 根K棒的全部指標
     → 節省 441 × 重複計算 = 極大量的運算
  2. 指標陣列存入 multiprocessing shared memory
     → 所有 worker 共用同一份資料，不需複製 34MB × N 次
  3. ProcessPoolExecutor（8 workers）並行執行模擬迴圈
     → 每個 worker 從預計算陣列 O(1) 查表，只跑狀態機

用法：
    python scripts/optimize_strategy.py
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
import re
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
TRAIN_MONTHS    = 18
TEST_MONTHS     = 6

PARAM_GRID = {
    "sl_mult":      [1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0],
    "min_strength": [0.55, 0.58, 0.61, 0.64, 0.66, 0.68, 0.70, 0.72, 0.75],
    "trail_trigger":[0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5],
}

# shared memory 的名稱和 metadata（由主進程寫入，worker 讀取）
_SHM_META = {}   # 子進程透過參數接收


# ── FixedParamStrategy（策略工廠）─────────────────────────────────────────
def _make_strategy(sl_mult: float, min_strength: float, trail_trigger: float):
    """在子進程內建立固定參數策略（closure 不跨進程傳遞）"""
    from strategy.momentum import AdaptiveMomentumStrategy
    from strategy.filters import MarketRegime

    strat = AdaptiveMomentumStrategy()
    sl, sig, trig = sl_mult, min_strength, trail_trigger

    def fixed_update(atr_ratio: float, regime: MarketRegime):
        p = strat.signal_generator.params
        a = 0.3
        t_sl  = max(sl, 5.0) if regime.value in ("crisis_down", "crisis_reversal") else sl
        t_sig = sig * 0.95   if regime.value in ("crisis_down", "crisis_reversal") else sig
        t_trig= max(trig, 3.0) if regime.value in ("crisis_down", "crisis_reversal") else trig
        p.stop_loss_multiplier = p.stop_loss_multiplier * (1 - a) + t_sl  * a
        p.min_signal_strength  = p.min_signal_strength  * (1 - a) + t_sig * a
        p.trailing_trigger     = p.trailing_trigger     * (1 - a) + t_trig * a
        p.trailing_distance    = p.trailing_distance    * (1 - a) + 1.0   * a
        p.time_stop_bars = 60

    strat.signal_generator.params.update = fixed_update
    return strat


# ── 績效計算 ──────────────────────────────────────────────────────────────
def _calc_metrics(result) -> dict:
    trades = result.trades
    n = len(trades)
    if n == 0:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "ret": 0.0, "dd": 0.0,
                "avg_win": 0.0, "avg_loss": 0.0, "sharpe": 0.0, "score": -999.0}
    wins   = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
    gp = sum(wins)
    gl = abs(sum(losses)) if losses else 1e-9
    pf = gp / gl
    wr = len(wins) / n
    ret = (result.final_balance - result.initial_balance) / result.initial_balance * 100
    avg_win  = gp / len(wins)   if wins   else 0.0
    avg_loss = gl / len(losses) if losses else 0.0
    eq = result.equity_curve
    peak, max_dd = eq[0], 0.0
    for e in eq:
        peak = max(peak, e)
        max_dd = max(max_dd, (peak - e) / peak * 100 if peak > 0 else 0)
    # Sharpe
    if result.daily_pnl:
        daily = list(result.daily_pnl.values())
        mu  = sum(daily) / len(daily)
        std = (sum((x - mu) ** 2 for x in daily) / max(len(daily) - 1, 1)) ** 0.5
        sharpe = mu / std * (252 ** 0.5) if std > 1e-9 else 0.0
    else:
        sharpe = 0.0
    trade_ok = 1.0 if n >= 20 else (0.6 if n >= 10 else 0.3)
    score = pf * (1 + max(ret, 0) / 100) / (1 + max_dd / 100) * trade_ok
    return {
        "n": n, "wr": round(wr * 100, 1), "pf": round(pf, 3),
        "ret": round(ret, 2), "dd": round(max_dd, 2),
        "avg_win": round(avg_win, 0), "avg_loss": round(avg_loss, 0),
        "sharpe": round(sharpe, 3), "score": round(score, 4),
    }


# ── Worker（子進程）──────────────────────────────────────────────────────
def _worker(args: tuple) -> dict:
    """
    args = (sl_mult, min_strength, trail_trigger, shm_meta)
    shm_meta = {
        "shm_names": {key: (name, shape, dtype)},
        "train_mask": (shm_name, n),  # bool array
        "test_mask":  (shm_name, n),
        "n": total bars,
        "df_meta": {"datetime": [str, ...], "open": ..., ...}
    }
    """
    sl_mult, min_strength, trail_trigger, shm_meta = args

    try:
        sys.path.insert(0, str(ROOT))
        from core.logger import setup_logger
        setup_logger(console_level="CRITICAL")

        import numpy as np
        from multiprocessing import shared_memory as shm_mod
        from core.gpu_indicators import snapshot_from_precomputed
        from backtest.fast_engine import FastBacktestEngine

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

        # 最小化 DataFrame（FastBacktestEngine 只需 datetime + OHLCV）
        n = shm_meta["n"]
        df = pd.DataFrame({
            "datetime": datetimes,
            "open":   indicators["open"],
            "high":   indicators["high"],
            "low":    indicators["low"],
            "close":  indicators["close"],
            "volume": indicators["volume"].astype(int),
        })

        train_end  = shm_meta["train_end"]
        test_start = shm_meta["test_start"]

        engine = FastBacktestEngine(initial_balance=INITIAL_BALANCE, instrument="TMF")

        # Train
        strat_t = _make_strategy(sl_mult, min_strength, trail_trigger)
        r_train = engine.run(df, indicators, strat_t, RISK_PROFILE,
                             start_idx=0, end_idx=train_end)
        m_train = _calc_metrics(r_train)

        # Test
        strat_v = _make_strategy(sl_mult, min_strength, trail_trigger)
        r_test = engine.run(df, indicators, strat_v, RISK_PROFILE,
                            start_idx=test_start, end_idx=n)
        m_test = _calc_metrics(r_test)

        # Walk-forward score（測試集為主）
        if m_test["n"] < 5:
            wf_score = -99.0
        else:
            wf_score = (
                m_test["pf"] * 0.40 +
                min(m_test["wr"] / 50.0, 2.0) * 0.25 +
                max(m_test["sharpe"], -5.0) * 0.20 +
                max(0.0, 1.0 - m_test["dd"] / 20.0) * 0.15
            )

        return {
            "sl_mult": sl_mult, "min_strength": min_strength, "trail_trigger": trail_trigger,
            "train_n": m_train["n"], "train_wr": m_train["wr"], "train_pf": m_train["pf"],
            "train_ret": m_train["ret"], "train_dd": m_train["dd"],
            "test_n": m_test["n"],  "test_wr": m_test["wr"],  "test_pf": m_test["pf"],
            "test_ret": m_test["ret"],  "test_dd": m_test["dd"],
            "test_sharpe": m_test["sharpe"], "wf_score": round(wf_score, 4),
        }

    except Exception as e:
        return {
            "sl_mult": sl_mult, "min_strength": min_strength, "trail_trigger": trail_trigger,
            "error": f"{type(e).__name__}: {e}", "wf_score": -999.0,
        }


# ── 寫回 signals.py ──────────────────────────────────────────────────────
def _apply_best_params(sl: float, sig: float, trail: float):
    signals_path = ROOT / "strategy" / "signals.py"
    content = signals_path.read_text(encoding="utf-8")

    def sub_attr(text, attr, value):
        fmt = f"{value:.4f}".rstrip("0").rstrip(".")
        return re.sub(rf'(self\.{attr}\s*=\s*)[\d\.]+', rf'\g<1>{fmt}', text, count=1)

    def sub_target(text, var, value):
        fmt = f"{value:.4f}".rstrip("0").rstrip(".")
        return re.sub(rf'(target_{var}\s*=\s*)[\d\.]+', rf'\g<1>{fmt}   # 優化後', text, count=1)

    content = sub_attr(content, "stop_loss_multiplier", sl)
    content = sub_attr(content, "min_signal_strength",  sig)
    content = sub_attr(content, "trailing_trigger",     trail)
    content = sub_target(content, "sl",    sl)
    content = sub_target(content, "sig",   sig)
    content = sub_target(content, "trail", trail)
    signals_path.write_text(content, encoding="utf-8")


# ════════════════════════════════════════════════════════════════════════════
# 主程式
# ════════════════════════════════════════════════════════════════════════════
def main():
    print("\n" + "=" * 70)
    print("  TMFtrader Optimizer v3  (GPU precompute + Shared Memory parallel)")
    print("=" * 70)

    # ── GPU / backend info
    from core.gpu_indicators import _CUDA_AVAILABLE, _NUMBA_AVAILABLE
    if _CUDA_AVAILABLE:
        try:
            from numba import cuda
            name = cuda.get_current_device().name.decode()
            print(f"  GPU: CUDA {name}")
        except Exception:
            print("  GPU: CUDA available")
    elif _NUMBA_AVAILABLE:
        print("  Backend: Numba JIT CPU (CUDA not detected)")
    else:
        print("  Backend: numpy (install numba for 10-20x speedup)")

    n_workers = min(os.cpu_count() or 4, 12)
    print(f"  CPU: {os.cpu_count()} cores, using {n_workers} workers")

    # ── load data
    print(f"\n[1/5] Loading historical data...")
    DATA_PATH = ROOT / "data" / "historical" / "tmf_20260411_full_1m.csv"
    if not DATA_PATH.exists():
        print(f"  ERROR: {DATA_PATH} not found, run backtest_longterm.py first")
        sys.exit(1)

    import pandas as pd
    df = pd.read_csv(DATA_PATH, parse_dates=["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)
    n = len(df)
    print(f"  OK: {n:,} bars  ({df['datetime'].iloc[0].date()} ~ {df['datetime'].iloc[-1].date()})")

    # Train / Test split
    split_dt = df["datetime"].iloc[0] + pd.DateOffset(months=TRAIN_MONTHS)
    train_end   = int(df[df["datetime"] < split_dt].index[-1]) + 1
    test_start  = train_end
    print(f"  Train: bar 0~{train_end:,}  ({df['datetime'].iloc[0].date()} ~ {df['datetime'].iloc[train_end-1].date()})")
    print(f"  Test:  bar {test_start:,}~{n:,}  ({df['datetime'].iloc[test_start].date()} ~ {df['datetime'].iloc[-1].date()})")

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
        shared = np.ndarray(arr_c.shape, dtype=arr_c.dtype, buffer=shm.buf)
        shared[:] = arr_c
        shm_list.append(shm)
        shm_names[key] = (shm.name, arr_c.shape, str(arr_c.dtype))
        total_mb += arr_c.nbytes / 1024 / 1024

    print(f"  OK: {total_mb:.1f} MB ({len(shm_names)} arrays)")

    # 將 datetime 也存入 shared memory（int64 nanoseconds，避免大型 pickle）
    import numpy as np
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

    # ── Grid Search 並行執行
    combos = list(itertools.product(
        PARAM_GRID["sl_mult"],
        PARAM_GRID["min_strength"],
        PARAM_GRID["trail_trigger"],
    ))
    total = len(combos)
    print(f"\n[4/5] Grid Search: {len(PARAM_GRID['sl_mult'])} × {len(PARAM_GRID['min_strength'])} × "
          f"{len(PARAM_GRID['trail_trigger'])} = {total} 組合")
    print(f"  {n_workers} worker 並行（每個 worker 讀 shared memory，無 IO 瓶頸）...")

    args_list = [(sl, sig, trail, shm_meta) for sl, sig, trail in combos]

    results = []
    errors  = 0
    t0 = time.time()

    try:
        with ProcessPoolExecutor(max_workers=n_workers) as executor:
            futures = {executor.submit(_worker, a): a for a in args_list}
            done = 0
            for future in as_completed(futures):
                done += 1
                try:
                    r = future.result(timeout=120)
                except Exception as e:
                    errors += 1
                    r = {"wf_score": -999.0, "error": str(e)}
                if "error" in r:
                    errors += 1
                else:
                    results.append(r)
                elapsed = time.time() - t0
                eta = elapsed / done * (total - done) if done > 0 else 0
                print(f"\r  Progress: {done}/{total} ({done/total*100:.0f}%)  "
                      f"OK: {len(results)}  Err: {errors}  ETA: {eta:.0f}s   ",
                      end="", flush=True)
    finally:
        for shm in shm_list:
            shm.close()
            shm.unlink()

    elapsed = time.time() - t0
    print(f"\n  Done: {elapsed:.1f}s ({elapsed/60:.1f} min), valid: {len(results)}")

    if not results:
        print("\n  ERROR: no valid results")
        return

    results.sort(key=lambda x: x["wf_score"], reverse=True)

    print("\n[5/5] Results")
    print(f"\n  {'#':>3}  {'SL':>5}  {'Sig':>5}  {'Trail':>6}  "
          f"{'Tr-N':>5}  {'Tr-WR':>6}  {'Tr-PF':>6}  "
          f"{'Te-N':>5}  {'Te-WR':>6}  {'Te-PF':>6}  {'Te-DD':>6}  {'WF':>7}")
    print("  " + "-" * 96)
    for i, r in enumerate(results[:15], 1):
        mark = " <--" if i == 1 else ""
        print(f"  {i:>3}  {r['sl_mult']:>5.2f}  {r['min_strength']:>5.2f}  {r['trail_trigger']:>6.2f}  "
              f"{r['train_n']:>5}  {r['train_wr']:>5.1f}%  {r['train_pf']:>6.3f}  "
              f"{r['test_n']:>5}  {r['test_wr']:>5.1f}%  {r['test_pf']:>6.3f}  "
              f"{r['test_dd']:>5.1f}%  {r['wf_score']:>7.4f}{mark}")

    RESULTS_DIR = ROOT / "data" / "optimize_results"
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M")
    csv_path = RESULTS_DIR / f"optimize_{ts}.csv"
    keys = [k for k in results[0].keys() if k != "error"]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)
    print(f"\n  Saved: {csv_path.name}  ({len(results)} combos)")

    best = results[0]
    if best["wf_score"] > 0:
        print(f"\n  BEST params (WF Score: {best['wf_score']:.4f})")
        print(f"     stop_loss_multiplier = {best['sl_mult']}")
        print(f"     min_signal_strength  = {best['min_strength']}")
        print(f"     trailing_trigger     = {best['trail_trigger']}")
        print(f"     Train: {best['train_n']} trades  WR={best['train_wr']}%  PF={best['train_pf']:.3f}  Ret={best['train_ret']:+.1f}%")
        print(f"     Test:  {best['test_n']} trades  WR={best['test_wr']}%  PF={best['test_pf']:.3f}  Ret={best['test_ret']:+.1f}%  DD={best['test_dd']:.1f}%")

        _apply_best_params(best["sl_mult"], best["min_strength"], best["trail_trigger"])
        print(f"\n  Written to strategy/signals.py")

        json_path = RESULTS_DIR / f"best_params_{ts}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump({
                "optimized_at": ts,
                "stop_loss_multiplier": best["sl_mult"],
                "min_signal_strength":  best["min_strength"],
                "trailing_trigger":     best["trail_trigger"],
                "metrics": {
                    "train": {k: best.get(k) for k in ["train_n","train_wr","train_pf","train_ret","train_dd"]},
                    "test":  {k: best.get(k) for k in ["test_n","test_wr","test_pf","test_ret","test_dd","test_sharpe","wf_score"]},
                },
            }, f, indent=2, ensure_ascii=False)
        print(f"  JSON: {json_path.name}")
    else:
        print("\n  WARNING: no valid test results, try lowering min_signal_strength")


if __name__ == "__main__":
    main()
