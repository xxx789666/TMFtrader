"""
BreakoutTrendStrategy 參數優化器
目標：找到在 2024~2026 每月獲利 6% 的參數組合

搜索策略：
  Phase 1 — 出場參數網格搜索 (sl_atr / tp_atr / trail_trigger / trail_dist / max_bars)
  Phase 2 — 進場訊號參數搜索 (min_adx / min_di_gap / squeeze_ratio / expand_ratio)
  Phase 3 — 聯合最佳組合

用法：python scripts/optimize_breakout.py [--max-rounds 5]
"""

import multiprocessing
multiprocessing.freeze_support()

import sys, os, time, json, itertools, random, argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import multiprocessing.pool
from multiprocessing import shared_memory

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ── 全局設定 ─────────────────────────────────────────────────────────────────
INITIAL_BALANCE = 200_000.0
RISK_PROFILE    = "balanced"
MONTHLY_TARGET  = 0.06
TARGET_MONTHS   = 24
MAX_ROUNDS      = 5
N_WORKERS       = min(os.cpu_count() or 4, 4)

RESULTS_DIR = ROOT / "data" / "optimize_results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Phase 1 — 出場參數網格
P1_GRID = {
    "sl_atr":          [0.75, 1.0, 1.5, 2.0, 2.5, 3.0],
    "tp_atr":          [3.0, 4.0, 5.0, 7.0, 10.0, 15.0],
    "trail_trigger":   [1.0, 1.5, 2.0, 3.0],
    "trail_dist":      [0.75, 1.0, 1.25, 1.5, 2.0],
    "max_bars":        [30, 60, 90, 120],
}

# Phase 2 — 進場訊號參數網格
P2_GRID = {
    "min_adx":        [12.0, 15.0, 18.0, 22.0, 25.0],
    "min_di_gap":     [3.0, 5.0, 7.0, 10.0],
    "squeeze_ratio":  [0.70, 0.75, 0.78, 0.82],
    "expand_ratio":   [1.05, 1.08, 1.12, 1.15],
    "min_vol_ratio":  [1.0, 1.05, 1.1, 1.15],
    "pullback_gap":   [0.2, 0.3, 0.4, 0.5],
}

P1_SAMPLE_N = 600
P2_SAMPLE_N = 150

ALL_MONTHS = [
    f"{y}-{m:02d}" for y in range(2024, 2027)
    for m in range(1, 13)
    if not (y == 2026 and m > 4)
]


# ── 評分計算 ─────────────────────────────────────────────────────────────────
def calc_monthly(result) -> dict:
    if not result.daily_pnl:
        return {"score": 0.0, "n_target_months": 0, "avg_monthly_ret": 0.0,
                "min_monthly_ret": 0.0, "monthly_ret": {ym: 0.0 for ym in ALL_MONTHS},
                "monthly_pnl": {}, "green_months": 0, "max_monthly_ret": 0.0}

    monthly_pnl = defaultdict(float)
    for day, pnl in result.daily_pnl.items():
        ym = day[:7]
        monthly_pnl[ym] += pnl

    balance = result.initial_balance
    monthly_ret = {}
    for ym in ALL_MONTHS:
        pnl = monthly_pnl.get(ym, 0.0)
        ret = pnl / balance if balance > 0 else 0.0
        monthly_ret[ym] = ret
        balance += pnl

    rets = [monthly_ret[ym] for ym in ALL_MONTHS]
    n_trading_months = sum(1 for ym in ALL_MONTHS if monthly_pnl.get(ym, 0) != 0)

    n_target = sum(1 for r in rets if r >= MONTHLY_TARGET)
    n_green  = sum(1 for r in rets if r > 0)
    avg_ret  = sum(rets) / len(rets)
    min_ret  = min(rets)
    max_ret  = max(rets)

    trade_penalty = max(0, (12 - n_trading_months) * 0.5)
    score = (n_target / TARGET_MONTHS * 60) + (min(avg_ret / MONTHLY_TARGET, 1.0) * 30) + (n_green / TARGET_MONTHS * 10) - trade_penalty

    return {
        "score":           round(score, 4),
        "n_target_months": n_target,
        "green_months":    n_green,
        "avg_monthly_ret": round(avg_ret, 4),
        "min_monthly_ret": round(min_ret, 4),
        "max_monthly_ret": round(max_ret, 4),
        "monthly_ret":     {k: round(v, 4) for k, v in monthly_ret.items()},
        "monthly_pnl":     {k: round(v, 0) for k, v in monthly_pnl.items()},
    }


