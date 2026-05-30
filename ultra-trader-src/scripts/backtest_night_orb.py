"""夜盤 ORB 回測 — 用原始 MXFR1 1分K resample 成全時段 5分K（含夜盤）。
ORB live 參數複製自 core/engine.py _create_strategy("orb")（ML 已停用）。
成本用微台 TMF（live 合約；MXF 為長歷史價格代理）。
原始資料在 TMFtrader-src/data/history/，本機就有。
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import warnings
warnings.filterwarnings("ignore")
import pandas as pd
import datetime as dt

from core.logger import setup_logger
setup_logger(console_level="CRITICAL")
from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.orb import ORBStrategy
from scripts.optimize_strategy import _calc_metrics

HIST = ROOT.parent / "TMFtrader-src" / "data" / "history"
CACHE = ROOT / "data" / "vwap_fade" / "MXF_full_5m.parquet"

# ORB live 參數（core/engine.py，ML 停用）
ORB_LIVE = dict(
    orb_minutes=45, min_orb_width_atr=3.0, max_orb_width_atr=5.0,
    max_breakout_vol_ratio=2.0, sl_atr=2.0, max_sl_pts=120.0,
    trail_trigger_atr=0.8, trail_dist_atr=0.3, max_bars=60,
    early_cut_bars=30, early_cut_loss_atr=1.5, max_loss_twd=4000.0,
    session_start=(21, 30), force_close_time=(4, 0), max_entry_time=(1, 30),
    ml_model_path="", ml_features_path="", ml_threshold=0.40,
)

def build_full_5m():
    if CACHE.exists():
        print(f"  使用快取 {CACHE.name}")
        return pd.read_parquet(CACHE)
    files = sorted(HIST.glob("MXFR1_1min_*.parquet"))
    print(f"  載入 {len(files)} 個月檔 1分K 並 resample 5min...")
    parts = []
    for f in files:
        d = pd.read_parquet(f)
        d["datetime"] = pd.to_datetime(d["ts"])
        d = d.rename(columns={"Open": "open", "High": "high", "Low": "low",
                              "Close": "close", "Volume": "volume"})
        parts.append(d[["datetime", "open", "high", "low", "close", "volume"]])
    raw = pd.concat(parts).set_index("datetime").sort_index()
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    r = raw.resample("5min", label="left", closed="left").agg(agg).dropna(subset=["open"])
    r = r[r["volume"] > 0].reset_index()
    r.to_parquet(CACHE)
    print(f"  全期 5min bars: {len(r):,}  存 {CACHE.name}")
    return r

def run(df, ind, label):
    strat = ORBStrategy(**ORB_LIVE)
    res = FastBacktestEngine(initial_balance=125_000.0, instrument="TMF").run(df, ind, strat, "tmf_3x")
    m = _calc_metrics(res)
    tr = list(res.trades)
    net = sum(t.get("net_pnl", t.get("pnl", 0)) for t in tr)
    nights = df[df["datetime"].dt.time >= dt.time(21, 30)]["datetime"].dt.date.nunique()
    qty = {}
    for t in tr:
        qty[t["quantity"]] = qty.get(t["quantity"], 0) + 1
    print(f"\n[{label}]  {df['datetime'].iloc[0].date()} ~ {df['datetime'].iloc[-1].date()}  (~{nights} 夜)")
    print(f"  trades={m['n']}  口數分佈={qty}")
    print(f"  WR={m['wr']:.1f}%  PF={m['pf']:.3f}  Sharpe={m['sharpe']:.3f}")
    print(f"  Ret={m['ret']:+.2f}%  MaxDD={m['dd']:.2f}%  淨損益(微台NTD)= {net:+,.0f}")

def main():
    print("=" * 64)
    print("  夜盤 ORB 回測 — MXF 全時段(含夜盤) 5分K, 微台計價, 本金20萬")
    print("=" * 64)
    print(f"  ORB live 參數: session 21:30-04:00, orb=45min, width 3-5×ATR")
    df = build_full_5m()
    df["datetime"] = pd.to_datetime(df["datetime"])
    ind = precompute_all(df, verbose=False)
    run(df, ind, "全期")
    for yr in sorted(df["datetime"].dt.year.unique()):
        sub = df[df["datetime"].dt.year == yr].reset_index(drop=True)
        if len(sub) < 500:
            continue
        ind_y = precompute_all(sub, verbose=False)
        run(sub, ind_y, str(yr))

if __name__ == "__main__":
    main()
