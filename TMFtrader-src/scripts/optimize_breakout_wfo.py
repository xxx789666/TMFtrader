"""Walk-Forward + 跨合約(MXF/TXF) OOS 嚴格驗證器(robustness 照妖鏡)。

★ 這不是「優化器」—— 它的價值是「樣本外驗證」:任何策略/參數都要在這裡證明
   能外推(OOS 正、大小台一致),否則一律視為過擬合/無 edge。

防自欺三道閘:
1. Walk-forward:滾動 IS 12月→OOS 6月、step 6月,OOS 為真未見資料。
2. 跨合約:每個 trial 目標 = min(MXF_score, TXF_score)(同台指大小台都要過)。
3. 過擬合偵測:看 trials↑ 時 OOS 有沒有變差(變差=過擬合)。

對外:`run_wfo(make_strategy, suggest_fn, label, n_trials, is_m, oos_m)` 供任何策略匯入驗證。
CLI:`python scripts/optimize_breakout_wfo.py [n_trials] [is_months] [oos_months]` 跑原始 BreakoutTrend。
"""
import sys, json
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import warnings; warnings.filterwarnings("ignore")
import pandas as pd, numpy as np
from core.logger import setup_logger; setup_logger(console_level="CRITICAL")

from core.instrument_config import InstrumentSpec, INSTRUMENT_SPECS
for _c, _pv, _mg in [("MXF", 50.0, 56000), ("TXF", 200.0, 230000)]:
    INSTRUMENT_SPECS[_c] = InstrumentSpec(code=_c, name=_c, point_value=_pv, margin=_mg,
        maintenance_margin=int(_mg * 0.77), commission=18.0, tax_rate_pct=0.00002,
        strategy_type="breakout", default_initial_price=20000.0)

import optuna
optuna.logging.set_verbosity(optuna.logging.WARNING)
from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.breakout import BreakoutTrendStrategy
from scripts.optimize_strategy import _calc_metrics

CONTRACTS = {"MXF": dict(bal=625_000.0, max_loss=20_000.0),
             "TXF": dict(bal=2_500_000.0, max_loss=80_000.0)}
FIXED = dict(pullback_ema_gap=0.20, min_di_gap=10.0, early_cut_loss_atr=1.5,
             momentum_rsi_bear=46.0, momentum_rsi_bull=52.0, squeeze_grace_bars=1)

_DATA_CACHE = {}


def _load():
    if _DATA_CACHE:
        return _DATA_CACHE
    for c in CONTRACTS:
        df = pd.read_parquet(ROOT / "data" / "vwap_fade" / f"{c}_day_5m.parquet")
        df["datetime"] = pd.to_datetime(df["datetime"]); df = df.sort_values("datetime").reset_index(drop=True)
        _DATA_CACHE[c] = (df, precompute_all(df, verbose=False))
    return _DATA_CACHE


def _slice(df, ind, lo, hi):
    mask = (df["datetime"] >= lo) & (df["datetime"] < hi)
    idx = np.where(mask.values)[0]
    if len(idx) == 0:
        return None, None
    s, e = idx[0], idx[-1] + 1
    return df.iloc[s:e].reset_index(drop=True), {k: np.asarray(v)[s:e] for k, v in ind.items()}


def _bt(df, ind, strategy, contract):
    res = FastBacktestEngine(initial_balance=CONTRACTS[contract]["bal"], instrument=contract,
                             intrabar_hard_exits=True).run(df, ind, strategy, "tmf_3x")
    m = _calc_metrics(res); m["net"] = sum(t["pnl"] for t in res.trades)
    return m