def calc_trade_metrics(result) -> dict:
    trades = result.trades
    n = len(trades)
    if n == 0:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "ret": 0.0}
    wins   = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
    gp = sum(wins)
    gl = abs(sum(losses)) if losses else 1e-9
    pf = round(gp / gl, 3)
    wr = round(len(wins) / n * 100, 1)
    ret = round((result.final_balance - result.initial_balance) / result.initial_balance * 100, 2)
    return {"n": n, "wr": wr, "pf": pf, "ret": ret}


# ── Worker 函數 ──────────────────────────────────────────────────────────────
def _worker(args: tuple) -> dict:
    (sl_atr, tp_atr, trail_trigger, trail_dist, max_bars,
     min_adx, min_di_gap, squeeze_ratio, expand_ratio,
     min_vol_ratio, pullback_gap, shm_meta) = args

    try:
        sys.path.insert(0, str(ROOT))
        from core.logger import setup_logger
        setup_logger(console_level="CRITICAL")

        import numpy as np
        from multiprocessing import shared_memory as shm_mod
        import pandas as pd
        from backtest.fast_engine import FastBacktestEngine
        from strategy.breakout import BreakoutTrendStrategy

        # 從 shared memory 重建指標（不複製）
        shm_handles = []
        indicators = {}
        for key, (shm_name, shape, dtype) in shm_meta["shm_names"].items():
            ex = shm_mod.SharedMemory(name=shm_name)
            shm_handles.append(ex)
            indicators[key] = np.ndarray(shape, dtype=dtype, buffer=ex.buf)

        dt_name, dt_shape, _ = shm_meta["dt_shm"]
        dt_shm = shm_mod.SharedMemory(name=dt_name)
        shm_handles.append(dt_shm)
        dt_ns = np.ndarray(dt_shape, dtype="int64", buffer=dt_shm.buf)

        df = pd.DataFrame({
            "datetime": pd.to_datetime(dt_ns, unit="ns"),
            "open":   indicators["open"],
            "high":   indicators["high"],
            "low":    indicators["low"],
            "close":  indicators["close"],
            "volume": indicators["volume"].astype(int),
        })

        strat = BreakoutTrendStrategy(
            sl_atr=sl_atr,
            tp_atr=tp_atr,
            trail_trigger_atr=trail_trigger,
            trail_dist_atr=trail_dist,
            max_bars=max_bars,
            min_adx=min_adx,
            min_di_gap=min_di_gap,
            squeeze_ratio=squeeze_ratio,
            expand_ratio=expand_ratio,
            min_vol_ratio=min_vol_ratio,
            pullback_ema_gap=pullback_gap,
        )

        engine = FastBacktestEngine(initial_balance=INITIAL_BALANCE, instrument="TMF")
        result = engine.run(df, indicators, strat, RISK_PROFILE)

        for h in shm_handles:
            h.close()

        m  = calc_monthly(result)
        tm = calc_trade_metrics(result)

        return {
            "sl_atr": sl_atr, "tp_atr": tp_atr,
            "trail_trigger": trail_trigger, "trail_dist": trail_dist,
            "max_bars": max_bars, "min_adx": min_adx,
            "min_di_gap": min_di_gap, "squeeze_ratio": squeeze_ratio,
            "expand_ratio": expand_ratio, "min_vol_ratio": min_vol_ratio,
            "pullback_gap": pullback_gap,
            "score":           m["score"],
            "n_target_months": m["n_target_months"],
            "green_months":    m["green_months"],
            "avg_monthly_ret": m["avg_monthly_ret"],
            "min_monthly_ret": m["min_monthly_ret"],
            "max_monthly_ret": m["max_monthly_ret"],
            "monthly_ret":     m["monthly_ret"],
            "monthly_pnl":     m.get("monthly_pnl", {}),
            "n_trades": tm["n"], "wr": tm["wr"], "pf": tm["pf"], "ret": tm["ret"],
            "final_balance": result.final_balance,
        }

    except Exception as e:
        import traceback
        return {
            "sl_atr": sl_atr, "tp_atr": tp_atr,
            "trail_trigger": trail_trigger, "trail_dist": trail_dist,
            "max_bars": max_bars, "min_adx": min_adx,
            "min_di_gap": min_di_gap, "squeeze_ratio": squeeze_ratio,
            "expand_ratio": expand_ratio, "min_vol_ratio": min_vol_ratio,
            "pullback_gap": pullback_gap,
            "error": f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
            "score": -999.0, "n_target_months": 0,
        }


