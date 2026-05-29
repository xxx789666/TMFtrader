"""
生成日盤 + 夜盤雙策略回測 Markdown 報告
用法：python scripts/gen_dual_report.py
"""
import sys, warnings
warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')

from core.logger import setup_logger
setup_logger(console_level='CRITICAL')

import pandas as pd
import numpy as np
import pickle
from datetime import datetime, time
from collections import defaultdict
from pathlib import Path

# ════════════════════════════════════════════════════════
# A. 日盤 — BreakoutTrendStrategy（08:45-13:30）
# ════════════════════════════════════════════════════════
from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.breakout import BreakoutTrendStrategy

print("載入 1m 資料並重採樣 5m...")
df_1m = pd.read_parquet('data/historical/tmf_5y_1m.parquet')
df_1m['datetime'] = pd.to_datetime(df_1m['datetime'])
df_1m = df_1m.set_index('datetime')
df_5m_all = (
    df_1m.resample('5min')
    .agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
    .dropna()
    .reset_index()
)

df_day = df_5m_all[
    df_5m_all['datetime'].dt.time.apply(lambda t: time(8, 45) <= t <= time(13, 30))
].copy().reset_index(drop=True)

print(f"日盤 5m: {len(df_day):,} bars  {df_day['datetime'].iloc[0].date()} ~ {df_day['datetime'].iloc[-1].date()}")

PARAMS = dict(
    sl_atr=2.5, tp_atr=10.0,
    trail_trigger_atr=1.75, trail_dist_atr=1.75, max_bars=30,
    min_adx=20.0, min_di_gap=5.0,
    squeeze_ratio=0.78, expand_ratio=1.08,
    min_vol_ratio=1.15, pullback_ema_gap=0.3,
    afternoon_min_adx=32.0, breakeven_trigger_atr=999,
)

print("計算日盤指標...")
ind = precompute_all(df_day, verbose=False)
strat = BreakoutTrendStrategy(**PARAMS)
engine = FastBacktestEngine(initial_balance=200_000, instrument='TMF')
result = engine.run(df_day, ind, strat, 'tmf_3x')
day_trades = result.trades
day_final = result.final_balance

day_wins = [t for t in day_trades if t['pnl'] > 0]
day_losses = [t for t in day_trades if t['pnl'] <= 0]
day_gp = sum(t['pnl'] for t in day_wins)
day_gl = abs(sum(t['pnl'] for t in day_losses)) if day_losses else 0

day_monthly = defaultdict(lambda: {'n': 0, 'wins': 0, 'pnl': 0.0})
for t in day_trades:
    ym = t['entry_time'][:7]
    day_monthly[ym]['n'] += 1
    day_monthly[ym]['pnl'] += t['pnl']
    if t['pnl'] > 0:
        day_monthly[ym]['wins'] += 1

eq = [200_000.0]; bal = 200_000.0
for t in sorted(day_trades, key=lambda x: x['entry_time']):
    bal += t['pnl']; eq.append(bal)
eq_arr = np.array(eq)
peak = np.maximum.accumulate(eq_arr)
day_max_dd = ((peak - eq_arr) / peak * 100).max()
day_avg_win = day_gp / len(day_wins) if day_wins else 0
day_avg_loss = day_gl / len(day_losses) if day_losses else 0
day_rr = day_avg_win / day_avg_loss if day_avg_loss else 999.0
day_mp = sum(1 for m in day_monthly.values() if m['pnl'] > 0)

print(f"日盤: {len(day_trades)}筆  WR={len(day_wins)/len(day_trades)*100:.1f}%  PF={day_gp/day_gl:.3f}  Net={day_final-200000:+,.0f}  MaxDD={day_max_dd:.2f}%")

# ════════════════════════════════════════════════════════
# B. 夜盤 — ORB B2（vol_ratio<=2.0 + ML >=0.40）
# ════════════════════════════════════════════════════════
print("載入 ORB 夜盤信號並套用 B2 過濾...")
df_orb = pd.read_parquet('data/historical/ml/TMF_night_orb_signals.parquet')
with open('optimizer/results_orb/orb_filter_b2.pkl', 'rb') as f:
    model = pickle.load(f)
features = [
    ln.strip()
    for ln in open('optimizer/results_orb/selected_features_b2.txt').readlines()
    if ln.strip()
]

