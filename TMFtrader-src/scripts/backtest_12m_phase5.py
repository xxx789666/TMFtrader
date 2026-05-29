"""
2025-04 ~ 2026-04 完整 12 個月回測（Phase 5 優化版）
夜盤：ML 停用, trail_dist=0.3, max_sl_pts=120
日盤：BreakoutTrend v6b
"""
import sys, warnings
warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')

from core.logger import setup_logger
setup_logger(console_level='CRITICAL')

import pandas as pd
import numpy as np
from datetime import time
from pathlib import Path

# ── 資料載入 ──────────────────────────────────────────
print("載入資料...")
df_1m = pd.read_parquet('data/historical/tmf_5y_1m.parquet')
df_1m['datetime'] = pd.to_datetime(df_1m['datetime'])
df_5m_all = (
    df_1m.set_index('datetime')
    .resample('5min')
    .agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'})
    .dropna().reset_index()
)

ML_DIR = Path('data/historical/ml')
df_night_5m = pd.read_parquet(ML_DIR / 'TMF_night_5m.parquet')
df_night_5m['datetime'] = pd.to_datetime(df_night_5m['datetime'])
feat_df = pd.read_parquet(ML_DIR / 'TMF_night_features.parquet')

print("產生 Phase 5 夜盤信號（全段）...")
from optimizer.ml.labels_orb import detect_and_label_orb, INSTRUMENT_ORB_CONFIGS
cfg = INSTRUMENT_ORB_CONFIGS['TMF']
signals_df = detect_and_label_orb(df_night_5m, feat_df, 'TMF_night', cfg)
signals_df['entry_time'] = pd.to_datetime(signals_df['entry_time'])

# ── 日盤引擎 ──────────────────────────────────────────
from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.breakout import BreakoutTrendStrategy

DAY_PARAMS = dict(
    sl_atr=2.5, tp_atr=10.0,
    trail_trigger_atr=1.75, trail_dist_atr=1.75, max_bars=30,
    min_adx=20.0, min_di_gap=5.0,
    squeeze_ratio=0.78, expand_ratio=1.08,
    min_vol_ratio=1.15, pullback_ema_gap=0.3,
    afternoon_min_adx=32.0, breakeven_trigger_atr=999,
)

RISK_PCT = 0.04
MAX_QTY  = 3
PV       = 10.0
INIT     = 200_000.0

def run_day(start, end):
    df = df_5m_all[
        (df_5m_all['datetime'] >= start) &
        (df_5m_all['datetime'] <= end + ' 23:59') &
        df_5m_all['datetime'].dt.time.apply(lambda t: time(8,45) <= t <= time(13,30))
    ].copy().reset_index(drop=True)
    if len(df) < 10:
        return []
    ind  = precompute_all(df, verbose=False)
    st   = BreakoutTrendStrategy(**DAY_PARAMS)
    eng  = FastBacktestEngine(initial_balance=INIT, instrument='TMF')
    res  = eng.run(df, ind, st, 'tmf_3x')
    return res.trades

def run_night(start, end, equity=INIT):
    df = signals_df[
        (signals_df['entry_time'] >= start) &
        (signals_df['entry_time'] <  end + ' 23:59')
    ].copy().reset_index(drop=True)
    if 'f_vol_ratio' in df.columns:
        df = df[df['f_vol_ratio'] <= 2.0].reset_index(drop=True)
    pnl_list, qty_list = [], []
    eq = equity
    for _, r in df.iterrows():
        sd = abs(float(r['entry_price']) - float(r['stop_price']))
        if sd < 1: sd = float(r.get('atr_at_entry', 60.0)) * 2.0
        qty = min(MAX_QTY, max(1, int(eq * RISK_PCT / (sd * PV))))
        pnl = (float(r['exit_price']) - float(r['entry_price'])) * int(r['direction']) * qty * PV
        pnl_list.append(pnl); qty_list.append(qty); eq += pnl
    df['qty']     = qty_list if qty_list else np.array([], dtype=int)
    df['pnl_twd'] = pnl_list if pnl_list else np.array([], dtype=float)
    return df

