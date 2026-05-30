"""TMF 真實合約逐年回測 — 兩支 live 策略。
- 日盤 BreakoutTrend v6b → TMF 日盤 5m (TMF_oos_day_5m.parquet)
- 夜盤 ORB → TMF 全天 5m (由 TMFR1 1分K resample，含夜盤)
配置對齊 live：risk_profile=tmf_3x，本金 125K，動態 1~3 口。
TMF 資料 2024-07 ~ 2026-05（商品 2024-07 上市），故年份 2024(H2)/2025/2026(1-5月)。
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import warnings; warnings.filterwarnings("ignore")
import pandas as pd, datetime as dt
from core.logger import setup_logger; setup_logger(console_level="CRITICAL")
from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.breakout import BreakoutTrendStrategy
from strategy.orb import ORBStrategy
from scripts.optimize_strategy import _calc_metrics

HIST = ROOT.parent / "TMFtrader-src" / "data" / "history"
TMF_DAY = ROOT / "data" / "vwap_fade" / "TMF_oos_day_5m.parquet"
TMF_FULL = ROOT / "data" / "vwap_fade" / "TMF_full_5m.parquet"
INIT_BAL, PROFILE = 125_000.0, "tmf_3x"

BREAKOUT_LIVE = dict(expand_ratio=1.18, pullback_ema_gap=0.20, min_di_gap=10.0,
    trail_trigger_atr=1.2, trail_dist_atr=1.25, early_cut_bars=40, early_cut_loss_atr=1.5,
    momentum_rsi_bear=46.0, momentum_rsi_bull=52.0, max_loss_twd=4000.0,
    min_adx=23.0, afternoon_min_adx=30.0, squeeze_grace_bars=1)
ORB_LIVE = dict(orb_minutes=45, min_orb_width_atr=3.0, max_orb_width_atr=5.0,
    max_breakout_vol_ratio=2.0, sl_atr=2.0, max_sl_pts=120.0, trail_trigger_atr=0.8,
    trail_dist_atr=0.3, max_bars=60, early_cut_bars=30, early_cut_loss_atr=1.5,
    max_loss_twd=4000.0, session_start=(21, 30), force_close_time=(4, 0),
    max_entry_time=(1, 30), ml_model_path="", ml_features_path="", ml_threshold=0.40)

def build_tmf_full():
    if TMF_FULL.exists():
        return pd.read_parquet(TMF_FULL)
    files = sorted(HIST.glob("TMFR1_1min_*.parquet"))
    parts = []
    for f in files:
        d = pd.read_parquet(f); d["datetime"] = pd.to_datetime(d["ts"])
        d = d.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
        parts.append(d[["datetime", "open", "high", "low", "close", "volume"]])
    raw = pd.concat(parts).set_index("datetime").sort_index()
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    r = raw.resample("5min", label="left", closed="left").agg(agg).dropna(subset=["open"])
    r = r[r["volume"] > 0].reset_index()
    r.to_parquet(TMF_FULL)
    print(f"  TMF 全天 5m built: {len(r):,} bars")
    return r

def run(make_strat, df, label):
    res = FastBacktestEngine(initial_balance=INIT_BAL, instrument="TMF").run(df, precompute_all(df, verbose=False), make_strat(), PROFILE)
    m = _calc_metrics(res); tr = list(res.trades)
    net = sum(t.get("net_pnl", t.get("pnl", 0)) for t in tr)
    qty = {}
    for t in tr: qty[t["quantity"]] = qty.get(t["quantity"], 0) + 1
    qs = " ".join(f"{k}口x{v}" for k, v in sorted(qty.items()))
    print(f"  {label:14} | 筆數 {m['n']:>3} | {qs:18} | WR {m['wr']:>5.1f}% | PF {m['pf']:>6.3f} | "
          f"Sharpe {m['sharpe']:>6.2f} | Ret {m['ret']:>+7.2f}% | MaxDD {m['dd']:>5.2f}% | 淨損益 {net:>+9,.0f}")

def per_year(make_strat, df, name):
    df["datetime"] = pd.to_datetime(df["datetime"])
    print(f"\n##### {name} #####")
    print(f"  {'年份':14} | {'筆數':>3} | {'口數分佈':18} | {'WR':>6} | {'PF':>6} | {'Sharpe':>6} | {'Ret':>8} | {'MaxDD':>6} | 淨損益(微台NTD)")
    print("  " + "-" * 120)
    for yr in sorted(df["datetime"].dt.year.unique()):
        sub = df[df["datetime"].dt.year == yr].reset_index(drop=True)
        if len(sub) < 200: continue
        run(make_strat, sub, str(yr))
    run(make_strat, df.reset_index(drop=True), "全期合計")

def main():
    print("=" * 64)
    print("  TMF 真實合約逐年回測 — tmf_3x / 本金125K / 動態1~3口")
    print("  TMF 資料: 2024-07 ~ 2026-05 (商品2024-07上市)")
    print("=" * 64)
    df_day = pd.read_parquet(TMF_DAY)
    df_full = build_tmf_full()
    per_year(lambda: BreakoutTrendStrategy(**BREAKOUT_LIVE), df_day, "日盤 BreakoutTrend (TMF 日盤)")
    per_year(lambda: ORBStrategy(**ORB_LIVE), df_full, "夜盤 ORB (TMF 全天)")

if __name__ == "__main__":
    main()
