"""BreakoutTrend 真實小台(MXF)口徑回測 2020-2026 日盤。

與微台代理版的差別:
- instrument=MXF、point_value=50(真實小台),不是微台 10。
- 本金 625,000(=微台 125K ×5),讓 balance-based 動態口數(4%/3口)等比。
- breakout max_loss_twd 4000→20000(×5),讓資金停損點數與微台等價(否則 50元/點會過早洗出)。
其餘 v6b 參數、intrabar 硬停損、tmf_3x 風控不變。
→ 預期 PF/WR 與微台代理版一致(比率不變),但 $ 金額 ×5、回撤% 真實。
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import warnings; warnings.filterwarnings("ignore")
import pandas as pd
from core.logger import setup_logger; setup_logger(console_level="CRITICAL")

# 動態註冊 MXF spec(不動 committed instrument_config 的 lock-tmf-only)
from core.instrument_config import InstrumentSpec, INSTRUMENT_SPECS
INSTRUMENT_SPECS["MXF"] = InstrumentSpec(
    code="MXF", name="小型臺指期貨", point_value=50.0,   # 真實小台 1 點 = 50 元
    margin=56000, maintenance_margin=43000, commission=18.0,
    tax_rate_pct=0.00002, strategy_type="breakout", default_initial_price=20000.0,
)

from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.breakout import BreakoutTrendStrategy
from scripts.optimize_strategy import _calc_metrics

DATA = ROOT / "data" / "vwap_fade" / "MXF_day_5m.parquet"
INIT_BAL = 625_000.0   # 微台 125K ×5
# v6b live 參數,只把 max_loss_twd ×5(等價小台)
BREAKOUT = dict(
    expand_ratio=1.18, pullback_ema_gap=0.20, min_di_gap=10.0,
    trail_trigger_atr=1.2, trail_dist_atr=1.25, early_cut_bars=40, early_cut_loss_atr=1.5,
    momentum_rsi_bear=46.0, momentum_rsi_bull=52.0, max_loss_twd=20000.0,
    min_adx=23.0, afternoon_min_adx=30.0, squeeze_grace_bars=1,
)


def run(df, label):
    df = df.sort_values("datetime").reset_index(drop=True)
    res = FastBacktestEngine(initial_balance=INIT_BAL, instrument="MXF",
                             intrabar_hard_exits=True).run(
        df, precompute_all(df, verbose=False), BreakoutTrendStrategy(**BREAKOUT), "tmf_3x")
    m = _calc_metrics(res)
    tr = list(res.trades); net = sum(t["pnl"] for t in tr)
    qty = {}
    for t in tr: qty[t["quantity"]] = qty.get(t["quantity"], 0) + 1
    qs = " ".join(f"{k}口x{v}" for k, v in sorted(qty.items()))
    print(f"  {label:18} | {m['n']:>3} 筆 | {qs:16} | WR {m['wr']:>5.1f}% | PF {m['pf']:>6.3f} | "
          f"Ret {m['ret']:>+7.2f}% | DD {m['dd']:>5.1f}% | 淨 {net:>+12,.0f}")


def main():
    print("=" * 96)
    print("  BreakoutTrend 真實小台(MXF, 50元/點)日盤回測 | 本金 625,000 | tmf_3x 動態1~3口 | max_loss_twd×5")
    df = pd.read_parquet(DATA); df["datetime"] = pd.to_datetime(df["datetime"])
    print(f"  資料: {df['datetime'].min()} ~ {df['datetime'].max()} | {df['datetime'].dt.date.nunique()} 交易日")
    print("=" * 96)
    run(df, "全期 2020-2026")
    print("  " + "-" * 92)
    for yr in sorted(df["datetime"].dt.year.unique()):
        sub = df[df["datetime"].dt.year == yr]
        if len(sub) < 200: continue
        run(sub, str(yr))


if __name__ == "__main__":
    main()