b2_l1 = df_orb[df_orb['f_vol_ratio'] <= 2.0].copy()
probs = model.predict_proba(b2_l1[features])
b2_l1['ml_prob'] = probs
night_df = b2_l1[b2_l1['ml_prob'] >= 0.40].copy().sort_values('entry_time').reset_index(drop=True)

# 動態口數（tmf_3x：4% 風險，上限 3 口，point_value=10）
RISK_PCT    = 0.04
MAX_CONTRACTS = 3
POINT_VALUE = 10.0

equity = 200_000.0
pnl_list = []
qty_list = []
for _, row in night_df.iterrows():
    stop_dist = abs(float(row['entry_price']) - float(row['stop_price']))
    if stop_dist < 1e-6:
        stop_dist = float(row['atr_at_entry']) * 2.0
    qty = min(MAX_CONTRACTS, max(1, int(equity * RISK_PCT / (stop_dist * POINT_VALUE))))
    pnl = (float(row['exit_price']) - float(row['entry_price'])) * int(row['direction']) * qty * POINT_VALUE
    pnl_list.append(pnl)
    qty_list.append(qty)
    equity += pnl

night_df['qty'] = qty_list
night_df['pnl_twd'] = pnl_list

night_trades_n = len(night_df)
night_wins_n = int((night_df['pnl_twd'] > 0).sum())
night_losses_n = int((night_df['pnl_twd'] <= 0).sum())
night_gp = float(night_df[night_df['pnl_twd'] > 0]['pnl_twd'].sum())
night_gl = float(abs(night_df[night_df['pnl_twd'] < 0]['pnl_twd'].sum()))
night_net = float(night_df['pnl_twd'].sum())
night_wr = night_wins_n / night_trades_n * 100
night_pf = night_gp / night_gl if night_gl > 0 else 999.0
night_avg_win = float(night_df[night_df['pnl_twd'] > 0]['pnl_twd'].mean())
night_avg_loss = float(abs(night_df[night_df['pnl_twd'] < 0]['pnl_twd'].mean()))
night_rr = night_avg_win / night_avg_loss if night_avg_loss else 999.0

eq_n = np.concatenate([[200000.], 200000 + np.cumsum(night_df['pnl_twd'].values)])
peak_n = np.maximum.accumulate(eq_n)
night_max_dd = float(((peak_n - eq_n) / peak_n * 100).max())

night_monthly = {}
night_df['ym'] = night_df['entry_time'].dt.to_period('M').astype(str)
for ym, grp in night_df.groupby('ym'):
    night_monthly[ym] = {
        'n': len(grp),
        'wins': int((grp['pnl_twd'] > 0).sum()),
        'pnl': float(grp['pnl_twd'].sum()),
    }
night_mp = sum(1 for m in night_monthly.values() if m['pnl'] > 0)

print(f"夜盤: {night_trades_n}筆  WR={night_wr:.1f}%  PF={night_pf:.3f}  Net={night_net:+,.0f}  MaxDD={night_max_dd:.2f}%")

# ════════════════════════════════════════════════════════
# C. 生成 Markdown
# ════════════════════════════════════════════════════════
print("生成 Markdown 報告...")
md = []
md.append("# TMFtrader TMF 雙策略回測報告")
md.append("")
md.append(f"> 生成時間：{datetime.now().strftime('%Y-%m-%d %H:%M')}  ")
md.append(f"> 資料來源：tmf_5y_1m.parquet（2024-01-02 ~ 2026-04-17，1m 重採樣至 5m）  ")
md.append(f"> 日盤策略：BreakoutTrendStrategy（5m K棒，tmf_3x 風控，初始資金 200,000）  ")
md.append(f"> 夜盤策略：ORB B2（Opening Range Breakout + XGBoost ML Filter，初始資金 200,000）  ")
md.append("")
md.append("---")
md.append("")

