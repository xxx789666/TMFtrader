"""用 live 實際參數回測 MXF(小台)長歷史 K 線。
- 日盤 live = BreakoutTrendStrategy v6b（engine.py 內建參數）
- 夜盤 live = ORB（夜盤 21:30-04:00，本機無夜盤資料 → skip，僅報告）
成本模型用 TMF 微台（live 合約；MXF 只作長歷史價格代理，與既有 pipeline 一致）。
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import warnings
warnings.filterwarnings("ignore")
import pandas as pd
import numpy as np

from core.logger import setup_logger
setup_logger(console_level="CRITICAL")
from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.breakout import BreakoutTrendStrategy
from scripts.optimize_strategy import _calc_metrics

DATA = ROOT / "data" / "vwap_fade" / "MXF_day_5m.parquet"

# live BreakoutTrend v6b 參數（複製自 core/engine.py _create_strategy("breakout")）
BREAKOUT_LIVE = dict(
    expand_ratio=1.18, pullback_ema_gap=0.20, min_di_gap=10.0,
    trail_trigger_atr=1.2, trail_dist_atr=1.25,
    early_cut_bars=40, early_cut_loss_atr=1.5,
    momentum_rsi_bear=46.0, momentum_rsi_bull=52.0,
    max_loss_twd=4000.0, min_adx=23.0, afternoon_min_adx=30.0,
    squeeze_grace_bars=1,
)

INIT_BAL = 125_000.0   # 實際 live 入金（engine 註解：入金125K）

def run_segment(df, ind, label):
    strat = BreakoutTrendStrategy(**BREAKOUT_LIVE)
    eng = FastBacktestEngine(initial_balance=INIT_BAL, instrument="TMF",
                             intrabar_hard_exits=True)   # 複製 live tick 層硬停損/止盈
    res = eng.run(df, ind, strat, "tmf_3x")
    m = _calc_metrics(res)
    trades = list(res.trades) if hasattr(res, "trades") else []
    net = sum(t.get("net_pnl", t.get("pnl", 0)) for t in trades)
    qty = {}
    for t in trades:
        qty[t["quantity"]] = qty.get(t["quantity"], 0) + 1
    days = df["datetime"].dt.date.nunique()
    print(f"\n[{label}]")
    print(f"  期間: {df['datetime'].iloc[0].date()} ~ {df['datetime'].iloc[-1].date()}  ({days} 交易日)")
    print(f"  trades={m['n']}  口數分佈={qty}  freq={m['n']/max(days,1):.2f}/日")
    print(f"  WR={m['wr']:.1f}%  PF={m['pf']:.3f}  Sharpe={m['sharpe']:.3f}")
    print(f"  Ret={m['ret']:+.2f}%  MaxDD={m['dd']:.2f}%")
    print(f"  淨損益(微台NTD, 已扣手續費+稅)= {net:+,.0f}")
    return m, trades

def main():
    print("=" * 64)
    print("  Live 策略回測 — 小台(MXF)長歷史 K 線, 微台(TMF)成本計價")
    print("=" * 64)
    df = pd.read_parquet(DATA).sort_values("datetime").reset_index(drop=True)
    ind = precompute_all(df, verbose=False)

    print("\n##### 日盤 live: BreakoutTrendStrategy v6b #####")
    print(f"  參數: {BREAKOUT_LIVE}")

    run_segment(df, ind, "全期")

    # 2024-01 之後（與 donchian/其他策略 OOS 對齊）
    mask = df["datetime"] >= "2024-01-01"
    if mask.any():
        i0 = int(df[mask].index[0])
        df2 = df.iloc[i0:].reset_index(drop=True)
        ind2 = precompute_all(df2, verbose=False)
        run_segment(df2, ind2, "2024-01 之後 (OOS 對齊)")

    # 逐年
    print("\n##### 逐年分解 #####")
    for yr in sorted(df["datetime"].dt.year.unique()):
        sub = df[df["datetime"].dt.year == yr].reset_index(drop=True)
        if len(sub) < 100:
            continue
        ind_y = precompute_all(sub, verbose=False)
        run_segment(sub, ind_y, f"{yr}")

    # 對照：實際 live 合約 TMF 微台 OOS（驗證 harness 能重現文件引用的 PF 1.513）
    tmf_oos = ROOT / "data" / "vwap_fade" / "TMF_oos_day_5m.parquet"
    if tmf_oos.exists():
        print("\n##### 對照: 實際 live 合約 TMF 微台 OOS #####")
        dft = pd.read_parquet(tmf_oos).sort_values("datetime").reset_index(drop=True)
        indt = precompute_all(dft, verbose=False)
        run_segment(dft, indt, "TMF_oos (2024-07+)")

    print("\n##### 夜盤 live: ORB #####")
    print("  SKIP — 本機 MXF 資料僅日盤 08:45-13:40，ORB 為夜盤策略(21:30-04:00)。")
    print("  夜盤歷史 K 線在 VPS data/history/，需先取回才能回測。")

if __name__ == "__main__":
    main()