def _run_parallel(args_list, desc=""):
    results = []
    errors  = 0
    total   = len(args_list)
    t0 = time.time()
    with multiprocessing.pool.Pool(processes=N_WORKERS, maxtasksperchild=20) as pool:
        done = 0
        for r in pool.imap_unordered(_worker, args_list):
            done += 1
            if "error" in r:
                errors += 1
            else:
                results.append(r)
            elapsed = time.time() - t0
            eta = elapsed / done * (total - done) if done > 0 else 0
            print(f"\r  {desc} {done}/{total} ({done/total*100:.0f}%) "
                  f"OK:{len(results)} Err:{errors} ETA:{eta:.0f}s  ",
                  end="", flush=True)
    print()
    return sorted(results, key=lambda x: x["score"], reverse=True)


# ── 報告 ─────────────────────────────────────────────────────────────────────
def print_report(best: dict, label: str):
    print(f"\n  ===== {label}  (Score: {best['score']:.2f}) =====")
    print(f"  Exit:  sl_atr={best['sl_atr']}  tp_atr={best['tp_atr']}  "
          f"trail={best['trail_trigger']}  dist={best['trail_dist']}  bars={best['max_bars']}")
    print(f"  Entry: adx={best['min_adx']}  di_gap={best['min_di_gap']}  "
          f"sq={best['squeeze_ratio']}  ex={best['expand_ratio']}  "
          f"vol={best['min_vol_ratio']}  pb={best['pullback_gap']}")
    print(f"  Trades:{best['n_trades']}  WR:{best['wr']}%  PF:{best['pf']}  "
          f"Ret:{best['ret']:+.1f}%  Final:{best['final_balance']:,.0f}")
    print(f"  Target(6%): {best['n_target_months']}/24  "
          f"Green: {best['green_months']}/24  "
          f"Avg:{best['avg_monthly_ret']*100:+.1f}%  "
          f"Min:{best['min_monthly_ret']*100:+.1f}%  "
          f"Max:{best['max_monthly_ret']*100:+.1f}%")

    mr = best.get("monthly_ret", {})
    if mr:
        print("\n  Monthly Return:")
        years = {}
        for ym in sorted(mr.keys()):
            y = ym[:4]
            years.setdefault(y, []).append((ym, mr[ym]))
        for yr, months in sorted(years.items()):
            line = f"  {yr}: "
            for ym, r in months:
                mo = ym[5:]
                flag = "**" if r >= MONTHLY_TARGET else ("+" if r > 0 else " ")
                line += f"{mo}={r*100:+5.1f}%{flag}  "
            print(line)
    print()


def save_result(best: dict, label: str, ts: str):
    path = RESULTS_DIR / f"breakout_{label}_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(best, f, indent=2, ensure_ascii=False)
    return path