# ── 日盤
md.append("## 日盤（08:45 ~ 13:30）— BreakoutTrendStrategy")
md.append("")
md.append(f"**資料期間：** {df_day['datetime'].iloc[0].strftime('%Y-%m-%d')} ~ {df_day['datetime'].iloc[-1].strftime('%Y-%m-%d')}  ")
md.append(f"**K棒數量：** {len(df_day):,} 根（5m）  ")
md.append("")
md.append("### 整體績效")
md.append("")
md.append("| 指標 | 數值 |")
md.append("|------|------|")
md.append(f"| 總交易筆數 | **{len(day_trades)} 筆** |")
md.append(f"| 勝率 | **{len(day_wins)/len(day_trades)*100:.1f}%** |")
md.append(f"| 獲利因子 (PF) | **{day_gp/day_gl:.3f}** |")
md.append(f"| 毛利 | +{day_gp:,.0f} |")
md.append(f"| 毛損 | -{day_gl:,.0f} |")
md.append(f"| 淨利 | **{day_final-200_000:+,.0f}** |")
md.append(f"| 報酬率 | **{(day_final-200_000)/200_000*100:+.2f}%** |")
md.append(f"| 最大回撤 | {day_max_dd:.2f}% |")
md.append(f"| 平均獲利 | +{day_avg_win:,.0f} |")
md.append(f"| 平均虧損 | -{day_avg_loss:,.0f} |")
md.append(f"| 盈虧比 | {day_rr:.2f} |")
md.append(f"| 獲利月份 | {day_mp} / {len(day_monthly)} 個月 |")
md.append("")
md.append("### 逐月損益")
md.append("")
md.append("| 月份 | 筆數 | 勝/敗 | 月損益 | 月報酬 | 累積資金 |")
md.append("|------|------|-------|--------|--------|----------|")
bal = 200_000.0
for ym in sorted(day_monthly):
    m = day_monthly[ym]
    ret = m['pnl'] / bal * 100
    flag = 'O' if m['pnl'] > 0 else 'X'
    bal += m['pnl']
    md.append(f"| {ym} | {m['n']} | {m['wins']}/{m['n']-m['wins']} | {m['pnl']:+,.0f} | {ret:+.1f}% | {bal:,.0f} {flag} |")
md.append("")
md.append("### 每筆交易明細")
md.append("")
md.append("| # | 進場時間 | 方向 | 進場價 | 出場價 | 損益 |")
md.append("|---|---------|------|--------|--------|------|")
for i, t in enumerate(day_trades, 1):
    d = t.get('direction', '?')
    md.append(f"| {i} | {t['entry_time'][:16]} | {d} | {t['entry_price']:,.0f} | {t['exit_price']:,.0f} | {t['pnl']:+,.0f} |")
md.append("")
md.append("---")
md.append("")

# ── 夜盤
md.append("## 夜盤（21:30 ~ 04:00）— ORB B2（Opening Range Breakout + ML Filter）")
md.append("")
md.append(f"**資料期間：** {night_df['entry_time'].iloc[0].strftime('%Y-%m-%d')} ~ {night_df['entry_time'].iloc[-1].strftime('%Y-%m-%d')}  ")
md.append(f"**信號來源：** TMF_night_orb_signals.parquet — 偵測到 137 筆突破信號，ML 拒絕 44 筆，實際下單 {night_trades_n} 筆")
md.append(f"**過濾條件：** Layer 1 vol_ratio <= 2.0 + B2 XGBoost 模型（threshold=0.40，AUC=0.621）")
md.append(f"**口數計算：** 動態口數（帳戶 4% 風險，上限 3 口，MXF 每點 10 元）")
md.append("")
md.append("### 整體績效")
md.append("")
md.append("| 指標 | 數值 |")
md.append("|------|------|")
md.append(f"| 偵測到突破信號 | 137 筆 |")
md.append(f"| ML 拒絕 | 44 筆 |")
md.append(f"| 實際下單 | **{night_trades_n} 筆** |")
md.append(f"| 口數模式 | 動態（1~3 口，4% 風控） |")
md.append(f"| 勝率 | **{night_wr:.1f}%** |")
md.append(f"| 獲利因子 (PF) | **{night_pf:.3f}** |")
md.append(f"| 毛利 | +{night_gp:,.0f} |")
md.append(f"| 毛損 | -{night_gl:,.0f} |")
md.append(f"| 淨利（TWD）| **{night_net:+,.0f}** |")
md.append(f"| 報酬率 | **{night_net/200_000*100:+.2f}%** |")
md.append(f"| 最大回撤 | {night_max_dd:.2f}% |")
md.append(f"| 平均獲利 | +{night_avg_win:,.0f} |")
md.append(f"| 平均虧損 | -{night_avg_loss:,.0f} |")
md.append(f"| 盈虧比 | {night_rr:.2f} |")
md.append(f"| 獲利月份 | {night_mp} / {len(night_monthly)} 個月 |")
md.append("")
md.append("### 逐月損益")
md.append("")
md.append("| 月份 | 筆數 | 勝/敗 | 月損益 | 月報酬 | 累積資金 |")
md.append("|------|------|-------|--------|--------|----------|")
bal = 200_000.0
for ym in sorted(night_monthly):
    m = night_monthly[ym]
    ret = m['pnl'] / bal * 100
    flag = 'O' if m['pnl'] > 0 else 'X'
    bal += m['pnl']
    md.append(f"| {ym} | {m['n']} | {m['wins']}/{m['n']-m['wins']} | {m['pnl']:+,.0f} | {ret:+.1f}% | {bal:,.0f} {flag} |")
