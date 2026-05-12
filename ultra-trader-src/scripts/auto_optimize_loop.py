"""
UltraTrader 自動循環優化系統
目標：找到在 2024~2026 每月獲利 6% 的策略參數組合

搜索策略：
  Phase 1 — 參數細網格搜索（SL / Sig / Trail / 追蹤距離 / 時間停損）
  Phase 2 — 因子權重隨機搜索（8 個因子 × Dirichlet 採樣）
  Phase 3 — 聯合優化（最佳參數 + 最佳權重的組合微調）
  循環直到目標達成或最大輪數

目標評分：
  月獲利達標月數（每月 >= 6%）× 100 / 24 = 達標率%
  同時最大化：平均月獲利、最差月獲利、整體 PF

用法：python scripts/auto_optimize_loop.py [--max-rounds 10]
"""

import multiprocessing
multiprocessing.freeze_support()

import sys, os, time, json, csv, re, itertools, random, math, argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from concurrent.futures import as_completed
import multiprocessing.pool
from multiprocessing import shared_memory

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ── 全局設定 ────────────────────────────────────────────────────────────────
INITIAL_BALANCE  = 200_000.0
RISK_PROFILE     = "balanced"
MONTHLY_TARGET   = 0.06        # 6% per month
TARGET_MONTHS    = 24          # need all 24 months green at 6%+
MIN_TRADES_MONTH = 3           # ignore months with < 3 trades
MAX_ROUNDS       = 10
N_WORKERS        = min(os.cpu_count() or 4, 4)

RESULTS_DIR = ROOT / "data" / "optimize_results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Phase 1 parameter grid (fine-grained)
P1_GRID = {
    "sl_mult":       [1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 3.5, 4.0],
    "min_strength":  [0.55, 0.58, 0.60, 0.62, 0.64, 0.66, 0.68, 0.70, 0.72, 0.75],
    "trail_trigger": [0.3, 0.5, 0.75, 1.0, 1.5, 2.0],
    "trail_dist":    [0.5, 0.75, 1.0, 1.5, 2.0],
    "time_stop":     [20, 40, 60],
}

# Phase 2 factor weight samples (Dirichlet random search)
FACTOR_NAMES = ["trend", "rsi", "breakout", "volume", "adx", "volatility", "mtf", "candle"]
P2_SAMPLES   = 100   # random weight combinations to try
P1_RANDOM_N  = 600   # Phase 1A random sample size (instead of full 8100 grid)