def calc(arr, init=INIT):
    n = len(arr)
    if n == 0: return dict(n=0,w=0,net=0,pf=0,dd=0,pct=0)
    arr = np.asarray(arr, dtype=float)
    wins = int((arr>0).sum())
    gp = arr[arr>0].sum(); gl = abs(arr[arr<0].sum()) or 1e-6
    net = arr.sum()
    eq  = np.concatenate([[init], init + np.cumsum(arr)])
    pk  = np.maximum.accumulate(eq)
    dd  = float(((pk-eq)/pk*100).max())
    return dict(n=n, w=wins, net=net, pf=gp/gl, dd=dd, pct=net/init*100)

# ── 月份清單 ──────────────────────────────────────────
import calendar
MONTHS = []
for y, m in [(2025,4),(2025,5),(2025,6),(2025,7),(2025,8),(2025,9),
             (2025,10),(2025,11),(2025,12),(2026,1),(2026,2),(2026,3),(2026,4)]:
    last = calendar.monthrange(y,m)[1]
    MONTHS.append((f'{y}-{m:02d}-01', f'{y}-{m:02d}-{last:02d}', f'{y}/{m:02d}'))

dir_map = {1:'LONG', -1:'SHORT'}

# ── 累計統計 ──────────────────────────────────────────
all_day_pnl   = []
all_night_pnl = []

# ════════════════════════════════════════════════════════
print()
print("=" * 75)
print("  2025-04 ~ 2026-04  Phase 5 完整回測（夜盤 ML 停用）")
print("=" * 75)

monthly_summary = []

for (start, end, label) in MONTHS:
    day_trades  = run_day(start, end)
    night_df    = run_night(start, end)
    day_pnl     = np.array([t['pnl'] for t in day_trades]) if day_trades else np.array([])
    night_pnl   = night_df['pnl_twd'].values if len(night_df) > 0 else np.array([])
    ds = calc(day_pnl)
    ns = calc(night_pnl)
    all_day_pnl.extend(day_pnl.tolist())
    all_night_pnl.extend(night_pnl.tolist())

    combined_net = ds['net'] + ns['net']
    monthly_summary.append((label, ds, ns, combined_net))

    print(f"\n{'━'*75}")
    print(f"  ◆ {label}  日盤:{ds['n']}筆/{ds['net']:+,.0f}  夜盤:{ns['n']}筆/{ns['net']:+,.0f}  合計:{combined_net:+,.0f} TWD")
    print(f"{'━'*75}")

    # 日盤明細
    print(f"  【日盤 BreakoutTrend】", end='')
    if ds['n']:
        print(f"  {ds['n']}筆  WR:{ds['w']/ds['n']*100:.0f}%  PF:{ds['pf']:.3f}  MaxDD:{ds['dd']:.2f}%")
        print(f"  {'日期':<17} {'方向':<6} {'口':<3} {'進場':>8} {'出場':>8}  {'原因':<16} {'損益':>10}")
        print(f"  {'─'*68}")
        for t in day_trades:
            d = t.get('side', t.get('direction','?')).upper()
            r = t.get('reason','')[:14]
            print(f"  {t['entry_time'][:16]:<17} {d:<6} {t.get('quantity',1)}口  {t['entry_price']:>8,.0f} {t['exit_price']:>8,.0f}  {r:<16} {t['pnl']:>+10,.0f}")
    else:
        print("  無交易")

    # 夜盤明細
    print(f"  【夜盤 ORB Phase5】", end='')
    raw_cnt = len(signals_df[(signals_df['entry_time']>=start)&(signals_df['entry_time']<end+' 23:59')])
    if ns['n']:
        print(f"  {ns['n']}筆（原始{raw_cnt}→vol過濾後{ns['n']}）  WR:{ns['w']/ns['n']*100:.0f}%  PF:{ns['pf']:.3f}  MaxDD:{ns['dd']:.2f}%")
        print(f"  {'日期':<17} {'方向':<6} {'口':<3} {'進場':>8} {'出場':>8}  {'出場原因':<18} {'損益':>10}")
        print(f"  {'─'*68}")
        for _, r in night_df.iterrows():
            d = dir_map.get(int(r['direction']),'?')
            ex = str(r.get('exit_reason', r.get('label','')))
            ex = ex.replace('trail_stop','追蹤止損').replace('stop_loss','SL').replace('force_close','強制平倉').replace('early_cut','早切').replace('time_exit','時間出場').replace('tp','TP')[:16]
            print(f"  {str(r['entry_time'])[:16]:<17} {d:<6} {int(r['qty'])}口  {r['entry_price']:>8,.0f} {r['exit_price']:>8,.0f}  {ex:<18} {r['pnl_twd']:>+10,.0f}")
    else:
        print(f"  無交易（原始{raw_cnt}筆，全被過濾）")