def run_wfo(make_strategy, suggest_fn, label="strategy", n_trials=60, is_m=12, oos_m=6):
    """make_strategy(params:dict, contract:str)->strategy;suggest_fn(trial)->params dict。
    回傳 rows(逐窗 OOS)。在 IS 上用 Optuna 挑參數(僅為選參、非目的),OOS 雙合約驗證。"""
    data = _load()
    step_m = oos_m
    t0 = data["MXF"][0]["datetime"].min().normalize()
    tN = data["MXF"][0]["datetime"].max().normalize()
    wins, cur = [], t0
    while cur + pd.DateOffset(months=is_m + oos_m) <= tN + pd.Timedelta(days=1):
        is_hi = cur + pd.DateOffset(months=is_m)
        wins.append((cur, is_hi, is_hi, is_hi + pd.DateOffset(months=oos_m)))
        cur = cur + pd.DateOffset(months=step_m)

    print(f"\n##### OOS 驗證:{label} | {len(wins)} 窗 IS {is_m}m/OOS {oos_m}m | n_trials={n_trials} | 跨合約 #####")
    print(f"{'OOS期間':<24}{'MXF_PF':>8}{'MXF_net':>11}{'TXF_PF':>8}{'TXF_net':>11}")
    print("-" * 70)
    rows = []
    for (is_lo, is_hi, oos_lo, oos_hi) in wins:
        is_sl = {c: _slice(*data[c], is_lo, is_hi) for c in CONTRACTS}
        if any(s[0] is None for s in is_sl.values()):
            continue

        def objective(trial):
            params = suggest_fn(trial)
            scs = []
            for c in CONTRACTS:
                m = _bt(is_sl[c][0], is_sl[c][1], make_strategy(params, c), c)
                if m["n"] < 8:
                    return -50.0
                scs.append(m["score"])
            return min(scs)

        study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=42))
        study.optimize(objective, n_trials=n_trials, show_progress_bar=False)
        best = study.best_params
        oos = {}
        for c in CONTRACTS:
            dfo, indo = _slice(*data[c], oos_lo, oos_hi)
            oos[c] = _bt(dfo, indo, make_strategy(best, c), c) if dfo is not None else {"pf": 0, "net": 0, "n": 0}
        print(f"{str(oos_lo.date())}~{oos_hi.date():%m-%d}      {oos['MXF']['pf']:>8.2f}{oos['MXF']['net']:>11,.0f}{oos['TXF']['pf']:>8.2f}{oos['TXF']['net']:>11,.0f}")
        rows.append({"oos": f"{oos_lo.date()}~{oos_hi.date()}", "best": best,
                     "mxf": {k: oos["MXF"].get(k) for k in ("pf", "net", "n", "wr")},
                     "txf": {k: oos["TXF"].get(k) for k in ("pf", "net", "n", "wr")}})
    print("-" * 70)
    for c, key in [("MXF", "mxf"), ("TXF", "txf")]:
        nets = [r[key]["net"] or 0 for r in rows]
        pfs = [r[key]["pf"] for r in rows if r[key]["pf"]]
        pos = sum(1 for x in nets if x > 0)
        print(f"  {c} OOS stitched: 淨 {sum(nets):>+12,.0f} | 正報酬窗 {pos}/{len(nets)} | 平均OOS_PF {np.mean(pfs) if pfs else 0:.2f}")
    out = ROOT / "data" / f"wfo_oos_{label}.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"逐窗存:{out.name}")
    return rows


# ── 原始 BreakoutTrend(CLI 預設)──────────────────────────────────
def _suggest_breakout(trial):
    return dict(
        trail_trigger_atr=trial.suggest_float("trail_trigger_atr", 0.8, 2.0, step=0.1),
        trail_dist_atr=trial.suggest_float("trail_dist_atr", 0.3, 2.0, step=0.1),
        min_adx=trial.suggest_float("min_adx", 18.0, 30.0, step=1.0),
        afternoon_min_adx=trial.suggest_float("afternoon_min_adx", 25.0, 40.0, step=1.0),
        expand_ratio=trial.suggest_float("expand_ratio", 1.05, 1.30, step=0.01),
        early_cut_bars=trial.suggest_int("early_cut_bars", 20, 60, step=5),
    )


def _make_breakout(params, contract):
    return BreakoutTrendStrategy(**{**FIXED, **params, "max_loss_twd": CONTRACTS[contract]["max_loss"]})


if __name__ == "__main__":
    nt = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    im = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    om = int(sys.argv[3]) if len(sys.argv) > 3 else 6
    run_wfo(_make_breakout, _suggest_breakout, "breakout", nt, im, om)
