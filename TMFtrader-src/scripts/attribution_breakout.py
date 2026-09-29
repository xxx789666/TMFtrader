"""v0 BreakoutTrend 虧損歸因 —— 拆「Mode A vs B / 多 vs 空 / 高ER vs 低ER年」。

用 live v6b 參數、真實小台 MXF、full 2020-2026 日盤。掛 on_kbar 抓每筆進場模式,
match 回交易損益。目的:讓 v2 由數據逼出(不再純推測)。
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import warnings; warnings.filterwarnings("ignore")
import pandas as pd, numpy as np
from core.logger import setup_logger; setup_logger(console_level="CRITICAL")
from core.instrument_config import InstrumentSpec, INSTRUMENT_SPECS
INSTRUMENT_SPECS["MXF"] = InstrumentSpec(code="MXF", name="MXF", point_value=50.0, margin=56000,
    maintenance_margin=43000, commission=18.0, tax_rate_pct=0.00002, strategy_type="breakout",
    default_initial_price=20000.0)
from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.breakout import BreakoutTrendStrategy

BREAKOUT = dict(expand_ratio=1.18, pullback_ema_gap=0.20, min_di_gap=10.0, trail_trigger_atr=1.2,
    trail_dist_atr=1.25, early_cut_bars=40, early_cut_loss_atr=1.5, momentum_rsi_bear=46.0,
    momentum_rsi_bull=52.0, max_loss_twd=20000.0, min_adx=23.0, afternoon_min_adx=30.0, squeeze_grace_bars=1)
HIGH_ER_YEARS = {2020, 2026}   # 乾淨趨勢年(其餘為震盪/低 ER)


def grp(trades, keyfn):
    out = {}
    for t in trades:
        k = keyfn(t)
        out.setdefault(k, []).append(t["pnl"])
    rows = {}
    for k, v in out.items():
        n = len(v); wins = [x for x in v if x > 0]
        gp = sum(wins); gl = abs(sum(x for x in v if x <= 0))
        rows[k] = (n, sum(v), len(wins)/n*100 if n else 0, gp/gl if gl else 999)
    return rows


def main():
    df = pd.read_parquet(ROOT / "data" / "vwap_fade" / "MXF_day_5m.parquet")
    df["datetime"] = pd.to_datetime(df["datetime"]); df = df.sort_values("datetime").reset_index(drop=True)
    strat = BreakoutTrendStrategy(**BREAKOUT)
    entry_mode = {}
    orig = strat.on_kbar
    def hooked(kbar, snapshot, **kw):
        sig = orig(kbar, snapshot, **kw)
        if sig is not None:
            entry_mode[kbar.datetime.isoformat()] = "A" if "A-Squeeze" in sig.reason else ("B" if "B-Pullback" in sig.reason else "?")
        return sig
    strat.on_kbar = hooked
    res = FastBacktestEngine(initial_balance=625000.0, instrument="MXF", intrabar_hard_exits=True).run(
        df, precompute_all(df, verbose=False), strat, "tmf_3x")
    tr = list(res.trades)
    for t in tr:
        t["mode"] = entry_mode.get(t["entry_time"], "?")
        t["yr"] = int(t["entry_time"][:4])

    def show(title, rows):
        print(f"\n[{title}]")
        for k in sorted(rows):
            n, net, wr, pf = rows[k]
            print(f"  {str(k):14} | {n:>3} 筆 | 淨 {net:>+11,.0f} | WR {wr:>5.1f}% | PF {pf:>6.2f}")

    print("=" * 70); print("  v0 BreakoutTrend 虧損歸因 — 真實小台 MXF 2020-2026 日盤")
    print(f"  總: {len(tr)} 筆 | 淨 {sum(t['pnl'] for t in tr):+,.0f}"); print("=" * 70)
    show("依 進場模式(A=壓縮突破 / B=趨勢回調)", grp(tr, lambda t: t["mode"]))
    show("依 方向", grp(tr, lambda t: t["side"]))
    show("依 模式×方向", grp(tr, lambda t: f"{t['mode']}-{t['side']}"))
    show("依 regime(高ER趨勢年 2020/26 vs 其餘震盪年)", grp(tr, lambda t: "高ER趨勢年" if t["yr"] in HIGH_ER_YEARS else "低ER震盪年"))
    show("依 模式×regime", grp(tr, lambda t: f"{t['mode']}-{'趨勢' if t['yr'] in HIGH_ER_YEARS else '震盪'}"))


if __name__ == "__main__":
    main()