md.append("")
md.append("### 每筆交易明細")
md.append("")
md.append("| # | 進場時間 | 出場時間 | 方向 | 口數 | 進場價 | 出場價 | 出場原因 | ML機率 | 損益(TWD) |")
md.append("|---|---------|---------|------|------|--------|--------|---------|--------|-----------|")
dir_map = {1: 'LONG', -1: 'SHORT'}
for i, row in night_df.iterrows():
    d = dir_map.get(int(row['direction']), '?')
    reason = str(row.get('exit_reason', '?'))
    prob = f"{row['ml_prob']:.2f}"
    md.append(
        f"| {i+1} | {str(row['entry_time'])[:16]} | {str(row['exit_time'])[:16]} "
        f"| {d} | {int(row['qty'])} | {row['entry_price']:,.0f} | {row['exit_price']:,.0f} "
        f"| {reason} | {prob} | {row['pnl_twd']:+,.0f} |"
    )
md.append("")
md.append("---")
md.append("")

# ── 對比摘要
md.append("## 日夜盤對比摘要")
md.append("")
md.append("| 指標 | 日盤（Breakout） | 夜盤（ORB B2） |")
md.append("|------|---------|---------|")
cmp_rows = [
    ("策略",         "BreakoutTrendStrategy",         "ORB + B2 ML Filter"),
    ("時段",         "08:45-13:30",                   "21:30-04:00"),
    ("總交易筆數",   f"{len(day_trades)} 筆",          f"{night_trades_n} 筆（ML 拒絕 44 筆）"),
    ("月均交易",     f"{len(day_trades)/len(day_monthly):.1f} 筆",
                                                       f"{night_trades_n/len(night_monthly):.1f} 筆"),
    ("勝率",         f"{len(day_wins)/len(day_trades)*100:.1f}%",  f"{night_wr:.1f}%"),
    ("獲利因子 PF",  f"{day_gp/day_gl:.3f}",           f"{night_pf:.3f}"),
    ("淨利",         f"{day_final-200_000:+,.0f}",     f"{night_net:+,.0f}"),
    ("報酬率",       f"{(day_final-200_000)/200_000*100:+.2f}%",   f"{night_net/200_000*100:+.2f}%"),
    ("最大回撤",     f"{day_max_dd:.2f}%",              f"{night_max_dd:.2f}%"),
    ("獲利月份",     f"{day_mp}/{len(day_monthly)}",    f"{night_mp}/{len(night_monthly)}"),
]
for label, dv, nv in cmp_rows:
    md.append(f"| {label} | {dv} | {nv} |")
md.append("")
md.append("> 備註：")
md.append("> - 日盤資料截至 2026-04-17；夜盤 ORB 信號截至 2026-04-08（4/10 後夜盤資料待補）")
md.append("> - 夜盤 MXF 每點 10 TWD，1 口；日盤 TMF 每點 200 TWD，1 口")
md.append("> - B2 ML 模型為 XGBoost（LOIO TMF AUC=0.621），threshold=0.40")
md.append("> - 兩策略各自獨立 200,000 初始資金與風控")

out = Path('data/backtest_results/session_report_20260428.md')
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text('\n'.join(md), encoding='utf-8')
print(f"\n[儲存完成] {out}")
print(f"\n{'='*60}")
print(f"日盤: {len(day_trades)}筆  WR={len(day_wins)/len(day_trades)*100:.1f}%  PF={day_gp/day_gl:.3f}  Net={day_final-200000:+,.0f}  MaxDD={day_max_dd:.2f}%")
print(f"夜盤: {night_trades_n}筆  WR={night_wr:.1f}%  PF={night_pf:.3f}  Net={night_net:+,.0f}  MaxDD={night_max_dd:.2f}%")
print(f"{'='*60}")