# ════════════════════════════════════════════════════════
# 月度總覽表
# ════════════════════════════════════════════════════════
print()
print("=" * 75)
print("  月度總覽")
print("=" * 75)
print(f"  {'月份':<9} {'日筆':<5} {'日損益':>9} {'夜筆':<5} {'夜損益':>9}  {'夜WR':<7} {'夜PF':<7} {'合計':>10}")
print(f"  {'─'*70}")
for (label, ds, ns, cnet) in monthly_summary:
    nwr  = f"{ns['w']/ns['n']*100:.0f}%" if ns['n'] else 'N/A'
    npf  = f"{ns['pf']:.2f}" if ns['n'] else 'N/A'
    flag = '✓' if cnet >= 0 else '✗'
    print(f"  {label:<9} {ds['n']:<5} {ds['net']:>+9,.0f} {ns['n']:<5} {ns['net']:>+9,.0f}  {nwr:<7} {npf:<7} {cnet:>+10,.0f} {flag}")

# ════════════════════════════════════════════════════════
# 總績效
# ════════════════════════════════════════════════════════
day_all   = np.array(all_day_pnl)
night_all = np.array(all_night_pnl)
ds_all    = calc(day_all)
ns_all    = calc(night_all)
total_net = ds_all['net'] + ns_all['net']

print()
print("=" * 75)
print("  2025-04 ~ 2026-04  13 個月總績效")
print("=" * 75)
print(f"  {'策略':<16} {'筆數':<6} {'勝率':<8} {'PF':<8} {'MaxDD':<8} {'淨利 TWD':>12} {'報酬率':>8}")
print(f"  {'─'*66}")
def fmt(s, lbl):
    wr  = f"{s['w']/s['n']*100:.1f}%" if s['n'] else 'N/A'
    pf  = f"{s['pf']:.3f}" if s['n'] else 'N/A'
    dd  = f"{s['dd']:.2f}%" if s['n'] else 'N/A'
    print(f"  {lbl:<16} {s['n']:<6} {wr:<8} {pf:<8} {dd:<8} {s['net']:>+12,.0f} {s['pct']:>+7.2f}%")
fmt(ds_all, '日盤 Breakout')
fmt(ns_all, '夜盤 ORB P5')

# 合計 equity curve
eq_comb  = INIT + np.cumsum(day_all.tolist() + night_all.tolist()) if (len(day_all)+len(night_all)) else np.array([INIT])
pk_comb  = np.maximum.accumulate(np.concatenate([[INIT], eq_comb]))
dd_comb  = float(((pk_comb - np.concatenate([[INIT], eq_comb])) / pk_comb * 100).max())
print(f"  {'─'*66}")
print(f"  {'合計':<16} {ds_all['n']+ns_all['n']:<6} {'─':<8} {'─':<8} {dd_comb:<7.2f}% {total_net:>+12,.0f} {total_net/INIT*100:>+7.2f}%")
print()
print(f"  平均月報酬（合計）：{total_net/INIT/13*100:+.2f}%/月")
print("=" * 75)
