"""
Diagnostic：是不是回測本身有問題？

驗證項目：
  1. or_fade longonly best params 跑 TMF OOS、trade reason 分佈
  2. avg win pts vs avg loss pts（判斷對稱性）
  3. 抽 1 個 short trade 手算 PnL 對 trade.pnl
  4. 跑既有 live BreakoutTrendStrategy 在同 TMF OOS、看是否有正常 edge
     （若有 → 引擎/資料 OK，MR 真的沒救；若無 → 引擎/資料可能有問題）

用法：python scripts/diagnose_or_fade.py
"""
import sys, warnings
warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
sys.path.insert(0, ".")

import pandas as pd
from pathlib import Path
from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.or_fade import OrFadeStrategy
from scripts.optimize_strategy import _calc_metrics


def reason_bucket(reason: str) -> str:
    if "盤末" in reason: return "force_close"
    if "停損" in reason and "時間" not in reason: return "atr_stop"
    if "OR-mid" in reason: return "or_mid_target"
    if "時間" in reason: return "time_stop"
    return "other:" + reason[:30]


def section(title): print("\n" + "=" * 60 + f"\n{title}\n" + "=" * 60)


# ── 載入 TMF OOS
df = pd.read_parquet("data/vwap_fade/TMF_oos_day_5m.parquet").reset_index(drop=True)
df["datetime"] = pd.to_datetime(df["datetime"])
days = df["datetime"].dt.date.nunique()
print(f"TMF OOS: {len(df)} bars, {days} trading days, "
      f"{df['datetime'].min()} ~ {df['datetime'].max()}")
ind = precompute_all(df, verbose=False)


# ── 1. or_fade longonly best params
section("1. or_fade longonly best params → TMF OOS")
best = {"or_bars": 12, "vol_ratio_max": 0.7, "wait_bars": 0,
        "sl_atr": 2.5, "max_bars": 24, "cooldown": 5}
strat = OrFadeStrategy(allow_short=False, **best)
res = FastBacktestEngine(initial_balance=200_000, instrument="TMF").run(
    df, ind, strat, "balanced")
m = _calc_metrics(res)
print(f"\nn={m['n']} WR={m['wr']}% PF={m['pf']} ret={m['ret']}% dd={m['dd']}% sharpe={m['sharpe']}")

# Reason 分佈
from collections import Counter
reasons = Counter(reason_bucket(t["reason"]) for t in res.trades)
print("\nExit reason 分佈：")
for k, v in reasons.most_common():
    pct = v / len(res.trades) * 100 if res.trades else 0
    print(f"  {k:<20} {v:>4} ({pct:.1f}%)")


# ── 2. 對稱性：avg win pts vs avg loss pts
section("2. 對稱性檢查：avg win pts vs avg loss pts")
wins  = [t["pnl_points"] for t in res.trades if t["pnl"] > 0]
losss = [t["pnl_points"] for t in res.trades if t["pnl"] <= 0]
avg_w = sum(wins) / max(len(wins), 1)
avg_l = sum(losss) / max(len(losss), 1)
print(f"\n  Wins:   n={len(wins):>3}  avg_pnl_pts={avg_w:+.2f}")
print(f"  Losses: n={len(losss):>3}  avg_pnl_pts={avg_l:+.2f}")
print(f"  Win/loss 點數比：{abs(avg_w/avg_l):.3f}（>1 才有 edge）")
if wins and losss:
    print(f"  Largest win:  {max(wins):+.0f} pts")
    print(f"  Largest loss: {min(losss):+.0f} pts")


# ── 3. 抽一筆 short trade 手算 PnL
section("3. SHORT trade PnL 手算驗證（如果 longonly 沒 short trade，跑一次 biside）")
short_trades = [t for t in res.trades if t["side"] == "short"]
if not short_trades:
    print("\n  longonly 沒 short trade（合理）；改跑 biside 一次抓 short：")
    biside_best = {"or_bars": 12, "vol_ratio_max": 0.7, "wait_bars": 0,
                   "sl_atr": 2.5, "max_bars": 24, "cooldown": 5}
    strat2 = OrFadeStrategy(allow_short=True, **biside_best)
    res2 = FastBacktestEngine(initial_balance=200_000, instrument="TMF").run(
        df, ind, strat2, "balanced")
    short_trades = [t for t in res2.trades if t["side"] == "short"]

if short_trades:
    t = short_trades[0]
    ep, xp, qty = t["entry_price"], t["exit_price"], t["quantity"]
    # SHORT: pnl_pts = (entry - exit) * qty
    expected_pnl_pts = (ep - xp) * qty
    expected_pnl = expected_pnl_pts * 10  # TMF point_value=10
    print(f"\n  Sample SHORT trade:")
    print(f"    entry_time={t['entry_time']}  exit_time={t['exit_time']}")
    print(f"    entry={ep}  exit={xp}  qty={qty}")
    print(f"    手算 pnl_pts = (entry - exit) * qty = ({ep} - {xp}) * {qty} = {expected_pnl_pts}")
    print(f"    手算 pnl     = pnl_pts * 10 = {expected_pnl}")
    print(f"    引擎 pnl_points = {t['pnl_points']}  (match: {t['pnl_points'] == expected_pnl_pts})")
    print(f"    引擎 pnl        = {t['pnl']}        (毛利 vs 手算 {expected_pnl}, 差 = commission)")
else:
    print("\n  仍無 short trade（biside 在此 OOS 可能也無 short），跳過手算")


# ── 4. 既有 live BreakoutTrendStrategy 跑同 TMF OOS、Sanity check
section("4. SANITY: 既有 live BreakoutTrendStrategy 在同 TMF OOS")
try:
    from strategy.breakout import BreakoutTrendStrategy
    bt = BreakoutTrendStrategy()
    res_b = FastBacktestEngine(initial_balance=200_000, instrument="TMF").run(
        df, ind, bt, "balanced")
    m_b = _calc_metrics(res_b)
    print(f"\n  BreakoutTrendStrategy on TMF OOS:")
    print(f"    n={m_b['n']} WR={m_b['wr']}% PF={m_b['pf']} ret={m_b['ret']}% "
          f"dd={m_b['dd']}% sharpe={m_b['sharpe']}")
    print(f"\n  解讀：")
    print(f"    若 PF > 1.0  → 引擎/資料 OK、MR 真的沒救（symptom = 3 MR FAIL 合理）")
    print(f"    若 PF < 1.0  → breakout 也虧 → 可能引擎/資料/成本有共同問題、需深查")
    print(f"    若 n = 0     → breakout 進場條件 vs OOS 資料不匹配（無關引擎）")
except Exception as e:
    print(f"\n  BreakoutTrendStrategy 跑不起來：{type(e).__name__}: {e}")
    print("  （可能需要 ml_model_path 等參數；可手動用 BreakoutTrendStrategy(...) 補）")


print("\n" + "=" * 60 + "\nDONE\n" + "=" * 60)
