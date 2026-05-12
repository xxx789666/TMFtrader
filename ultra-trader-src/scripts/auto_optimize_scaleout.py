"""
auto_optimize_scaleout.py — 分批出場（Scale-out）架構自動化優化

基準：v4b (trail_trigger=1.2, ec_bars=30, rsi_bear=46, 3口)
目標：找出 scale_out_trigger_atr × scale_out_qty 最佳組合

架構邏輯：
  持倉 3 口 → 獲利達到 scale_out_trigger_atr×ATR 時平 scale_out_qty 口
  剩餘口數繼續 trail_dist_atr=1.25 追蹤 → 捕捉大行情尾段

預期效益：
  - 鎖定部分獲利（減少回吐風險）
  - 剩餘口數繼續跑 → 大行情貢獻更高 net
  - RR 可能提升（部分口數提前鎖利）
"""

import json
import sys
import os
import time
from pathlib import Path
from itertools import product

# 加入專案根目錄
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

from core.logger import setup_logger
setup_logger(console_level='CRITICAL')

import pandas as pd
import numpy as np
from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.breakout import BreakoutTrendStrategy

# ── v4b 基準參數
BASE_V4B = dict(
    sl_atr=2.5, tp_atr=10.0,
    trail_trigger_atr=1.2,
    trail_dist_atr=1.25,
    max_bars=80,
    min_adx=20.0, afternoon_min_adx=32.0, min_di_gap=5.0,
    squeeze_ratio=0.90, expand_ratio=1.08, min_vol_ratio=1.0,
    pullback_ema_gap=0.3, breakeven_trigger_atr=999,
    early_cut_bars=30,
    early_cut_loss_atr=1.5,
    max_loss_twd=4000.0,
    trend_filter=True, ema200_margin_atr=0.0,
    use_momentum_score=True,
    momentum_rsi_bull=52.0, momentum_rsi_bear=46.0,
    momentum_session_atr=0.5, point_value=10.0,
    scale_out_trigger_atr=0.0,  # 0 = 停用，基準對照
    scale_out_qty=1,
)

# v4b 已知基準績效（供比對用）
BASELINE = {
    "n": 119, "wr": 57.1, "pf": 1.949, "net": 181130, "rr": 1.461, "n6": 8
}

# ── 掃描空間
SCALE_TRIGGER_RANGE = [0.0, 0.5, 0.7, 1.0, 1.2, 1.5, 2.0]   # ATR 倍數
SCALE_QTY_RANGE = [1]   # 口數（3口總量，先平1口，剩2口追蹤）


def run_backtest(df_5m: pd.DataFrame, ind: dict, params: dict) -> dict:
    """執行單次回測，回傳績效指標"""

    strategy = BreakoutTrendStrategy(**params)
    engine = FastBacktestEngine(
        initial_balance=200_000,
        slippage=1,
        instrument="TMF",
    )
    result = engine.run(df_5m, ind, strategy, risk_profile="tmf_3x")

    trades = result.trades
    if not trades:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "net": 0.0, "rr": 0.0, "n6": 0}

    winners = [t for t in trades if t["pnl"] > 0]
    losers  = [t for t in trades if t["pnl"] <= 0]
    n = len(trades)
    wr = len(winners) / n * 100 if n else 0.0

    total_win  = sum(t["pnl"] for t in winners)
    total_loss = abs(sum(t["pnl"] for t in losers))
    pf = total_win / total_loss if total_loss > 0 else float("inf")

    avg_win  = total_win  / len(winners) if winners else 0.0
    avg_loss = total_loss / len(losers)  if losers  else 0.0
    rr = avg_win / avg_loss if avg_loss > 0 else 0.0

    net = result.final_balance - result.initial_balance

    # N6: 月報酬≥6% 的月份數
    monthly: dict[str, float] = {}
    for t in trades:
        ym = t["exit_time"][:7]
        monthly[ym] = monthly.get(ym, 0) + t["pnl"]
    n6 = sum(1 for v in monthly.values() if v >= 200_000 * 0.06)

    return {"n": n, "wr": round(wr, 1), "pf": round(pf, 3),
            "net": round(net, 0), "rr": round(rr, 3), "n6": n6}


def score(m: dict) -> float:
    """
    綜合評分（分批出場版本）
    重點：net 和 rr 都要提升（相對基準）
    """
    # 正規化
    net_score = (m["net"] - 150_000) / 100_000
    rr_score  = (m["rr"]  - 1.2) / 0.5
    wr_score  = (m["wr"]  - 50.0) / 15.0
    pf_score  = (m["pf"]  - 1.5) / 0.8
    n6_score  = m["n6"] / 10.0

    s = rr_score * 3.0 + net_score * 2.5 + n6_score * 3.0 + wr_score * 1.0 + pf_score * 0.5

    # 懲罰：WR < 50% 或 PF < 1.5
    if m["wr"] < 50.0:
        s -= 2.0
    if m["pf"] < 1.5:
        s -= 2.0
    if m["n"] < 80:
        s -= 1.0

    return round(s, 4)


def format_row(trigger: float, qty: int, m: dict, s: float, vs_baseline: str) -> str:
    trigger_label = f"{trigger:.1f}" if trigger > 0 else "OFF"
    return (
        f"  trigger={trigger_label} qty={qty}"
        f"  n={m['n']:3d} wr={m['wr']:.1f}% pf={m['pf']:.3f}"
        f"  net={m['net']:+,.0f} rr={m['rr']:.3f} n6={m['n6']}"
        f"  score={s:+.3f}  {vs_baseline}"
    )