# ── 主程式 ───────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-rounds", type=int, default=MAX_ROUNDS)
    args_ns = parser.parse_args()
    max_rounds = args_ns.max_rounds

    print("\n" + "=" * 70)
    print("  BreakoutTrendStrategy Parameter Optimizer")
    print(f"  Target: {MONTHLY_TARGET*100:.0f}% every month  (24/24 months)")
    print(f"  Max rounds: {max_rounds}   Workers: {N_WORKERS}")
    print("=" * 70)

    # ── GPU 預計算
    print("\n[INIT] GPU precompute all indicators...")
    import pandas as pd, numpy as np
    DATA_PATH = ROOT / "data" / "historical" / "tmf_20260411_full_1m.csv"
    if not DATA_PATH.exists():
        print(f"ERROR: {DATA_PATH} not found")
        sys.exit(1)
    df = pd.read_csv(DATA_PATH, parse_dates=["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)
    n  = len(df)

    from core.gpu_indicators import precompute_all
    indicators = precompute_all(df, verbose=True)

    # ── Shared Memory
    print("[INIT] Building shared memory...")
    shm_list  = []
    shm_names = {}
    for key, arr in indicators.items():
        arr_c = np.ascontiguousarray(arr)
        shm = shared_memory.SharedMemory(create=True, size=arr_c.nbytes)
        np.ndarray(arr_c.shape, dtype=arr_c.dtype, buffer=shm.buf)[:] = arr_c
        shm_list.append(shm)
        shm_names[key] = (shm.name, arr_c.shape, str(arr_c.dtype))

    dt_ns  = df["datetime"].values.astype("datetime64[ns]").view("int64")
    dt_c   = np.ascontiguousarray(dt_ns)
    shm_dt = shared_memory.SharedMemory(create=True, size=dt_c.nbytes)
    np.ndarray(dt_c.shape, dtype="int64", buffer=shm_dt.buf)[:] = dt_c
    shm_list.append(shm_dt)

    shm_meta = {
        "shm_names": shm_names,
        "dt_shm":    (shm_dt.name, dt_c.shape, "int64"),
        "n": n,
    }
    print(f"  OK: {n:,} bars in shared memory")

    ts = datetime.now().strftime("%Y%m%d_%H%M")
    global_best = None

    # 預設進場參數（Phase 1 時固定）
    default_entry = {
        "min_adx": 15.0, "min_di_gap": 5.0,
        "squeeze_ratio": 0.78, "expand_ratio": 1.08,
        "min_vol_ratio": 1.15, "pullback_gap": 0.3,
    }

    try:
        for round_num in range(1, max_rounds + 1):
            print(f"\n{'='*70}")
            print(f"  ROUND {round_num}/{max_rounds}")
            print(f"{'='*70}")

            # ──────────────────────────────
            # Phase 1：出場參數搜索
            # ──────────────────────────────
            all_exit_combos = list(itertools.product(
                P1_GRID["sl_atr"], P1_GRID["tp_atr"],
                P1_GRID["trail_trigger"], P1_GRID["trail_dist"],
                P1_GRID["max_bars"],
            ))

            if round_num == 1:
                random.seed(1337)
                exit_combos = random.sample(all_exit_combos, min(P1_SAMPLE_N, len(all_exit_combos)))
                print(f"\n  Phase 1A: Exit param random sample  ({len(exit_combos)}/{len(all_exit_combos)} combos)")
            else:
                # Zoom in on best exit params
                if global_best:
                    b = global_best
                    def near(v, step, lo, hi):
                        return sorted(set(max(lo, min(hi, v + i*step)) for i in range(-1, 2)))
                    sl_r  = near(b["sl_atr"],       0.25, 0.5,  4.0)
                    tp_r  = near(b["tp_atr"],        1.0,  2.0, 20.0)
                    tr_r  = near(b["trail_trigger"], 0.25, 0.5,  5.0)
                    td_r  = near(b["trail_dist"],    0.25, 0.5,  3.0)
                    mb_r  = near(b["max_bars"],        15,  15,  180)
                    all_zoom = list(itertools.product(sl_r, tp_r, tr_r, td_r, mb_r))
                    random.seed(round_num * 13)
                    exit_combos = random.sample(all_zoom, min(300, len(all_zoom)))
                    print(f"\n  Phase 1B: Zoom-in exit params  ({len(exit_combos)} combos)")
                else:
                    exit_combos = random.sample(all_exit_combos, min(P1_SAMPLE_N, len(all_exit_combos)))

            # 使用預設進場參數或目前最佳進場參數
            if global_best:
                entry = {k: global_best[k] for k in
                         ["min_adx","min_di_gap","squeeze_ratio","expand_ratio","min_vol_ratio","pullback_gap"]}
            else:
                entry = default_entry

            args1 = [
                (sl, tp, trail, dist, bars,
                 entry["min_adx"], entry["min_di_gap"],
                 entry["squeeze_ratio"], entry["expand_ratio"],
                 entry["min_vol_ratio"], entry["pullback_gap"],
                 shm_meta)
                for sl, tp, trail, dist, bars in exit_combos
            ]
            r1 = _run_parallel(args1, desc="P1-Exit")
            best_p1 = r1[0] if r1 else None
            if best_p1:
                print(f"  P1 best: score={best_p1['score']:.2f}  "
                      f"target={best_p1['n_target_months']}/24  "
                      f"WR={best_p1['wr']}%  PF={best_p1['pf']}  Ret={best_p1['ret']:+.1f}%")

            # ──────────────────────────────
            # Phase 2：進場訊號參數搜索
            # ──────────────────────────────
            all_entry_combos = list(itertools.product(
                P2_GRID["min_adx"], P2_GRID["min_di_gap"],
                P2_GRID["squeeze_ratio"], P2_GRID["expand_ratio"],
                P2_GRID["min_vol_ratio"], P2_GRID["pullback_gap"],
            ))
            random.seed(round_num * 99)
            entry_combos = random.sample(all_entry_combos, min(P2_SAMPLE_N, len(all_entry_combos)))
            print(f"\n  Phase 2: Entry param random search  ({len(entry_combos)}/{len(all_entry_combos)} combos)")

            # 固定目前最佳出場參數
            if best_p1:
                exit_params = (best_p1["sl_atr"], best_p1["tp_atr"],
                               best_p1["trail_trigger"], best_p1["trail_dist"],
                               best_p1["max_bars"])
            else:
                exit_params = (1.5, 5.0, 2.0, 1.5, 60)

            args2 = [
                (*exit_params, adx, di_gap, sq, ex, vol, pb, shm_meta)
                for adx, di_gap, sq, ex, vol, pb in entry_combos
            ]
            r2 = _run_parallel(args2, desc="P2-Entry")

            # ──────────────────────────────
            # Phase 3：Top-5 × Top-5 聯合搜索
            # ──────────────────────────────
            print(f"\n  Phase 3: Joint top-5×top-5 search...")
            top_exit   = r1[:5] if r1 else []
            top_entry  = r2[:5] if r2 else []

            args3 = []
            for ep in top_exit:
                for en in top_entry:
                    args3.append((
                        ep["sl_atr"], ep["tp_atr"],
                        ep["trail_trigger"], ep["trail_dist"], ep["max_bars"],
                        en["min_adx"], en["min_di_gap"],
                        en["squeeze_ratio"], en["expand_ratio"],
                        en["min_vol_ratio"], en["pullback_gap"],
                        shm_meta,
                    ))
            r3 = _run_parallel(args3, desc="P3-Joint") if args3 else []

            # ── 本輪最佳
            all_results = r1 + r2 + r3
            all_results.sort(key=lambda x: x["score"], reverse=True)
            round_best = all_results[0] if all_results else None

            if round_best:
                print_report(round_best, f"Round {round_num} Best")
                save_path = save_result(round_best, f"r{round_num:02d}", ts)
                print(f"  Saved: {save_path.name}")

                if global_best is None or round_best["score"] > global_best["score"]:
                    global_best = round_best
                    print(f"  [NEW GLOBAL BEST] score={global_best['score']:.2f}  "
                          f"target={global_best['n_target_months']}/24")

                if round_best["n_target_months"] >= TARGET_MONTHS:
                    print(f"\n  *** TARGET ACHIEVED! {round_best['n_target_months']}/24 months >= 6% ***")
                    break
            else:
                print("  No valid results this round")

        # ── 最終報告
        print("\n" + "=" * 70)
        print("  FINAL RESULT")
        print("=" * 70)
        if global_best:
            print_report(global_best, "GLOBAL BEST")
            save_path = save_result(global_best, "final", ts)
            print(f"  Final result saved: {save_path.name}")

            if global_best["n_target_months"] < TARGET_MONTHS:
                gap = TARGET_MONTHS - global_best["n_target_months"]
                print(f"  Gap: {gap} months still below 6%")
                # Show worst months
                mr = global_best.get("monthly_ret", {})
                bad_months = sorted([(ym, r) for ym, r in mr.items() if r < MONTHLY_TARGET],
                                     key=lambda x: x[1])[:5]
                print(f"  5 worst months: " + "  ".join(f"{ym}={r*100:+.1f}%" for ym, r in bad_months))
        else:
            print("  No valid results found")

    finally:
        for shm in shm_list:
            shm.close()
            shm.unlink()


if __name__ == "__main__":
    main()
