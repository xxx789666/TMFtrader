"""
ORB 策略參數掃描 — 夜盤 MXF + QQQM 交叉驗證
============================================
Step 1: MXF 夜盤美股時段（21:30-04:00 TST）
Step 2: QQQM US session（14:30-21:00 UTC）

執行：
    cd TMFtrader-src
    python scripts/sweep_orb.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from core.logger import setup_logger
setup_logger(console_level='CRITICAL')

import pandas as pd
import numpy as np
from itertools import product
from collections import defaultdict
from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.orb import ORBStrategy

ROOT = Path(__file__).parent.parent

# ── 載入資料 ─────────────────────────────────────────────────────────────
print('Loading MXF night session data (21:30-04:00 TST)...')
CSV = ROOT / 'data' / 'historical' / 'tmf_20260411_full_1m.csv'
df_1m = pd.read_csv(CSV, parse_dates=['datetime']).sort_values('datetime').reset_index(drop=True)
df_raw = df_1m.set_index('datetime').resample('5min').agg(
    {'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
df_mxf = df_raw.between_time('21:30', '04:00').reset_index()
n_m_mxf = (df_mxf['datetime'].iloc[-1] - df_mxf['datetime'].iloc[0]).days / 30.44
print(f'  MXF night: {len(df_mxf):,} bars, {n_m_mxf:.1f} months')

print('Loading QQQM US session data...')
QQQM = ROOT / 'data' / 'historical' / 'ml' / 'QQQM_5m.parquet'
df_qqqm = pd.read_parquet(QQQM).reset_index()
if 'datetime' not in df_qqqm.columns:
    df_qqqm = df_qqqm.rename(columns={df_qqqm.columns[0]: 'datetime'})
df_qqqm['datetime'] = pd.to_datetime(df_qqqm['datetime'])
df_qqqm = df_qqqm.sort_values('datetime').reset_index(drop=True)
n_m_qqqm = (df_qqqm['datetime'].iloc[-1] - df_qqqm['datetime'].iloc[0]).days / 30.44
print(f'  QQQM:      {len(df_qqqm):,} bars, {n_m_qqqm:.1f} months')

print('Precomputing MXF indicators...')
ind_mxf = precompute_all(df_mxf, verbose=False)
print('  Done.')

# ── 固定參數 ─────────────────────────────────────────────────────────────
BASE_MXF = dict(
    point_value=10.0, max_loss_twd=4000.0,
    tp_atr=10.0, early_cut_loss_atr=1.5,
    allow_both_directions=True, trend_filter=False,
    session_start=(21, 30), force_close_time=(4, 0),
)

# QQQM：Stage B 僅用於方向對齊驗證（不直接跑 ORB）
# 原因：QQQM ORB 產生假突破（WR~6%），不適合直接驗證；
# 改為計算 MXF ORB 信號方向 vs QQQM 當日開→收方向的一致率

# ── 掃描格 ───────────────────────────────────────────────────────────────
GRID = {
    'orb_minutes':       [20, 30, 45],
    'entry_filter_atr':  [0.0, 0.1, 0.2],
    'sl_type':           ['atr', 'range'],
    'sl_atr':            [1.5, 2.0, 2.5],   # sl_type='atr' 時
    'sl_range_buf':      [0.2, 0.4],         # sl_type='range' 時
    'trail_trigger_atr': [0.8, 1.0, 1.2],
    'trail_dist_atr':    [1.0, 1.25],
    'adx_min':           [0, 15],
    'max_bars':          [48, 60],           # 4h / 5h
}

# 為避免組合爆炸，分兩階段
# Stage A: orb_minutes × entry_filter × sl × trail_trigger × adx (固定 trail_dist + max_bars)
combos_a = list(product(
    GRID['orb_minutes'],
    GRID['entry_filter_atr'],
    GRID['sl_type'],
    GRID['sl_atr'],
    GRID['trail_trigger_atr'],
    GRID['adx_min'],
))
print(f'\nStage A: {len(combos_a)} combos')
print(f'  Fixed: trail_dist=1.25, max_bars=60, sl_range_buf=0.3')

# ── 評分函數 ─────────────────────────────────────────────────────────────
def score(r):
    if r['nm'] < 3:        return -999
    if r['wr'] < 0.50:     return -999
    if r['pf'] < 1.20:     return -999
    if r['max_dd'] > 30:   return -999
    freq_s = min(r['nm'] / 8.0, 1.0)           # 目標 8/月 → 1.0
    wr_s   = min((r['wr'] - 0.50) / 0.20, 1.0)
    pf_s   = min((r['pf'] - 1.0) / 2.0, 1.0)
    dd_s   = max(0.0, 1.0 - r['max_dd'] / 30.0)
    return freq_s * 0.35 + wr_s * 0.35 + pf_s * 0.20 + dd_s * 0.10

def run_backtest(df, ind, params, instrument='TMF', n_months=1.0):
    strat  = ORBStrategy(**params)
    engine = FastBacktestEngine(initial_balance=200_000, instrument=instrument)
    result = engine.run(df, ind, strat, 'tmf_3x')
    trades = result.trades
    n = len(trades)
    if n == 0:
        return None
    wins   = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    gp = sum(t['pnl'] for t in wins)
    gl = abs(sum(t['pnl'] for t in losses)) or 1e-9
    pf = gp / gl
    wr = len(wins) / n
    net = result.final_balance - 200_000
    eq = result.equity_curve; peak=eq[0]; max_dd=0.0
    for v in eq:
        if v > peak: peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd: max_dd = dd
    nm = n / n_months
    return dict(n=n, nm=nm, wr=wr, pf=pf, net=net, max_dd=max_dd)

# ── QQQM 日方向對照表（每個交易日開盤→收盤方向）──────────────────────
def build_qqqm_daily_dir(df_qqqm):
    """
    計算 QQQM 每個 UTC+2 交易日的方向（開盤→收盤）。
    返回 {date: 'up'/'down'} 字典。
    """
    df = df_qqqm.copy()
    df['date'] = df['datetime'].dt.date
    daily_open  = df.groupby('date')['open'].first()
    daily_close = df.groupby('date')['close'].last()
    result = {}
    for d in daily_open.index:
        result[d] = 'up' if daily_close[d] >= daily_open[d] else 'down'
    return result

_qqqm_daily_dir = None  # 全局快取

def qqqm_direction_alignment(df_mxf, ind_mxf, params):
    """
    跑 MXF ORB，對每筆交易查 QQQM 當日方向，計算方向一致率。
    MXF 夜盤 entry 日期 → QQQM UTC+2 交易日：
      21:30-23:59 TST = QQQM 同一日
      00:00-04:00 TST = QQQM 前一日
    隨機基準 ≈ 50%，有效信號目標 ≥ 55%
    """
    global _qqqm_daily_dir
    import datetime as _dt
    strat  = ORBStrategy(**params)
    engine = FastBacktestEngine(initial_balance=200_000, instrument='TMF')
    result = engine.run(df_mxf, ind_mxf, strat, 'tmf_3x')
    trades = result.trades
    if not trades:
        return None

    aligned = 0; total = 0
    for t in trades:
        try:
            entry_dt = pd.Timestamp(t['entry_time'])
            entry_time = entry_dt.time()
            if entry_time >= _dt.time(21, 30):
                us_date = entry_dt.date()
            else:  # 00:00-04:00 TST → 前一日是 QQQM 的交易日
                us_date = (entry_dt - pd.Timedelta(days=1)).date()
            if us_date in _qqqm_daily_dir:
                qqqm_dir = _qqqm_daily_dir[us_date]
                mxf_dir  = 'up' if t['side'] == 'long' else 'down'
                if qqqm_dir == mxf_dir:
                    aligned += 1
                total += 1
        except Exception:
            pass

    if total == 0:
        return None
    return dict(n=total, alignment=aligned/total)

# ── Stage A 掃描 ─────────────────────────────────────────────────────────
print('\n正在掃描...')
results_a = []

for i, (om, ef, slt, sl, trig, adx) in enumerate(combos_a, 1):
    p_mxf = {**BASE_MXF,
        'orb_minutes': om, 'entry_filter_atr': ef,
        'sl_type': slt, 'sl_atr': sl, 'sl_range_buffer': 0.3,
        'trail_trigger_atr': trig, 'trail_dist_atr': 1.25,
        'adx_min': adx, 'max_bars': 60, 'early_cut_bars': 30,
    }
    r_mxf = run_backtest(df_mxf, ind_mxf, p_mxf, 'TMF', n_m_mxf)
    if r_mxf is None:
        continue

    s = score(r_mxf)
    results_a.append(dict(
        om=om, ef=ef, slt=slt, sl=sl, trig=trig, adx=adx,
        **r_mxf, score=s,
    ))

    if i % 100 == 0:
        print(f'  [{i}/{len(combos_a)}] ...')

print(f'  掃描完成，{len(results_a)} 組有效結果')

# ── 結果輸出 ─────────────────────────────────────────────────────────────
HDR = (f"{'om':>3} {'ef':>4} {'slt':>5} {'sl':>4} {'tri':>4} {'adx':>3} | "
       f"{'N':>4} {'N/m':>4} {'WR':>6} {'PF':>6} {'MaxDD':>7} {'Net':>9}")
SEP = '-' * 72

# Top 20 by score
scored = sorted([r for r in results_a if r['score'] > -999], key=lambda x: -x['score'])
print(f'\n{"="*72}')
print('▶ MXF 夜盤 — Top 20 綜合評分（頻率0.35+WR0.35+PF0.2+DD0.1）')
print(f'{"="*72}')
print(HDR); print(SEP)
for r in scored[:20]:
    print(f"{r['om']:>3} {r['ef']:>4.1f} {r['slt']:>5} {r['sl']:>4.1f} {r['trig']:>4.1f} {r['adx']:>3d} | "
          f"{r['n']:>4} {r['nm']:>4.1f} {r['wr']:>5.1%} {r['pf']:>6.3f} {r['max_dd']:>6.1f}% {r['net']:>+9,.0f}  s={r['score']:.3f}")

# 頻率分布
print(f'\n{"="*72}')
print('▶ 頻率分布統計')
print(f'{"="*72}')
for lo, hi in [(0,3),(3,5),(5,8),(8,12),(12,99)]:
    sub = [r for r in results_a if lo <= r['nm'] < hi]
    if sub:
        avg_wr = sum(r['wr'] for r in sub) / len(sub)
        avg_pf = sum(r['pf'] for r in sub) / len(sub)
        print(f'  {lo:>2}-{hi:<3}/月: {len(sub):>4} 組  avgWR={avg_wr:.1%}  avgPF={avg_pf:.2f}')

# ── Stage B: QQQM 方向對齊驗證（Top 5）──────────────────────────────────
# 邏輯：每筆 MXF ORB 信號 → 查 QQQM 當日開→收方向 → 計算一致率
# 隨機基準 ≈ 50%；有效 ≥ 55%；強 ≥ 60%
import datetime as _dt

if scored:
    qqqm_daily_dir = build_qqqm_daily_dir(df_qqqm)
    top5 = scored[:5]
    print(f'\n{"="*72}')
    print('▶ QQQM 方向對齊驗證（Top 5 — MXF ORB 信號 vs QQQM 當日走向）')
    print('  一致率基準：隨機≈50%，有效≥55%✓，強≥60%★')
    print(f'{"="*72}')
    print(f"  {'om':>3} {'ef':>4} {'slt':>5} {'sl':>4} {'tri':>4} | "
          f"{'MXF N/m':>8} {'MXF WR':>7} {'MXF PF':>7} | "
          f"{'對齊N':>6} {'對齊率':>7}")
    print('  ' + '-'*72)

    for r in top5:
        p2 = {**BASE_MXF,
            'orb_minutes': r['om'], 'entry_filter_atr': r['ef'],
            'sl_type': r['slt'], 'sl_atr': r['sl'], 'sl_range_buffer': 0.3,
            'trail_trigger_atr': r['trig'], 'trail_dist_atr': 1.25,
            'adx_min': r['adx'], 'max_bars': 60, 'early_cut_bars': 30,
        }
        strat2  = ORBStrategy(**p2)
        engine2 = FastBacktestEngine(initial_balance=200_000, instrument='TMF')
        t2list  = engine2.run(df_mxf, ind_mxf, strat2, 'tmf_3x').trades
        aligned = 0; total = 0
        for t in t2list:
            try:
                edt  = pd.Timestamp(t['entry_time'])
                etm  = edt.time()
                usd  = edt.date() if etm >= _dt.time(21, 30) else (edt - pd.Timedelta(days=1)).date()
                if usd in qqqm_daily_dir:
                    if qqqm_daily_dir[usd] == ('up' if t['side']=='long' else 'down'):
                        aligned += 1
                    total += 1
            except Exception:
                pass
        if total:
            al = aligned / total
            flag = '★' if al >= 0.60 else ('✓' if al >= 0.55 else ('△' if al >= 0.50 else '✗'))
            print(f"  {r['om']:>3} {r['ef']:>4.1f} {r['slt']:>5} {r['sl']:>4.1f} {r['trig']:>4.1f} | "
                  f"{r['nm']:>8.1f} {r['wr']:>7.1%} {r['pf']:>7.3f} | "
                  f"{total:>6} {al:>7.1%} {flag}")
        else:
            print(f"  {r['om']:>3} {r['ef']:>4.1f} {r['slt']:>5} {r['sl']:>4.1f} {r['trig']:>4.1f} | "
                  f"{r['nm']:>8.1f} {r['wr']:>7.1%} {r['pf']:>7.3f} | "
                  f"  無法對齊")

# ── 最終建議 ────────────────────────────────────────────────────────────
best = scored[0] if scored else None
print(f'\n{"="*72}')
print('▶ 最終建議（ORB 夜盤 v1 候選）')
print(f'{"="*72}')
if best and best['score'] > -999:
    print(f'''  params:
    orb_minutes       = {best["om"]}
    entry_filter_atr  = {best["ef"]}
    sl_type           = {best["slt"]}
    sl_atr            = {best["sl"]}
    trail_trigger_atr = {best["trig"]}
    adx_min           = {best["adx"]}
    trail_dist_atr    = 1.25
    max_bars          = 60
    force_close_time  = (4, 0)

  預期 MXF 夜盤表現:
    N/月  = {best["nm"]:.1f}
    WR    = {best["wr"]:.1%}
    PF    = {best["pf"]:.3f}
    MaxDD = {best["max_dd"]:.1f}%
    Net   = {best["net"]:+,.0f} TWD ({n_m_mxf:.0f}m)

  日盤 + 夜盤合計預期:
    日盤 (v6b)  ≈ 1.9 筆/月
    夜盤 (ORB)  ≈ {best["nm"]:.1f} 筆/月
    合計        ≈ {1.9 + best["nm"]:.1f} 筆/月  ← 目標 8-10 筆
''')
else:
    print('  [未找到符合條件的組合]')
    print('  建議: 降低品質門檻 或 加入 TMF 夜盤多一段資料')
