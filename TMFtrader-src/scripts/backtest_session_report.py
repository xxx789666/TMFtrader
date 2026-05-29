"""
日盤 vs 夜盤 完整回測 → 生成 Markdown 報告
用法：python scripts/backtest_session_report.py
"""
import sys, warnings
warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')

from core.logger import setup_logger
setup_logger(console_level='CRITICAL')

import pandas as pd
import numpy as np
from datetime import datetime, time
from collections import defaultdict
from pathlib import Path

from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.breakout import BreakoutTrendStrategy

# ── 載入 1m 資料並重採樣至 5m ───────────────────────────────
df = pd.read_parquet('data/historical/tmf_5y_1m.parquet')
df['datetime'] = pd.to_datetime(df['datetime'])
df = df.set_index('datetime')
print(f"原始 1m: {len(df):,} bars  {df.index[0].date()} ~ {df.index[-1].date()}")

df_5m_all = (
    df.resample('5min')
    .agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
    .dropna()
    .reset_index()
)

# 日盤：08:45 ~ 13:45
df_day = df_5m_all[
    df_5m_all['datetime'].dt.time.apply(lambda t: time(8, 45) <= t <= time(13, 45))
].copy().reset_index(drop=True)

# 夜盤：>= 15:00 or <= 05:00
df_night = df_5m_all[
    df_5m_all['datetime'].dt.time.apply(lambda t: t >= time(15, 0) or t <= time(5, 0))
].copy().reset_index(drop=True)

print(f"日盤 5m: {len(df_day):,} bars  {df_day['datetime'].iloc[0].date()} ~ {df_day['datetime'].iloc[-1].date()}")
print(f"夜盤 5m: {len(df_night):,} bars  {df_night['datetime'].iloc[0].date()} ~ {df_night['datetime'].iloc[-1].date()}")

# ── 策略參數（與 .env 一致）──────────────────────────────────
PARAMS = dict(
    sl_atr=2.5, tp_atr=10.0,
    trail_trigger_atr=1.75, trail_dist_atr=1.75, max_bars=30,
    min_adx=20.0, min_di_gap=5.0,
    squeeze_ratio=0.78, expand_ratio=1.08,
    min_vol_ratio=1.15, pullback_ema_gap=0.3,
    afternoon_min_adx=32.0, breakeven_trigger_atr=999,
)


def run_session(df_sess, label):
    """執行一個時段的回測，回傳統計字典"""
    if len(df_sess) < 100:
        print(f"  {label}: 資料不足（{len(df_sess)} bars）")
        return None
    print(f"  計算指標中...")
    ind = precompute_all(df_sess, verbose=False)
    strat = BreakoutTrendStrategy(**PARAMS)
    engine = FastBacktestEngine(initial_balance=200_000, instrument='TMF')
    result = engine.run(df_sess, ind, strat, 'tmf_3x')
    trades = result.trades
    print(f"  {label}: {len(trades)} 筆交易")

    if not trades:
        return {
            'label': label, 'trades': [], 'monthly': {},
            'final': 200_000, 'n_bars': len(df_sess),
            'date_start': df_sess['datetime'].iloc[0],
            'date_end':   df_sess['datetime'].iloc[-1],
        }

    wins   = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    gp = sum(t['pnl'] for t in wins)
    gl = abs(sum(t['pnl'] for t in losses)) if losses else 0

    monthly = defaultdict(lambda: {'n': 0, 'wins': 0, 'pnl': 0.0})
    for t in trades:
        ym = t['entry_time'][:7]
        monthly[ym]['n'] += 1
        monthly[ym]['pnl'] += t['pnl']
        if t['pnl'] > 0:
            monthly[ym]['wins'] += 1

    # 最大回撤
    eq = [200_000.0]
    bal = 200_000.0
    for t in sorted(trades, key=lambda x: x['entry_time']):
        bal += t['pnl']
        eq.append(bal)
    eq_arr = np.array(eq)
    peak = np.maximum.accumulate(eq_arr)
    dd = (peak - eq_arr) / peak * 100

    return {
        'label': label,
        'trades': trades,
        'wins': wins,
        'losses': losses,
        'gp': gp,
        'gl': gl,
        'pf': gp / gl if gl > 0 else 999.0,
        'wr': len(wins) / len(trades) * 100,
        'monthly': dict(monthly),
        'final': result.final_balance,
        'max_dd': dd.max(),
        'n_bars': len(df_sess),
        'date_start': df_sess['datetime'].iloc[0],
        'date_end':   df_sess['datetime'].iloc[-1],
    }


print("\n執行日盤回測...")
day_r = run_session(df_day, '日盤')
print("執行夜盤回測...")
night_r = run_session(df_night, '夜盤')
print("生成 Markdown 報告...")


