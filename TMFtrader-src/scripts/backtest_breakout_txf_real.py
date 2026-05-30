"""BreakoutTrend 真實大台(TXF, 200元/點)日盤回測 2020-2026 —— 等比例縮放。

微台基準 125K/4000 → 大台 ×20:本金 250 萬、max_loss_twd 80,000。
其餘 v6b 參數、intrabar 硬停損、tmf_3x(4%/3口)不變。commission 18(與小台/微台同)。
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import warnings; warnings.filterwarnings("ignore")
import pandas as pd
from core.logger import setup_logger; setup_logger(console_level="CRITICAL")

from core.instrument_config import InstrumentSpec, INSTRUMENT_SPECS
INSTRUMENT_SPECS["TXF"] = InstrumentSpec(
    code="TXF", name="臺股期貨(大台)", point_value=200.0,
    margin=230000, maintenance_margin=176000, commission=18.0,
    tax_rate_pct=0.00002, strategy_type="breakout", default_initial_price=20000.0,
)

from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.breakout import BreakoutTrendStrategy
from scripts.optimize_strategy import _calc_metrics

DATA = ROOT / "data" / "vwap_fade" / "TXF_day_5m.parquet"
INIT_BAL = 2_500_000.0   # 微台 125K ×20
BREAKOUT = dict(
    expand_ratio=1.18, pullback_ema_gap=0.20, min_di_gap=10.0,
    trail_trigger_atr=1.2, trail_dist_atr=1.25, early_cut_bars=40, early_cut_loss_atr=1.5,
    momentum_rsi_bear=46.0, momentum_rsi_bull=52.0, max_loss_twd=80000.0,  # ×20
    min_adx=23.0, afternoon_min_adx=30.0, squeeze_grace_bars=1,
)


def run(df, label):
    df = df.sort_values("datetime").reset_index(drop=True)
    res = FastBacktestEngine(initial_balance=INIT_BAL, instrument="TXF",
                             intrabar_hard_exits=True).run(
        df, precompute_all(df, verbose=False), BreakoutTrendStrategy(**BREAKOUT), "tmf_3x")
    m = _calc_metrics(res)
    tr = list(res.trades); net = sum(t["pnl"] for t in tr)
    qty = {}
    for t in tr: qty[t["quantity"]] = qty.get(t["quantity"], 0) + 1
    qs = " ".join(f"{k}kx{v}" for k, v in sorted(qty.items()))
    print(f"  {label:18} | {m['n']:>3} bi | {qs:16} | WR {m['wr']:>5.1f}% | PF {m['pf']:>6.3f} | "
          f"Ret {m['ret']:>+7.2f}% | DD {m['dd']:>5.1f}% | net {net:>+13,.0f}")


def main():
    print("=" * 98)
    print("  BreakoutTrend REAL TXF(200/pt) day | bal 2,500,000 | tmf_3x 1-3 lots | max_loss_twd x20")
    df = pd.read_parquet(DATA); df["datetime"] = pd.to_datetime(df["datetime"])
    print(f"  data: {df['datetime'].min()} ~ {df['datetime'].max()} | {df['datetime'].dt.date.nunique()} days")
    print("=" * 98)
    run(df, "FULL 2020-2026")
    print("  " + "-" * 94)
    for yr in sorted(df["datetime"].dt.year.unique()):
        sub = df[df["datetime"].dt.year == yr]
        if len(sub) < 200: continue
        run(sub, str(yr))


if __name__ == "__main__":
    main()
