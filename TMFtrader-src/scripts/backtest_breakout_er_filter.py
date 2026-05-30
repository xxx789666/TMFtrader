"""實驗:在 BreakoutTrend(真實小台 MXF 口徑)加「趨勢效率 ER 開關」。

假設:策略只在趨勢盤賺(高 ER)、震盪盤虧(低 ER)。
做法:對 5m close 算滾動 Kaufman ER(window W,純後向、無 lookahead),
      ER < threshold 的 bar 不放行進場(wrap strategy.on_kbar)。
比較:baseline(thr=0) vs 各門檻的 全期 PF/Ret/net/筆數,最佳者再印逐年。
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import warnings; warnings.filterwarnings("ignore")
import pandas as pd, numpy as np
from core.logger import setup_logger; setup_logger(console_level="CRITICAL")

from core.instrument_config import InstrumentSpec, INSTRUMENT_SPECS
INSTRUMENT_SPECS["MXF"] = InstrumentSpec(
    code="MXF", name="小型臺指期貨", point_value=50.0, margin=56000,
    maintenance_margin=43000, commission=18.0, tax_rate_pct=0.00002,
    strategy_type="breakout", default_initial_price=20000.0)

from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.breakout import BreakoutTrendStrategy
from scripts.optimize_strategy import _calc_metrics

DATA = ROOT / "data" / "vwap_fade" / "MXF_day_5m.parquet"
INIT_BAL = 625_000.0
BREAKOUT = dict(expand_ratio=1.18, pullback_ema_gap=0.20, min_di_gap=10.0,
    trail_trigger_atr=1.2, trail_dist_atr=1.25, early_cut_bars=40, early_cut_loss_atr=1.5,
    momentum_rsi_bear=46.0, momentum_rsi_bull=52.0, max_loss_twd=20000.0,
    min_adx=23.0, afternoon_min_adx=30.0, squeeze_grace_bars=1)


def rolling_er(close: pd.Series, W: int) -> pd.Series:
    num = (close - close.shift(W)).abs()
    den = close.diff().abs().rolling(W).sum()
    return (num / den).fillna(0.0)


def run(df, er_map, thr, label):
    df = df.sort_values("datetime").reset_index(drop=True)
    strat = BreakoutTrendStrategy(**BREAKOUT)
    if thr > 0:
        orig = strat.on_kbar
        def gated(kbar, snapshot, **kw):
            if er_map.get(kbar.datetime, 0.0) < thr:
                return None
            return orig(kbar, snapshot, **kw)
        strat.on_kbar = gated
    res = FastBacktestEngine(initial_balance=INIT_BAL, instrument="MXF",
                             intrabar_hard_exits=True).run(
        df, precompute_all(df, verbose=False), strat, "tmf_3x")
    m = _calc_metrics(res); net = sum(t["pnl"] for t in res.trades)
    print(f"  {label:22} | {m['n']:>3} bi | WR {m['wr']:>5.1f}% | PF {m['pf']:>6.3f} | "
          f"Ret {m['ret']:>+7.2f}% | DD {m['dd']:>5.1f}% | net {net:>+12,.0f}")
    return m, net


def main():
    df = pd.read_parquet(DATA); df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)
    W = int(sys.argv[1]) if len(sys.argv) > 1 else 300   # 5m bars; 60/day -> 300=5日
    er = rolling_er(df["close"], W)
    er_map = dict(zip(df["datetime"], er))
    print("=" * 90)
    print(f"  ER 過濾實驗 — 真實小台 MXF | window={W} bars(~{W//60}日)| 本金 62.5萬")
    print(f"  ER 分位數: p25={er.quantile(.25):.3f} p50={er.quantile(.5):.3f} p75={er.quantile(.75):.3f}")
    print("=" * 90)
    print("  [全期 2020-2026 各門檻]")
    for thr in [0.0, 0.10, 0.13, 0.16, 0.20, 0.25, 0.30]:
        run(df, er_map, thr, f"thr={thr:.2f}")
    # 逐年:baseline vs 一個代表性門檻
    best = float(sys.argv[2]) if len(sys.argv) > 2 else 0.16
    for tag, thr in [("baseline", 0.0), (f"ER>{best}", best)]:
        print(f"\n  [逐年 {tag}]")
        for yr in sorted(df["datetime"].dt.year.unique()):
            sub = df[df["datetime"].dt.year == yr]
            if len(sub) < 200: continue
            run(sub, er_map, thr, str(yr))


if __name__ == "__main__":
    main()
