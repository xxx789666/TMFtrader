"""BreakoutTrend 重新回測 — 用與 live core/engine.py 一字不差的 v6b 參數與 strategy code path。

引擎: FastBacktestEngine(intrabar_hard_exits=True) 驅動同一個 BreakoutTrendStrategy。
  - tick 層硬停損/止盈 = 複製 live core/engine.py（bar high/low 觸價）
  - strategy.check_exit = trail / early-cut / money-stop / time（與 live 同一份）
  - tmf_3x 動態 1~3 口、本金 125K、動態稅成本（與 live 同）
資料: TMF 日盤 5m（冷備份 ml/TMF_5m.parquet, 2024-01~2026-04）。
  TMF 商品 2024-07 上市，2024-01~06 為 TXF/MXF 代理回填 → 另切「2024-07+ 真實合約」段。
輸出: 彙總 + 出場原因分布 + 逐年 + 逐筆 JSON/MD。
"""
import sys, json
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import warnings; warnings.filterwarnings("ignore")
import pandas as pd

from core.logger import setup_logger; setup_logger(console_level="CRITICAL")
from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.breakout import BreakoutTrendStrategy
from scripts.optimize_strategy import _calc_metrics

# 資料來源（冷備份；working tree 的 data/ 已 gitignore 大型 parquet）
DATA_CANDIDATES = [
    ROOT / "data" / "vwap_fade" / "TMF_oos_day_5m.parquet",
    Path("C:/Users/xx/Desktop/永豐-自動化交易/ultra-trader-src/data/historical/ml/TMF_5m.parquet"),
]
DATA = next((p for p in DATA_CANDIDATES if p.exists()), None)

# live BreakoutTrend v6b 參數（逐項核對 == core/engine.py _create_strategy("breakout") L121-135）
BREAKOUT_LIVE = dict(
    expand_ratio=1.18, pullback_ema_gap=0.20, min_di_gap=10.0,
    trail_trigger_atr=1.2, trail_dist_atr=1.25,
    early_cut_bars=40, early_cut_loss_atr=1.5,
    momentum_rsi_bear=46.0, momentum_rsi_bull=52.0,
    max_loss_twd=4000.0, min_adx=23.0, afternoon_min_adx=30.0,
    squeeze_grace_bars=1,
)
INIT_BAL, PROFILE = 125_000.0, "tmf_3x"


def run_segment(df, label):
    df = df.sort_values("datetime").reset_index(drop=True)
    ind = precompute_all(df, verbose=False)
    strat = BreakoutTrendStrategy(**BREAKOUT_LIVE)
    eng = FastBacktestEngine(initial_balance=INIT_BAL, instrument="TMF",
                             intrabar_hard_exits=True)
    res = eng.run(df, ind, strat, PROFILE)
    m = _calc_metrics(res)
    trades = list(res.trades)
    net = sum(t["pnl"] for t in trades)
    qty = {}
    for t in trades:
        qty[t["quantity"]] = qty.get(t["quantity"], 0) + 1
    # 出場原因分布（取原因前綴：硬停損/硬停利/追蹤止損/時間止損/虧損上限/早切/...）
    reasons = {}
    for t in trades:
        key = t["reason"].split(" @")[0].split(" ")[0].strip()
        reasons[key] = reasons.get(key, 0) + 1
    days = pd.to_datetime(df["datetime"]).dt.date.nunique()
    print(f"\n[{label}]")
    print(f"  期間 {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}  ({days} 交易日)")
    print(f"  trades={m['n']}  口數={qty}  freq={m['n']/max(days,1):.2f}/日")
    print(f"  WR={m['wr']:.1f}%  PF={m['pf']:.3f}  Sharpe={m['sharpe']:.3f}")
    print(f"  Ret={m['ret']:+.2f}%  MaxDD={m['dd']:.2f}%  期末={INIT_BAL+net:,.0f}")
    print(f"  平均獲利={m['avg_win']:+,.0f}  平均虧損=-{m['avg_loss']:,.0f}  淨損益={net:+,.0f}")
    print(f"  出場分布: " + "  ".join(f"{k}:{v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1])))
    return {"label": label, "metrics": m, "net": net, "qty": qty,
            "reasons": reasons, "trades": trades}


def main():
    if DATA is None:
        print("找不到 TMF 日盤資料 parquet"); sys.exit(1)
    print("=" * 70)
    print("  BreakoutTrend v6b 重新回測 — live 一字不差參數 + intrabar 硬停損")
    print(f"  資料: {DATA}")
    print(f"  參數: {BREAKOUT_LIVE}")
    print(f"  本金 {INIT_BAL:,.0f} / {PROFILE} 動態1~3口 / 動態稅")
    print("=" * 70)

    df = pd.read_parquet(DATA)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df[(df["datetime"].dt.hour >= 8) & (df["datetime"].dt.hour <= 13)].copy()  # 日盤段

    out = []
    out.append(run_segment(df, "全期 (含 2024H1 代理)"))
    real = df[df["datetime"] >= "2024-07-01"].copy()
    out.append(run_segment(real, "2024-07+ 真實合約"))

    print("\n##### 逐年 #####")
    for yr in sorted(df["datetime"].dt.year.unique()):
        sub = df[df["datetime"].dt.year == yr]
        if len(sub) < 200:
            continue
        out.append(run_segment(sub, f"{yr}"))

    # 逐筆 + 彙總落地
    stamp = "20260530"
    jpath = ROOT / "data" / f"breakout_tmf_live_{stamp}.json"
    payload = {
        "params": BREAKOUT_LIVE, "init_bal": INIT_BAL, "profile": PROFILE,
        "data": str(DATA), "engine": "FastBacktestEngine intrabar_hard_exits=True",
        "segments": [{k: v for k, v in s.items() if k != "trades"} for s in out],
        "trades_full_period": out[0]["trades"],
        "trades_real_contract": out[1]["trades"],
    }
    jpath.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n逐筆+彙總已寫入: {jpath}  ({len(out[0]['trades'])} 全期筆 / {len(out[1]['trades'])} 真實合約筆)")


if __name__ == "__main__":
    main()
