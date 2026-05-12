"""
vol_ratio 設計比較：session lock vs bar skip
=============================================
Version A (current):  vol_ratio > 2.0 → _entered=True → 本 session 報廢
Version B (proposed): vol_ratio > 2.0 → 只跳過此根 K 棒，session 繼續觀察

注意：不含 ML Filter，純測 vol_ratio 行為差異

執行：
    cd ultra-trader-src
    python scripts/compare_vol_lock.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from core.logger import setup_logger
setup_logger(console_level='CRITICAL')

import pandas as pd
import numpy as np
from typing import Optional
from strategy.orb import ORBStrategy
from strategy.base import Signal, KBar, MarketSnapshot
from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from datetime import time

ROOT = Path(__file__).parent.parent

# ── 載入資料 ──────────────────────────────────────────────────────────────
print("Loading TMF 5Y night session data...")
df = pd.read_parquet(ROOT / "data" / "historical" / "tmf_5y_5m.parquet")
df["datetime"] = pd.to_datetime(df["datetime"])
df = df.sort_values("datetime").reset_index(drop=True)

df_night = df[df["datetime"].dt.time.apply(
    lambda t: t >= time(21, 30) or t <= time(4, 0)
)].copy().reset_index(drop=True)

months = (df_night["datetime"].max() - df_night["datetime"].min()).days / 30.44
print(f"  Night bars: {len(df_night):,}  |  Period: {months:.1f} months")

print("Precomputing indicators...")
ind = precompute_all(df_night, verbose=False)
print("  Done.\n")


# ── Version B 子類：高量只跳棒，不鎖 session ─────────────────────────────
class ORBStrategy_BarSkip(ORBStrategy):
    """
    修改：vol_ratio > threshold 時只跳過此棒（return None），
    不設 _entered=True，讓下一根棒繼續判斷突破。
    """
    def on_kbar(self, kbar: KBar, snapshot: MarketSnapshot, **kwargs) -> Optional[Signal]:
        vol_ratio = getattr(snapshot, "volume_ratio", 1.0) or 1.0
        is_high_vol = (self.max_breakout_vol_ratio > 0
                       and vol_ratio > self.max_breakout_vol_ratio)

        if not is_high_vol:
            # 正常量：父類全走
            return super().on_kbar(kbar, snapshot, **kwargs)

        # 高量：暫停 vol_ratio 過濾跑父類，再處理結果
        orig = self.max_breakout_vol_ratio
        self.max_breakout_vol_ratio = 0.0
        result = super().on_kbar(kbar, snapshot, **kwargs)
        self.max_breakout_vol_ratio = orig

        if result is not None:
            # 父類想進場但量太高 → 撤銷 entry 狀態，不鎖 session
            self._entered = False
            self._entry_atr = 0.0
            self._trail_best = 0.0
            return None

        # 父類 return None（暖機/session外/ORB未就緒/ORB寬度過濾）
        # 如果是因為 ORB 寬度過濾而鎖 session（_entered 被父類設 True），保持不動
        # 若 _entered 未被父類設為 True，也不動
        return None


# ── 共用參數（B2 生產設定，不含 ML）─────────────────────────────────────
BASE = dict(
    point_value=10.0,
    max_loss_twd=4000.0,
    tp_atr=10.0,
    orb_minutes=45,
    sl_type="atr",
    sl_atr=2.0,
    min_orb_width_atr=3.0,
    max_orb_width_atr=5.0,
    max_breakout_vol_ratio=2.0,
    trail_trigger_atr=0.8,
    trail_dist_atr=1.25,
    early_cut_loss_atr=1.5,
    max_bars=60,
    allow_both_directions=True,
    trend_filter=False,
    session_start=(21, 30),
    force_close_time=(4, 0),
    ml_model_path=None,   # 關閉 ML，單純測 vol_ratio 行為
)


# ── 回測執行器 ────────────────────────────────────────────────────────────
def run_version(label: str, strategy_cls):
    strat  = strategy_cls(**BASE)
    engine = FastBacktestEngine(initial_balance=200_000, instrument="TMF")
    result = engine.run(df_night, ind, strat, "tmf_3x")
    trades = result.trades
    n = len(trades)
    if n == 0:
        print(f"[{label}] No trades.")
        return None

    wins   = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    gp = sum(t["pnl"] for t in wins)
    gl = abs(sum(t["pnl"] for t in losses)) or 1e-9
    pf = gp / gl
    wr = len(wins) / n * 100
    net = result.final_balance - 200_000
    n_per_month = n / months

    # avg_R（用 ATR 止損距離估算 risk）
    r_list = []
    for t in trades:
        risk = abs(t.get("entry_price", 0) - t.get("stop_loss", 0)) * 10.0
        if risk > 0:
            r_list.append(t["pnl"] / risk)
    avg_r = np.mean(r_list) if r_list else 0.0
    total_r = sum(r_list)

    # Max Drawdown
    eq = result.equity_curve
    peak = eq[0]; max_dd = 0.0
    for v in eq:
        if v > peak: peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd: max_dd = dd

    print(f"\n{'='*58}")
    print(f"  {label}")
    print(f"{'='*58}")
    print(f"  總交易數  : {n}  ({n_per_month:.1f} 筆/月)")
    print(f"  Win Rate  : {wr:.1f}%")
    print(f"  avg_R     : {avg_r:+.3f}")
    print(f"  Total R   : {total_r:+.1f}R")
    print(f"  PF        : {pf:.3f}")
    print(f"  Net P&L   : {net:+,.0f} TWD")
    print(f"  Max DD    : {max_dd:.1f}%")

    return dict(n=n, n_per_month=n_per_month, wr=wr,
                avg_r=avg_r, total_r=total_r, pf=pf, net=net, max_dd=max_dd)


# ── 執行兩版本 ────────────────────────────────────────────────────────────
print("Running Version A (current: session lock)...")
res_a = run_version("Version A — Session Lock (現行)", ORBStrategy)

print("\nRunning Version B (proposed: bar skip only)...")
res_b = run_version("Version B — Bar Skip Only (提案)", ORBStrategy_BarSkip)

# ── 差異摘要 ──────────────────────────────────────────────────────────────
if res_a and res_b:
    print(f"\n{'='*58}")
    print("  對比摘要  (+ 表示 B 比 A 更好)")
    print(f"{'='*58}")
    print(f"  {'指標':<15} {'Version A':>11} {'Version B':>11} {'B-A差異':>10}")
    print(f"  {'-'*53}")
    items = [
        ("交易數/月",  "n_per_month", "{:.1f}"),
        ("Win Rate %", "wr",          "{:.1f}"),
        ("avg_R",      "avg_r",       "{:+.3f}"),
        ("Total R",    "total_r",     "{:+.1f}"),
        ("PF",         "pf",          "{:.3f}"),
        ("Net P&L(元)","net",         "{:+,.0f}"),
        ("Max DD %",   "max_dd",      "{:.1f}"),
    ]
    for name, key, fmt in items:
        va = res_a[key]
        vb = res_b[key]
        diff = vb - va
        sign = "+" if diff > 0 else ""
        print(f"  {name:<15} {fmt.format(va):>11} {fmt.format(vb):>11} {sign}{fmt.format(diff):>9}")

    print(f"\n{'='*58}")
    better = "B" if res_b["avg_r"] > res_a["avg_r"] else "A"
    if better == "B":
        print("  結論 ★  Version B (bar skip) avg_R 更高 → 建議修改設計")
    else:
        print("  結論 ★  Version A (session lock) avg_R 更高 → 維持現行設計")
    print(f"{'='*58}\n")