# ── Markdown 生成 ─────────────────────────────────────────────
def md_section(r):
    lines = []
    if r is None:
        return ['> 資料不足，無法回測\n']

    trades = r['trades']
    n = len(trades)
    lines.append(f"**資料期間：** {r['date_start'].strftime('%Y-%m-%d')} ～ {r['date_end'].strftime('%Y-%m-%d')}  ")
    lines.append(f"**K棒數量：** {r['n_bars']:,} 根  ")
    lines.append("")

    if n == 0:
        lines.append("> 0 筆交易 — 策略條件在此時段內未觸發")
        return lines

    wins_pnl = r['gp'] / len(r['wins']) if r['wins'] else 0
    loss_pnl = r['gl'] / len(r['losses']) if r['losses'] else 0
    rr = wins_pnl / loss_pnl if loss_pnl > 0 else 999.0
    monthly_profit = sum(1 for m in r['monthly'].values() if m['pnl'] > 0)

    lines.append("### 整體績效")
    lines.append("")
    lines.append("| 指標 | 數值 |")
    lines.append("|------|------|")
    lines.append(f"| 總交易筆數 | **{n} 筆** |")
    lines.append(f"| 勝率 | **{r['wr']:.1f}%** |")
    lines.append(f"| 獲利因子 (PF) | **{r['pf']:.3f}** |")
    lines.append(f"| 毛利 | +{r['gp']:,.0f} |")
    lines.append(f"| 毛損 | -{r['gl']:,.0f} |")
    lines.append(f"| 淨利 | **{r['final'] - 200_000:+,.0f}** |")
    lines.append(f"| 報酬率 | **{(r['final'] - 200_000) / 200_000 * 100:+.2f}%** |")
    lines.append(f"| 最大回撤 | {r['max_dd']:.2f}% |")
    lines.append(f"| 平均獲利 | +{wins_pnl:,.0f} |")
    lines.append(f"| 平均虧損 | -{loss_pnl:,.0f} |")
    lines.append(f"| 盈虧比 | {rr:.2f} |")
    lines.append(f"| 獲利月份 | {monthly_profit} / {len(r['monthly'])} 個月 |")
    lines.append("")

    lines.append("### 逐月損益")
    lines.append("")
    lines.append("| 月份 | 筆數 | 勝/敗 | 月損益 | 月報酬 | 累積資金 |")
    lines.append("|------|------|-------|--------|--------|----------|")
    bal = 200_000.0
    for ym in sorted(r['monthly']):
        m = r['monthly'][ym]
        ret = m['pnl'] / bal * 100
        flag = '✅' if m['pnl'] > 0 else '❌'
        bal += m['pnl']
        lines.append(f"| {ym} | {m['n']} | {m['wins']}/{m['n'] - m['wins']} | {m['pnl']:+,.0f} | {ret:+.1f}% | {bal:,.0f} {flag} |")
    lines.append("")

    lines.append("### 每筆交易明細")
    lines.append("")
    lines.append("| # | 進場時間 | 方向 | 進場價 | 出場價 | 損益 |")
    lines.append("|---|---------|------|--------|--------|------|")
    for i, t in enumerate(trades, 1):
        d = t.get('direction', '?')
        lines.append(
            f"| {i} | {t['entry_time'][:16]} | {d} "
            f"| {t['entry_price']:,.0f} | {t['exit_price']:,.0f} "
            f"| {t['pnl']:+,.0f} |"
        )
    return lines


md = []
md.append("# TMFtrader TMF BreakoutTrendStrategy 回測報告")
md.append("")
md.append(f"> 生成時間：{datetime.now().strftime('%Y-%m-%d %H:%M')}  ")
md.append(f"> 策略：BreakoutTrendStrategy（5m K棒，tmf_3x 風控，初始資金 200,000）  ")
md.append(f"> 資料來源：tmf_5y_1m.parquet（重採樣至 5m）  ")
md.append(f"> 資料截止：2026-04-17（4/18 後資料待補）  ")
md.append("")
md.append("---")
md.append("")
md.append("## 🌅 日盤（08:45 ～ 13:45）")
md.append("")
md += md_section(day_r)
md.append("")
md.append("---")
md.append("")
md.append("## 🌙 夜盤（15:00 ～ 05:00）")
md.append("")
md += md_section(night_r)
md.append("")
md.append("---")
md.append("")

# 對比摘要
if day_r and night_r and day_r['trades'] and night_r['trades']:
    md.append("## 📊 日夜盤對比摘要")
    md.append("")
    md.append("| 指標 | 🌅 日盤 | 🌙 夜盤 |")
    md.append("|------|--------|--------|")
    rows = [
        ("總交易筆數",  f"{len(day_r['trades'])} 筆",               f"{len(night_r['trades'])} 筆"),
        ("勝率",        f"{day_r['wr']:.1f}%",                      f"{night_r['wr']:.1f}%"),
        ("獲利因子 PF", f"{day_r['pf']:.3f}",                       f"{night_r['pf']:.3f}"),
        ("淨利",        f"{day_r['final'] - 200_000:+,.0f}",        f"{night_r['final'] - 200_000:+,.0f}"),
        ("報酬率",      f"{(day_r['final']-200_000)/200_000*100:+.2f}%", f"{(night_r['final']-200_000)/200_000*100:+.2f}%"),
        ("最大回撤",    f"{day_r['max_dd']:.2f}%",                  f"{night_r['max_dd']:.2f}%"),
        ("獲利月份",
         f"{sum(1 for m in day_r['monthly'].values() if m['pnl']>0)}/{len(day_r['monthly'])}",
         f"{sum(1 for m in night_r['monthly'].values() if m['pnl']>0)}/{len(night_r['monthly'])}"),
    ]
    for label, dv, nv in rows:
        md.append(f"| {label} | {dv} | {nv} |")
    md.append("")

out_path = Path('data/backtest_results/session_report_20260428.md')
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text('\n'.join(md), encoding='utf-8')
print(f"\n[儲存完成] {out_path}")

# 終端摘要
print("\n" + "="*60)
print("日盤:")
if day_r and day_r['trades']:
    print(f"  {len(day_r['trades'])}筆  WR={day_r['wr']:.1f}%  PF={day_r['pf']:.3f}  "
          f"淨利={day_r['final']-200_000:+,.0f}  MaxDD={day_r['max_dd']:.2f}%")
else:
    print("  0筆")
print("夜盤:")
if night_r and night_r['trades']:
    print(f"  {len(night_r['trades'])}筆  WR={night_r['wr']:.1f}%  PF={night_r['pf']:.3f}  "
          f"淨利={night_r['final']-200_000:+,.0f}  MaxDD={night_r['max_dd']:.2f}%")
else:
    print("  0筆")
print("="*60)