def main():
    # ── 資料載入（同 auto_optimize_v4b.py）
    data_path = ROOT / "data" / "historical" / "tmf_20260411_full_1m.csv"
    if not data_path.exists():
        print(f"[ERROR] 找不到資料檔：{data_path}")
        sys.exit(1)

    print("=" * 80)
    print("  Scale-out 架構優化 — v4b 基準 × 分批出場掃描")
    print("=" * 80)

    print("[INIT] 載入資料...")
    df_1m = pd.read_csv(data_path, parse_dates=["datetime"]).sort_values("datetime").reset_index(drop=True)
    df = df_1m.set_index("datetime")
    df_5m = df.resample("5min").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    df_5m = df_5m.between_time("08:45", "13:30").reset_index()
    print(f"  5分鐘K線: {len(df_5m):,} 根")
    print("[INIT] 預計算指標...")
    ind = precompute_all(df_5m, verbose=False)
    print("  完成\n")

    print(f"基準 v4b: n={BASELINE['n']} wr={BASELINE['wr']}% "
          f"pf={BASELINE['pf']} net={BASELINE['net']:+,} "
          f"rr={BASELINE['rr']} n6={BASELINE['n6']}")
    print()

    combos = list(product(SCALE_TRIGGER_RANGE, SCALE_QTY_RANGE))
    total = len(combos)
    results = []

    # 先跑基準（trigger=0，停用 scale-out）以確認環境一致
    print(f"[0/{total}] 基準對照 (scale_out=OFF)...")
    baseline_params = {**BASE_V4B, "scale_out_trigger_atr": 0.0, "scale_out_qty": 1}
    bm = run_backtest(df_5m, ind, baseline_params)
    bs = score(bm)
    print(f"  [BASELINE] n={bm['n']} wr={bm['wr']}% pf={bm['pf']:.3f} "
          f"net={bm['net']:+,} rr={bm['rr']:.3f} n6={bm['n6']} score={bs:+.3f}")
    print()

    for idx, (trigger, qty) in enumerate(combos, 1):
        if trigger == 0.0:
            # 與基準相同，跳過
            m = bm.copy()
            s = bs
            vs = "[BASELINE]"
        else:
            params = {**BASE_V4B, "scale_out_trigger_atr": trigger, "scale_out_qty": qty}
            t0 = time.time()
            m = run_backtest(df_5m, ind, params)
            elapsed = time.time() - t0
            s = score(m)

            # 與 v4b 基準比較
            net_chg = m["net"] - bm["net"]
            rr_chg  = m["rr"]  - bm["rr"]
            wr_chg  = m["wr"]  - bm["wr"]
            vs = f"Δnet={net_chg:+,.0f} Δrr={rr_chg:+.3f} Δwr={wr_chg:+.1f}%"

        results.append((trigger, qty, m, s))
        print(f"[{idx}/{total}] {format_row(trigger, qty, m, s, vs)}")

    # 排序
    results.sort(key=lambda x: -x[3])

    print()
    print("=" * 80)
    print("  TOP 10 結果（依評分排序）")
    print("=" * 80)
    for i, (trigger, qty, m, s) in enumerate(results[:10], 1):
        net_chg = m["net"] - bm["net"]
        rr_chg  = m["rr"]  - bm["rr"]
        wr_chg  = m["wr"]  - bm["wr"]
        vs = f"Δnet={net_chg:+,.0f} Δrr={rr_chg:+.3f} Δwr={wr_chg:+.1f}%"
        trigger_label = f"{trigger:.1f}" if trigger > 0 else "OFF"
        print(f"#{i:2d} trigger={trigger_label} qty={qty}  "
              f"n={m['n']:3d} wr={m['wr']:.1f}% pf={m['pf']:.3f}  "
              f"net={m['net']:+,.0f} rr={m['rr']:.3f} n6={m['n6']}  "
              f"score={s:+.3f}  {vs}")

    # 找出嚴格優於基準的結果（全部指標≥基準）
    strict_better = [
        (trigger, qty, m, s) for trigger, qty, m, s in results
        if trigger > 0
        and m["net"] >= bm["net"]
        and m["rr"]  >= bm["rr"]
        and m["wr"]  >= bm["wr"] - 0.5
        and m["pf"]  >= bm["pf"] - 0.05
    ]

    print()
    if strict_better:
        print("=" * 80)
        print("  ★ 嚴格優於基準（net↑ rr↑ wr≈ pf≈）")
        print("=" * 80)
        for trigger, qty, m, s in strict_better:
            net_chg = m["net"] - bm["net"]
            rr_chg  = m["rr"]  - bm["rr"]
            print(f"  trigger={trigger:.1f} qty={qty}  "
                  f"net={m['net']:+,.0f}({net_chg:+,.0f})  "
                  f"rr={m['rr']:.3f}({rr_chg:+.3f})  "
                  f"wr={m['wr']:.1f}%  pf={m['pf']:.3f}  score={s:+.3f}")
    else:
        print("  （無結果嚴格優於基準，參見 TOP 10）")

    print()
    best = results[0]
    print(f"★ 最佳: scale_out_trigger_atr={best[0]:.1f}, scale_out_qty={best[1]}")
    print(f"  net={best[2]['net']:+,.0f}  rr={best[2]['rr']:.3f}  "
          f"wr={best[2]['wr']:.1f}%  pf={best[2]['pf']:.3f}  "
          f"n6={best[2]['n6']}  score={best[3]:+.3f}")

    # 儲存結果
    out_path = ROOT / "data" / "backtest_results" / "scaleout_sweep_results.json"
    out_data = [
        {"scale_out_trigger_atr": t, "scale_out_qty": q,
         **m, "score": s}
        for t, q, m, s in results
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=2)
    print(f"\n結果已儲存：{out_path}")


if __name__ == "__main__":
    main()
