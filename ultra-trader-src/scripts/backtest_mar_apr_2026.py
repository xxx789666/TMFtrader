"""
2026年 3月+4月 雙策略回測（Phase 5 優化版）
夜盤：ML 停用，trail_dist=0.3, max_sl_pts=120（Phase 5 新參數）
日盤：BreakoutTrend v6b（不變）
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
from pathlib import Path

# ════════════════════════════════════════════════════════
# 載入 1m → 重採樣 5m
# ════════════════════════════════════════════════════════
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

PERIODS = [
    ('2026-03-01', '2026-03-31', '3月'),
    ('2026-04-01', '2026-04-30', '4月'),
]

RISK_PCT      = 0.04
MAX_CONTRACTS = 3
POINT_VALUE   = 10.0

# ════════════════════════════════════════════════════════
# 日盤 BreakoutTrend
# ════════════════════════════════════════════════════════
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

def run_day_backtest(start, end):
    df_day = df_5m_all[
        (df_5m_all['datetime'] >= start) &
        (df_5m_all['datetime'] <= end + ' 23:59') &
        df_5m_all['datetime'].dt.time.apply(lambda t: time(8, 45) <= t <= time(13, 30))
    ].copy().reset_index(drop=True)
    if len(df_day) < 10:
        return [], 200_000.0
    ind   = precompute_all(df_day, verbose=False)
    strat = BreakoutTrendStrategy(**DAY_PARAMS)
    eng   = FastBacktestEngine(initial_balance=200_000, instrument='TMF')
    res   = eng.run(df_day, ind, strat, 'tmf_3x')
    return res.trades, res.final_balance

# ════════════════════════════════════════════════════════
# 夜盤 ORB — Phase 5（ML 停用，用 detect_and_label_orb）
# ════════════════════════════════════════════════════════
from optimizer.ml.labels_orb import detect_and_label_orb, INSTRUMENT_ORB_CONFIGS

print("產生夜盤 Phase 5 信號（TMF_night_5m + features parquet）...")
ML_DIR = Path('data/historical/ml')
df_night_5m = pd.read_parquet(ML_DIR / 'TMF_night_5m.parquet')
df_night_5m['datetime'] = pd.to_datetime(df_night_5m['datetime'])
feat_df = pd.read_parquet(ML_DIR / 'TMF_night_features.parquet')
cfg = INSTRUMENT_ORB_CONFIGS['TMF']
signals_df = detect_and_label_orb(df_night_5m, feat_df, 'TMF_night', cfg)
signals_df['entry_time'] = pd.to_datetime(signals_df['entry_time'])

def run_night_backtest(start, end, init_equity=200_000.0):
    df_m = signals_df[
        (signals_df['entry_time'] >= start) &
        (signals_df['entry_time'] <  end + ' 23:59')
    ].copy().reset_index(drop=True)

    # vol_ratio 過濾（Layer 1 — 已在 detect_and_label_orb 處理，但保險再過一次）
    if 'f_vol_ratio' in df_m.columns:
        df_m = df_m[df_m['f_vol_ratio'] <= 2.0].reset_index(drop=True)

    equity = init_equity
    pnl_list, qty_list = [], []
    for _, row in df_m.iterrows():
        stop_dist = abs(float(row['entry_price']) - float(row['stop_price']))
        if stop_dist < 1e-6:
            stop_dist = float(row.get('atr_at_entry', 60.0)) * 2.0
        qty = min(MAX_CONTRACTS, max(1, int(equity * RISK_PCT / (stop_dist * POINT_VALUE))))
        pnl = (float(row['exit_price']) - float(row['entry_price'])) * int(row['direction']) * qty * POINT_VALUE
        pnl_list.append(pnl)
        qty_list.append(qty)
        equity += pnl

    df_m['qty']     = qty_list if qty_list else pd.Series([], dtype=int)
    df_m['pnl_twd'] = pnl_list if pnl_list else pd.Series([], dtype=float)
    return df_m

# ════════════════════════════════════════════════════════
# 輸出
# ════════════════════════════════════════════════════════
def stats(pnl_arr, init=200_000.0):
    n = len(pnl_arr)
    if n == 0:
        return 0, 0, 0.0, 0.0, 0.0, 0.0
    wins   = int((pnl_arr > 0).sum())
    gp     = float(pnl_arr[pnl_arr > 0].sum())
    gl     = float(abs(pnl_arr[pnl_arr < 0].sum())) or 1e-6
    net    = float(pnl_arr.sum())
    pf     = gp / gl
    eq     = np.concatenate([[init], init + np.cumsum(pnl_arr)])
    peak   = np.maximum.accumulate(eq)
    maxdd  = float(((peak - eq) / peak * 100).max())
    return n, wins, net, pf, maxdd, net/init*100

print()
print("=" * 70)
print("  2026年 3月 + 4月 雙策略回測（夜盤 Phase 5 優化版）")
print("=" * 70)

dir_map = {1: 'LONG', -1: 'SHORT'}

for (start, end, label) in PERIODS:
    print(f"\n{'━'*70}")
    print(f"  ◆ {label}（{start} ~ {end}）")
    print(f"{'━'*70}")

    # 日盤
    day_trades, day_final = run_day_backtest(start, end)
    day_pnl = np.array([t['pnl'] for t in day_trades]) if day_trades else np.array([])
    dn, dw, dnet, dpf, ddd, dpct = stats(day_pnl)

    print(f"\n  【日盤 BreakoutTrend】")
    if dn > 0:
        print(f"  交易：{dn} 筆  勝率：{dw/dn*100:.1f}%（{dw}勝{dn-dw}敗）  PF：{dpf:.3f}  淨利：{dnet:+,.0f} TWD（{dpct:+.2f}%）  MaxDD：{ddd:.2f}%")
        print()
        print(f"  {'日期':<18} {'方向':<6} {'口':<3} {'進場':>8} {'出場':>8}  {'原因':<16} {'損益':>10}")
        print(f"  {'─'*68}")
        for t in day_trades:
            d = t.get('side', t.get('direction', '?')).upper()
            reason = t.get('reason', '')[:14]
            print(f"  {t['entry_time'][:16]:<18} {d:<6} {t.get('quantity',1)}口  {t['entry_price']:>8,.0f} {t['exit_price']:>8,.0f}  {reason:<16} {t['pnl']:>+10,.0f}")
    else:
        print(f"  本月無交易")

    # 夜盤
    night_df = run_night_backtest(start, end)
    nn = len(night_df)
    night_pnl = night_df['pnl_twd'].values if nn > 0 else np.array([])
    nn2, nw, nnet, npf, ndd, npct = stats(night_pnl)

    print(f"\n  【夜盤 ORB Phase 5（ML停用）】")
    # 顯示原始信號數
    raw_m = signals_df[(signals_df['entry_time'] >= start) & (signals_df['entry_time'] < end + ' 23:59')]
    print(f"  原始信號：{len(raw_m)} 筆 → Layer1 vol_ratio≤2.0 通過：{nn} 筆")
    if nn > 0:
        print(f"  交易：{nn} 筆  勝率：{nw/nn*100:.1f}%（{nw}勝{nn-nw}敗）  PF：{npf:.3f}  淨利：{nnet:+,.0f} TWD（{npct:+.2f}%）  MaxDD：{ndd:.2f}%")
        print()
        print(f"  {'日期':<18} {'方向':<6} {'口':<3} {'進場':>8} {'出場':>8}  {'出場原因':<18} {'損益':>10}")
        print(f"  {'─'*72}")
        for _, row in night_df.iterrows():
            d = dir_map.get(int(row['direction']), '?')
            reason = str(row.get('exit_reason', row.get('label', ''))).replace('trail_stop', '追蹤止損').replace('stop_loss', 'SL').replace('force_close', '強制平倉').replace('early_cut', '早切').replace('time_exit', '時間出場').replace('tp', 'TP')[:16]
            print(f"  {str(row['entry_time'])[:16]:<18} {d:<6} {int(row['qty'])}口  {row['entry_price']:>8,.0f} {row['exit_price']:>8,.0f}  {reason:<18} {row['pnl_twd']:>+10,.0f}")
    else:
        print(f"  本月無交易（信號被過濾）")

    cnet = dnet + nnet
    print(f"\n  {'─'*70}")
    print(f"  {label} 合計（日+夜）：{cnet:+,.0f} TWD  ({cnet/200_000*100:+.2f}%)")

print()
print("=" * 70)
print("  回測完成")
print("=" * 70)