# ── 月度損益計算 ─────────────────────────────────────────────────────────────
def calc_monthly(result) -> dict:
    """
    從 result.daily_pnl 計算每月損益和月報酬率
    回傳: {
      "monthly_pnl":    {"2024-01": 1234, ...},
      "monthly_ret":    {"2024-01": 0.062, ...},
      "n_target_months": 15,     # >= 6% 的月數
      "avg_monthly_ret": 0.03,
      "min_monthly_ret": -0.02,
      "max_monthly_ret":  0.08,
      "green_months":    12,     # > 0% 的月數
      "score":          62.5,   # n_target_months / TARGET_MONTHS * 100
    }
    """
    # 強制包含所有 24 個月（空倉月份 = 0%）
    ALL_MONTHS = [
        f"{y}-{m:02d}" for y in range(2024, 2027)
        for m in range(1, 13)
        if not (y == 2026 and m > 4)   # 資料截至 2026-04
    ]

    if not result.daily_pnl:
        return {"score": 0.0, "n_target_months": 0, "avg_monthly_ret": 0.0,
                "min_monthly_ret": 0.0, "monthly_ret": {ym: 0.0 for ym in ALL_MONTHS},
                "monthly_pnl": {}, "green_months": 0, "max_monthly_ret": 0.0}

    # 按月聚合 daily_pnl
    monthly_pnl = defaultdict(float)
    for day, pnl in result.daily_pnl.items():
        ym = day[:7]   # "2024-01"
        monthly_pnl[ym] += pnl

    # 計算月報酬率（複利基礎），空倉月份補 0
    balance = result.initial_balance
    monthly_ret = {}
    for ym in ALL_MONTHS:
        pnl = monthly_pnl.get(ym, 0.0)
        ret = pnl / balance if balance > 0 else 0.0
        monthly_ret[ym] = ret
        balance += pnl

    # 月報酬統計（基於全 24 個月）
    rets = [monthly_ret[ym] for ym in ALL_MONTHS]
    n_trading_months = sum(1 for ym in ALL_MONTHS if monthly_pnl.get(ym, 0) != 0)

    n_target  = sum(1 for r in rets if r >= MONTHLY_TARGET)
    n_green   = sum(1 for r in rets if r > 0)
    avg_ret   = sum(rets) / len(rets)
    min_ret   = min(rets)
    max_ret   = max(rets)

    # 懲罰交易太少的策略：少於 12 個月有交易則扣分
    trade_penalty = max(0, (12 - n_trading_months) * 0.5)

    # 評分：以目標月數為主，平均月報酬為輔
    score = (n_target / TARGET_MONTHS * 60) + (min(avg_ret / MONTHLY_TARGET, 1.0) * 30) + (n_green / TARGET_MONTHS * 10) - trade_penalty

    return {
        "score":          round(score, 4),
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


# ── 策略工廠 ────────────────────────────────────────────────────────────────
def _make_strategy(sl_mult, min_strength, trail_trigger, trail_dist, time_stop,
                   factor_weights=None):
    from strategy.momentum import AdaptiveMomentumStrategy
    from strategy.filters import MarketRegime

    strat = AdaptiveMomentumStrategy()

    # 注入因子權重
    if factor_weights:
        strat.signal_generator.weights = factor_weights.copy()

    sl, sig, trig, dist, ts = sl_mult, min_strength, trail_trigger, trail_dist, time_stop

    def fixed_update(atr_ratio: float, regime: MarketRegime):
        p = strat.signal_generator.params
        a = 0.3
        if regime.value in ("crisis_down", "crisis_reversal"):
            t_sl, t_sig, t_trig, t_dist = max(sl, 5.0), sig * 0.95, max(trig, 3.0), max(dist, 2.0)
        else:
            t_sl, t_sig, t_trig, t_dist = sl, sig, trig, dist
        p.stop_loss_multiplier = p.stop_loss_multiplier * (1-a) + t_sl  * a
        p.min_signal_strength  = p.min_signal_strength  * (1-a) + t_sig * a
        p.trailing_trigger     = p.trailing_trigger     * (1-a) + t_trig * a
        p.trailing_distance    = p.trailing_distance    * (1-a) + t_dist * a
        p.time_stop_bars = ts

    strat.signal_generator.params.update = fixed_update
    return strat


# ── Worker 函數 ──────────────────────────────────────────────────────────────
def _worker(args: tuple) -> dict:
    (sl_mult, min_strength, trail_trigger, trail_dist, time_stop,
     factor_weights, shm_meta) = args

    try:
        sys.path.insert(0, str(ROOT))
        from core.logger import setup_logger
        setup_logger(console_level="CRITICAL")

        import numpy as np
        from multiprocessing import shared_memory as shm_mod
        import pandas as pd
        from backtest.fast_engine import FastBacktestEngine

        # 從 shared memory 重建指標（不複製，保持 shm handle 開著）
        shm_handles = []
        indicators = {}
        for key, (shm_name, shape, dtype) in shm_meta["shm_names"].items():
            ex = shm_mod.SharedMemory(name=shm_name)
            shm_handles.append(ex)
            indicators[key] = np.ndarray(shape, dtype=dtype, buffer=ex.buf)

        # datetime
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

        engine = FastBacktestEngine(initial_balance=INITIAL_BALANCE, instrument="TMF")
        strat  = _make_strategy(sl_mult, min_strength, trail_trigger, trail_dist, time_stop, factor_weights)
        result = engine.run(df, indicators, strat, RISK_PROFILE)

        # 釋放 shared memory handles
        for h in shm_handles:
            h.close()

        m  = calc_monthly(result)
        tm = calc_trade_metrics(result)

        return {
            "sl_mult": sl_mult, "min_strength": min_strength,
            "trail_trigger": trail_trigger, "trail_dist": trail_dist,
            "time_stop": time_stop,
            "factor_weights": factor_weights,
            "score":           m["score"],
            "n_target_months": m["n_target_months"],
            "green_months":    m["green_months"],
            "avg_monthly_ret": m["avg_monthly_ret"],
            "min_monthly_ret": m["min_monthly_ret"],
            "max_monthly_ret": m["max_monthly_ret"],
            "monthly_ret":     m["monthly_ret"],
            "n_trades": tm["n"], "wr": tm["wr"], "pf": tm["pf"], "ret": tm["ret"],
            "final_balance": result.final_balance,
        }

    except Exception as e:
        import traceback
        return {
            "sl_mult": sl_mult, "min_strength": min_strength,
            "trail_trigger": trail_trigger, "trail_dist": trail_dist,
            "time_stop": time_stop, "factor_weights": factor_weights,
            "error": f"{type(e).__name__}: {e}",
            "score": -999.0, "n_target_months": 0,
        }


def _run_parallel(args_list, desc=""):
    results = []
    errors  = 0
    total   = len(args_list)
    t0 = time.time()
    # maxtasksperchild=20：每個 worker 跑 20 個任務後重啟，避免記憶體洩漏累積
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


# ── 月度報表 ────────────────────────────────────────────────────────────────
def print_monthly_report(best: dict, round_num: int):
    print(f"\n  ===== Round {round_num} Best  (Score: {best['score']:.2f}) =====")
    print(f"  Params: SL={best['sl_mult']}  Sig={best['min_strength']}  "
          f"Trail={best['trail_trigger']}  Dist={best['trail_dist']}  "
          f"TimeStop={best['time_stop']}")
    if best.get("factor_weights"):
        fw = best["factor_weights"]
        print(f"  Weights: " + "  ".join(f"{k}={v:.2f}" for k, v in fw.items()))
    print(f"  Trades:{best['n_trades']}  WR:{best['wr']}%  PF:{best['pf']}  "
          f"Ret:{best['ret']:+.1f}%  Final:{best['final_balance']:,.0f}")
    print(f"  Monthly target(6%): {best['n_target_months']}/24  "
          f"Green: {best['green_months']}/24  "
          f"Avg:{best['avg_monthly_ret']*100:+.1f}%  "
          f"Min:{best['min_monthly_ret']*100:+.1f}%  "
          f"Max:{best['max_monthly_ret']*100:+.1f}%")
    print()
    mr = best.get("monthly_ret", {})
    if mr:
        print("  Monthly Return:")
        cols = sorted(mr.keys())
        years = {}
        for ym in cols:
            y = ym[:4]
            if y not in years:
                years[y] = []
            years[y].append((ym, mr[ym]))
        for yr, months in sorted(years.items()):
            line = f"  {yr}: "
            for ym, r in months:
                mo = ym[5:]
                flag = "**" if r >= MONTHLY_TARGET else ("+" if r > 0 else " ")
                line += f"{mo}={r*100:+5.1f}%{flag}  "
            print(line)
    print()


def save_round_result(best: dict, round_num: int, ts: str):
    path = RESULTS_DIR / f"autoloop_r{round_num:02d}_{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        data = {k: v for k, v in best.items() if k not in ("monthly_ret",)}
        data["monthly_ret"] = best.get("monthly_ret", {})
        json.dump(data, f, indent=2, ensure_ascii=False)
    return path


def apply_params(best: dict):
    """將最佳參數和權重寫回 strategy/signals.py"""
    signals_path = ROOT / "strategy" / "signals.py"
    content = signals_path.read_text(encoding="utf-8")

    def sub_attr(text, attr, value):
        fmt = f"{value:.4f}".rstrip("0").rstrip(".")
        return re.sub(rf'(self\.{attr}\s*=\s*)[\d\.]+', rf'\g<1>{fmt}', text, count=1)

    def sub_target(text, var, value):
        fmt = f"{value:.4f}".rstrip("0").rstrip(".")
        return re.sub(rf'(target_{var}\s*=\s*)[\d\.]+', rf'\g<1>{fmt}', text, count=1)

    content = sub_attr(content, "stop_loss_multiplier", best["sl_mult"])
    content = sub_attr(content, "min_signal_strength",  best["min_strength"])
    content = sub_attr(content, "trailing_trigger",     best["trail_trigger"])
    content = sub_attr(content, "trailing_distance",    best["trail_dist"])
    content = sub_target(content, "sl",    best["sl_mult"])
    content = sub_target(content, "sig",   best["min_strength"])
    content = sub_target(content, "trail", best["trail_trigger"])
    signals_path.write_text(content, encoding="utf-8")

    if best.get("factor_weights"):
        fw = best["factor_weights"]
        # 更新 DEFAULT_WEIGHTS
        for fname, fval in fw.items():
            fmt = f"{fval:.4f}".rstrip("0").rstrip(".")
            content = re.sub(
                rf'("{fname}"\s*:\s*)[\d\.]+',
                rf'\g<1>{fmt}',
                content, count=1
            )
        signals_path.write_text(content, encoding="utf-8")


# ── 主程式 ───────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-rounds", type=int, default=MAX_ROUNDS)
    args = parser.parse_args()
    max_rounds = args.max_rounds

    print("\n" + "=" * 70)
    print("  UltraTrader Auto-Optimize Loop")
    print(f"  Target: {MONTHLY_TARGET*100:.0f}% profit every month  (24/24 months)")
    print(f"  Max rounds: {max_rounds}   Workers: {N_WORKERS}")
    print("=" * 70)

    # ── GPU 預計算（只做一次）
    print("\n[INIT] GPU precompute all indicators...")
    import pandas as pd, numpy as np
    DATA_PATH = ROOT / "data" / "historical" / "tmf_20260411_full_1m.csv"
    if not DATA_PATH.exists():
        print("ERROR: run backtest_longterm.py first")
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
    history = []

    try:
        for round_num in range(1, max_rounds + 1):
            print(f"\n{'='*70}")
            print(f"  ROUND {round_num}/{max_rounds}")
            print(f"{'='*70}")

            # ══════════════════════════════════════
            # Phase 1：參數網格搜索
            # ══════════════════════════════════════
            if round_num == 1:
                # 從全量 grid 中隨機採樣 P1_RANDOM_N 個組合
                all_combos = list(itertools.product(
                    P1_GRID["sl_mult"], P1_GRID["min_strength"],
                    P1_GRID["trail_trigger"], P1_GRID["trail_dist"], P1_GRID["time_stop"],
                ))
                random.seed(1337)
                combos = random.sample(all_combos, min(P1_RANDOM_N, len(all_combos)))
                print(f"\n  Phase 1A: Random sample  ({len(combos)}/{len(all_combos)} combos)")
            else:
                # Zoom in: 以目前最佳參數為中心縮小搜索範圍
                if global_best:
                    b = global_best
                    def near(v, step, lo, hi, n=3):
                        vals = sorted(set(max(lo, min(hi, v + i*step)) for i in range(-n, n+1)))
                        return vals
                    # n=1 → 3 values each → max 3^5=243 combos
                    sl_range  = near(b["sl_mult"],      0.25, 0.5, 5.0, n=1)
                    sig_range = near(b["min_strength"],  0.02, 0.50, 0.85, n=1)
                    tr_range  = near(b["trail_trigger"], 0.25, 0.25, 4.0, n=1)
                    td_range  = near(b["trail_dist"],    0.25, 0.25, 3.0, n=1)
                    ts_range  = near(b["time_stop"],       10,   10,  60, n=1)
                    all_zoom  = list(itertools.product(sl_range, sig_range, tr_range, td_range, ts_range))
                    random.seed(round_num * 13)
                    combos = random.sample(all_zoom, min(300, len(all_zoom)))
                    print(f"\n  Phase 1B: Zoom-in around best  ({len(combos)} combos)")
                else:
                    combos = list(itertools.product(
                        P1_GRID["sl_mult"], P1_GRID["min_strength"],
                        P1_GRID["trail_trigger"], P1_GRID["trail_dist"], P1_GRID["time_stop"],
                    ))

            args1 = [(sl, sig, trail, dist, ts_val, None, shm_meta)
                     for sl, sig, trail, dist, ts_val in combos]
            r1 = _run_parallel(args1, desc="P1")
            if r1:
                best_p1 = r1[0]
                print(f"  P1 best: score={best_p1['score']:.2f}  "
                      f"target={best_p1['n_target_months']}/24  "
                      f"WR={best_p1['wr']}%  PF={best_p1['pf']}  "
                      f"SL={best_p1['sl_mult']} Sig={best_p1['min_strength']} "
                      f"Trail={best_p1['trail_trigger']} Dist={best_p1['trail_dist']}")

            # ══════════════════════════════════════
            # Phase 2：因子權重隨機搜索
            # ══════════════════════════════════════
            print(f"\n  Phase 2: Factor weight random search  ({P2_SAMPLES} samples)")

            # 用目前最佳參數 + 隨機權重
            base_params = best_p1 if r1 else {
                "sl_mult": 1.75, "min_strength": 0.66, "trail_trigger": 0.5,
                "trail_dist": 1.0, "time_stop": 40,
            }

            def dirichlet_sample(n=8):
                # 帶偏向的 Dirichlet：給趨勢和突破更高初始值
                alpha = [3.0, 1.0, 2.0, 1.5, 1.0, 1.0, 1.5, 2.0]
                g = [-math.log(random.random()) / a for a in alpha]
                s = sum(g)
                return [round(v/s, 4) for v in g]

            random.seed(round_num * 42)
            weight_samples = []
            for _ in range(P2_SAMPLES):
                w = dirichlet_sample()
                weight_samples.append(dict(zip(FACTOR_NAMES, w)))

            # 也加入 DEFAULT_WEIGHTS 和一些手工設計的權重
            from strategy.signals import MultiFactorSignalGenerator
            weight_samples.append(MultiFactorSignalGenerator.DEFAULT_WEIGHTS.copy())
            # 趨勢優先版
            weight_samples.append({"trend": 0.35, "rsi": 0.08, "breakout": 0.15,
                                    "volume": 0.12, "adx": 0.10, "volatility": 0.05,
                                    "mtf": 0.10, "candle": 0.05})
            # 動量優先版
            weight_samples.append({"trend": 0.25, "rsi": 0.05, "breakout": 0.20,
                                    "volume": 0.15, "adx": 0.08, "volatility": 0.07,
                                    "mtf": 0.10, "candle": 0.10})
            # K線優先版
            weight_samples.append({"trend": 0.20, "rsi": 0.05, "breakout": 0.15,
                                    "volume": 0.10, "adx": 0.05, "volatility": 0.05,
                                    "mtf": 0.10, "candle": 0.30})

            args2 = [(base_params["sl_mult"], base_params["min_strength"],
                      base_params["trail_trigger"], base_params["trail_dist"],
                      base_params["time_stop"], fw, shm_meta)
                     for fw in weight_samples]

            r2 = _run_parallel(args2, desc="P2")

            # ══════════════════════════════════════
            # Phase 3：聯合最佳（Top-5 params × Top-5 weights）
            # ══════════════════════════════════════
            print(f"\n  Phase 3: Joint best-of-best search...")
            top_params  = r1[:5] if r1 else []
            top_weights = r2[:5] if r2 else []

            args3 = []
            for p in top_params:
                for w_result in top_weights:
                    fw = w_result.get("factor_weights") or MultiFactorSignalGenerator.DEFAULT_WEIGHTS.copy()
                    args3.append((p["sl_mult"], p["min_strength"], p["trail_trigger"],
                                  p["trail_dist"], p["time_stop"], fw, shm_meta))
            if args3:
                r3 = _run_parallel(args3, desc="P3")
            else:
                r3 = []

            # ══════════════════════════════════════
            # 本輪最佳
            # ══════════════════════════════════════
            all_results = r1 + r2 + r3
            all_results.sort(key=lambda x: x["score"], reverse=True)
            round_best = all_results[0] if all_results else None

            if round_best:
                print_monthly_report(round_best, round_num)
                save_path = save_round_result(round_best, round_num, ts)
                print(f"  Saved: {save_path.name}")
                history.append(round_best)

                # 更新全局最佳
                if global_best is None or round_best["score"] > global_best["score"]:
                    global_best = round_best
                    apply_params(global_best)
                    print(f"  [NEW BEST] score={global_best['score']:.2f}  "
                          f"target={global_best['n_target_months']}/24")

                # 達標檢查
                if round_best["n_target_months"] >= TARGET_MONTHS:
                    print(f"\n  *** TARGET ACHIEVED! {round_best['n_target_months']}/24 months >= 6% ***")
                    break

                # 進度顯示
                progress_pct = round_best["n_target_months"] / TARGET_MONTHS * 100
                print(f"  Progress: {progress_pct:.0f}%  "
                      f"({round_best['n_target_months']}/{TARGET_MONTHS} months on target)")
            else:
                print("  No valid results this round")

        # ══════════════════════════════════════════
        # 最終報告
        # ══════════════════════════════════════════
        print("\n" + "=" * 70)
        print("  FINAL RESULT")
        print("=" * 70)
        if global_best:
            print_monthly_report(global_best, 0)

            # 最終 MD 報告
            _write_final_md(global_best, ts)

            if global_best["n_target_months"] < TARGET_MONTHS:
                shortfall = TARGET_MONTHS - global_best["n_target_months"]
                print(f"  Gap: {shortfall} months still below 6% target")
                print("  Recommendation: Strategy architecture changes needed")
                print("  (RSI factor conflict with momentum regime — consider rewriting _score_rsi)")
        else:
            print("  No valid results found")

    finally:
        for shm in shm_list:
            shm.close()
            shm.unlink()


def _write_final_md(best: dict, ts: str):
    mr  = best.get("monthly_ret", {})
    mpnl = best.get("monthly_pnl", {})

    lines = [
        f"# Auto-Optimize Loop 最終報告",
        f"> 生成時間：{datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"> 目標：每月 >= 6%  |  達標：{best['n_target_months']}/24 月",
        "",
        "## 最佳參數",
        f"| 參數 | 值 |",
        f"|------|----|",
        f"| stop_loss_multiplier | {best['sl_mult']} |",
        f"| min_signal_strength  | {best['min_strength']} |",
        f"| trailing_trigger     | {best['trail_trigger']} |",
        f"| trailing_distance    | {best['trail_dist']} |",
        f"| time_stop_bars       | {best['time_stop']} |",
    ]
    if best.get("factor_weights"):
        lines += ["", "## 因子權重"]
        lines += [f"| {k} | {v:.3f} |" for k, v in best["factor_weights"].items()]

    lines += [
        "",
        "## 績效摘要",
        f"| 指標 | 數值 |",
        f"|------|------|",
        f"| 總交易次數 | {best['n_trades']} |",
        f"| 勝率 | {best['wr']}% |",
        f"| 獲利因子 | {best['pf']} |",
        f"| 總報酬率 | {best['ret']:+.2f}% |",
        f"| 達標月數（≥6%）| {best['n_target_months']}/24 |",
        f"| 獲利月數 | {best['green_months']}/24 |",
        f"| 平均月報酬 | {best['avg_monthly_ret']*100:+.1f}% |",
        f"| 最差月報酬 | {best['min_monthly_ret']*100:+.1f}% |",
        f"| 最佳月報酬 | {best['max_monthly_ret']*100:+.1f}% |",
        "",
        "## 月度損益明細",
        "| 月份 | 損益（元） | 月報酬率 | 達標 |",
        "|------|----------:|--------:|:---:|",
    ]
    for ym in sorted(set(list(mr.keys()) + list(mpnl.keys()))):
        r    = mr.get(ym, 0)
        pnl  = mpnl.get(ym, 0)
        flag = "✅" if r >= MONTHLY_TARGET else ("🟡" if r > 0 else "❌")
        lines.append(f"| {ym} | {pnl:+,.0f} | {r*100:+.1f}% | {flag} |")

    md_path = RESULTS_DIR / f"autoloop_final_{ts}.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n  MD report: {md_path.name}")


if __name__ == "__main__":
    main()
