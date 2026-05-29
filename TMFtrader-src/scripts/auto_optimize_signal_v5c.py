"""
auto_optimize_signal_v5c.py — v5 第三輪細化

v5 確認參數：expand_ratio=1.18, pullback_ema_gap=0.20, min_di_gap=10.0
v5 績效：n=96  WR=60.4%  PF=2.480  net=+203,430  RR=1.625  N6=8/28

本輪細化維度（v5 基礎上再找突破）：
  Phase 1 — trail_dist_atr 精細掃描
            v4b=1.25，在 v5 高品質信號基礎上，追蹤距離可能有最優解
            [0.80, 0.90, 1.00, 1.10, 1.15, 1.20, 1.25, 1.30, 1.35, 1.40, 1.50]

  Phase 2 — afternoon_min_adx × min_adx 精細掃描
            v5=32/20，更嚴格的 ADX 門檻配合高品質突破
            afternoon=[28, 30, 32, 34, 36, 38]
            min_adx=[18, 20, 22, 24]

  Phase 3 — trail_trigger_atr 精細掃描（固定最佳 trail_dist）
            v4b=1.2，在 v5 基礎上追蹤啟動點重新最佳化
            [0.8, 1.0, 1.1, 1.2, 1.3, 1.5, 1.8]

  Phase 4 — 聯合驗證（最佳 trail 組合 × 最佳 ADX 組合）
"""

import sys, time, json
from pathlib import Path
from itertools import product

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

from core.logger import setup_logger
setup_logger(console_level='CRITICAL')

import pandas as pd
from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.breakout import BreakoutTrendStrategy

# ── v5 基準（固定）───────────────────────────────────────────────────────────
BASE = dict(
    sl_atr=2.5, tp_atr=10.0,
    trail_trigger_atr=1.2, trail_dist_atr=1.25,
    max_bars=80,
    min_adx=20.0, afternoon_min_adx=32.0,
    min_di_gap=10.0,
    squeeze_ratio=0.90, expand_ratio=1.18,
    min_vol_ratio=1.0,
    pullback_ema_gap=0.20,
    breakeven_trigger_atr=999,
    early_cut_bars=30, early_cut_loss_atr=1.5,
    max_loss_twd=4000.0,
    trend_filter=True, ema200_margin_atr=0.0,
    use_momentum_score=True,
    momentum_rsi_bull=52.0, momentum_rsi_bear=46.0,
    momentum_session_atr=0.5, point_value=10.0,
    scale_out_trigger_atr=0.0, scale_out_qty=1,
)

BM = dict(n=96, wr=60.4, pf=2.480, net=203430, rr=1.625, n6=8)

INSTRUMENT      = 'TMF'
INITIAL_BALANCE = 200_000
RISK_PROFILE    = 'tmf_3x'

# ── 掃描空間 ──────────────────────────────────────────────────────────────────
TRAIL_DIST_VALS   = [0.80, 0.90, 1.00, 1.10, 1.15, 1.20, 1.25, 1.30, 1.35, 1.40, 1.50]
AFT_ADX_VALS      = [28.0, 30.0, 32.0, 34.0, 36.0, 38.0]
MIN_ADX_VALS      = [18.0, 20.0, 22.0, 24.0]
TRAIL_TRIG_VALS   = [0.8, 1.0, 1.1, 1.2, 1.3, 1.5, 1.8]


def run_backtest(df_5m, ind, params: dict) -> dict | None:
    try:
        strat  = BreakoutTrendStrategy(**params)
        engine = FastBacktestEngine(initial_balance=INITIAL_BALANCE, instrument=INSTRUMENT)
        r      = engine.run(df_5m, ind, strat, RISK_PROFILE)
        trades = r.trades
        n      = len(trades)
        if n == 0:
            return None
        wins   = [t for t in trades if t['pnl'] > 0]
        losses = [t for t in trades if t['pnl'] <= 0]
        gp = sum(t['pnl'] for t in wins)
        gl = abs(sum(t['pnl'] for t in losses)) if losses else 1e-9
        aw = gp / len(wins)  if wins   else 0.0
        al = gl / len(losses) if losses else 1e-9
        net = r.final_balance - r.initial_balance
        monthly: dict[str, float] = {}
        for t in trades:
            ym = t['exit_time'][:7]
            monthly[ym] = monthly.get(ym, 0) + t['pnl']
        n6 = sum(1 for v in monthly.values() if v >= INITIAL_BALANCE * 0.06)
        return dict(n=n, wr=round(len(wins)/n*100, 1),
                    pf=round(gp/gl, 3), net=round(net, 0),
                    rr=round(aw/al, 3), n6=n6)
    except Exception as e:
        print(f'  [ERR] {e}')
        return None


def score(m: dict) -> float:
    if m is None: return -99.0
    net_s = (m['net'] - 180_000) / 80_000
    rr_s  = (m['rr']  - 1.4) / 0.5
    wr_s  = (m['wr']  - 55.0) / 10.0
    pf_s  = (m['pf']  - 1.8) / 0.8
    n6_s  = m['n6'] / 10.0
    s = rr_s * 3.0 + net_s * 2.5 + n6_s * 3.0 + wr_s * 1.0 + pf_s * 0.5
    if m['wr']  < 50.0: s -= 3.0
    if m['pf']  < 1.5:  s -= 3.0
    if m['n']   < 60:   s -= 2.0
    return round(s, 4)


def is_better(m: dict) -> bool:
    """嚴格優於 v5 基準"""
    return (m['net'] >= BM['net'] - 2000
            and m['rr']  >= BM['rr']  - 0.01
            and m['wr']  >= BM['wr']  - 0.5
            and m['pf']  >= BM['pf']  - 0.05
            and m['n6']  >= BM['n6']  - 1)


def pr(label, m, s):
    if m is None:
        print(f'  {label:<45}  [None]')
        return
    flag = ' ★' if is_better(m) else ''
    print(f'  {label:<45}  n={m["n"]:3d} wr={m["wr"]:.1f}% pf={m["pf"]:.3f}'
          f'  net={m["net"]:+,.0f} rr={m["rr"]:.3f} n6={m["n6"]}'
          f'  s={s:+.3f}{flag}')


def top_n(results, n=5):
    return sorted(results, key=lambda x: -x[-1])[:n]


def main():
    data_path = ROOT / 'data' / 'historical' / 'tmf_20260411_full_1m.csv'
    print('=' * 90)
    print('  v5 第三輪細化 — trail 距離 / ADX 門檻 / trail 觸發點')
    print('=' * 90)

    print('[INIT] 載入資料...')
    df_1m = pd.read_csv(data_path, parse_dates=['datetime']).sort_values('datetime').reset_index(drop=True)
    df    = df_1m.set_index('datetime')
    df_5m = df.resample('5min').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
    df_5m = df_5m.between_time('08:45','13:30').reset_index()
    print(f'  5分鐘K線: {len(df_5m):,} 根')
    print('[INIT] 預計算指標...')
    ind = precompute_all(df_5m, verbose=False)
    print('  完成')

    bm_check = run_backtest(df_5m, ind, BASE)
    bs = score(bm_check)
    print(f'\n[v5 BASE]  n={bm_check["n"]} wr={bm_check["wr"]}% pf={bm_check["pf"]:.3f}'
          f'  net={bm_check["net"]:+,} rr={bm_check["rr"]:.3f} n6={bm_check["n6"]}  s={bs:+.3f}\n')

    p1_res, p2_res, p3_res = [], [], []

    # ════════════════════════════════════════════════════════════════════════
    # Phase 1: trail_dist_atr 精細掃描
    # ════════════════════════════════════════════════════════════════════════
    print('─' * 90)
    print('  Phase 1: trail_dist_atr 精細掃描（追蹤距離 / ATR 倍數）')
    print('─' * 90)
    for td in TRAIL_DIST_VALS:
        params = {**BASE, 'trail_dist_atr': td}
        m = run_backtest(df_5m, ind, params)
        s = score(m)
        label = f'trail_dist={td:.2f}'
        pr(label, m, s)
        if m:
            p1_res.append((td, m, s))

    top_p1 = top_n(p1_res, 5)
    print(f'\n  ▶ Phase 1 TOP 5:')
    for td, m, s in top_p1:
        flag = ' ★' if is_better(m) else ''
        print(f'    trail_dist={td:.2f}  net={m["net"]:+,.0f} rr={m["rr"]:.3f} wr={m["wr"]:.1f}%  s={s:+.3f}{flag}')
    best_td = top_p1[0][0] if top_p1 else BASE['trail_dist_atr']

    # ════════════════════════════════════════════════════════════════════════
    # Phase 2: afternoon_min_adx × min_adx
    # ════════════════════════════════════════════════════════════════════════
    print(f'\n{"─"*90}')
    print('  Phase 2: afternoon_min_adx × min_adx')
    print('─' * 90)
    for aft, madx in product(AFT_ADX_VALS, MIN_ADX_VALS):
        params = {**BASE, 'afternoon_min_adx': aft, 'min_adx': madx}
        m = run_backtest(df_5m, ind, params)
        s = score(m)
        label = f'aft_adx={aft:.0f} min_adx={madx:.0f}'
        pr(label, m, s)
        if m:
            p2_res.append((aft, madx, m, s))

    top_p2 = top_n(p2_res, 5)
    print(f'\n  ▶ Phase 2 TOP 5:')
    for aft, madx, m, s in top_p2:
        flag = ' ★' if is_better(m) else ''
        print(f'    aft={aft:.0f} madx={madx:.0f}  net={m["net"]:+,.0f} rr={m["rr"]:.3f} wr={m["wr"]:.1f}%  s={s:+.3f}{flag}')
    best_aft  = top_p2[0][0] if top_p2 else BASE['afternoon_min_adx']
    best_madx = top_p2[0][1] if top_p2 else BASE['min_adx']

    # ════════════════════════════════════════════════════════════════════════
    # Phase 3: trail_trigger_atr（固定最佳 trail_dist）
    # ════════════════════════════════════════════════════════════════════════
    print(f'\n{"─"*90}')
    print(f'  Phase 3: trail_trigger_atr 精細掃描（fixed trail_dist={best_td:.2f}）')
    print('─' * 90)
    for tt in TRAIL_TRIG_VALS:
        params = {**BASE, 'trail_trigger_atr': tt, 'trail_dist_atr': best_td}
        m = run_backtest(df_5m, ind, params)
        s = score(m)
        label = f'trail_trig={tt:.1f} (dist={best_td:.2f})'
        pr(label, m, s)
        if m:
            p3_res.append((tt, m, s))

    top_p3 = top_n(p3_res, 5)
    print(f'\n  ▶ Phase 3 TOP 5:')
    for tt, m, s in top_p3:
        flag = ' ★' if is_better(m) else ''
        print(f'    trail_trig={tt:.1f}  net={m["net"]:+,.0f} rr={m["rr"]:.3f} wr={m["wr"]:.1f}%  s={s:+.3f}{flag}')
    best_tt = top_p3[0][0] if top_p3 else BASE['trail_trigger_atr']

    # ════════════════════════════════════════════════════════════════════════
    # Phase 4: 聯合驗證（Top3 trail × Top3 ADX）
    # ════════════════════════════════════════════════════════════════════════
    print(f'\n{"─"*90}')
    print('  Phase 4: 聯合驗證（trail 組合 × ADX 組合 × trail_trigger）')
    print('─' * 90)

    top3_td  = [x[0] for x in top_n(p1_res, 3)]
    top3_aft = [(x[0], x[1]) for x in top_n(p2_res, 3)]
    top3_tt  = [x[0] for x in top_n(p3_res, 3)]

    print(f'  trail_dist 候選: {top3_td}')
    print(f'  (aft_adx, min_adx) 候選: {top3_aft}')
    print(f'  trail_trigger 候選: {top3_tt}')
    print()

    joint_res = []
    seen = set()
    for td, (aft, madx), tt in product(top3_td, top3_aft, top3_tt):
        key = (td, aft, madx, tt)
        if key in seen: continue
        seen.add(key)
        params = {**BASE,
                  'trail_dist_atr': td,
                  'afternoon_min_adx': aft, 'min_adx': madx,
                  'trail_trigger_atr': tt}
        m = run_backtest(df_5m, ind, params)
        s = score(m)
        label = f'dist={td:.2f} aft={aft:.0f} madx={madx:.0f} trig={tt:.1f}'
        pr(label, m, s)
        if m:
            joint_res.append((td, aft, madx, tt, m, s))

    # ════════════════════════════════════════════════════════════════════════
    # 最終報告
    # ════════════════════════════════════════════════════════════════════════
    all_res_fixed = []
    for td, m, s in p1_res:
        all_res_fixed.append(({'trail_dist_atr': td}, m, s))
    for aft, madx, m, s in p2_res:
        all_res_fixed.append(({'afternoon_min_adx': aft, 'min_adx': madx}, m, s))
    for tt, m, s in p3_res:
        all_res_fixed.append(({'trail_trigger_atr': tt, 'trail_dist_atr': best_td}, m, s))
    for td, aft, madx, tt, m, s in joint_res:
        all_res_fixed.append(({'trail_dist_atr': td,
                                'afternoon_min_adx': aft, 'min_adx': madx,
                                'trail_trigger_atr': tt}, m, s))

    all_res_fixed.sort(key=lambda x: -x[2])

    print(f'\n{"="*90}')
    print('  最終 TOP 15（全部 Phase 合併）')
    print('=' * 90)
    shown, seen2 = 0, set()
    for p, m, s in all_res_fixed:
        key = tuple(sorted(p.items()))
        if key in seen2: continue
        seen2.add(key)
        flag = ' ★' if is_better(m) else ''
        vary = {k: v for k, v in p.items() if abs(BASE.get(k, 999) - v) > 0.001}
        print(f'  #{shown+1:2d}  {str(vary):<65}  '
              f'n={m["n"]:3d} wr={m["wr"]:.1f}% rr={m["rr"]:.3f}'
              f'  net={m["net"]:+,.0f}  s={s:+.3f}{flag}')
        shown += 1
        if shown >= 15: break

    # 嚴格優於 v5 的組合
    strict = [(p, m, s) for p, m, s in all_res_fixed if is_better(m)]
    print(f'\n{"="*90}')
    if strict:
        print(f'  ★ 嚴格優於 v5 基準的組合（共 {len(strict)} 個，去重）')
        print('=' * 90)
        seen3 = set()
        for p, m, s in strict:
            key = tuple(sorted(p.items()))
            if key in seen3: continue
            seen3.add(key)
            vary = {k: v for k, v in p.items() if abs(BASE.get(k, 999) - v) > 0.001}
            delta_net = m['net'] - BM['net']
            delta_rr  = m['rr']  - BM['rr']
            print(f'  {str(vary):<65}  '
                  f'net={m["net"]:+,.0f}({delta_net:+,.0f})  '
                  f'rr={m["rr"]:.3f}({delta_rr:+.3f})  '
                  f'wr={m["wr"]:.1f}%  pf={m["pf"]:.3f}  n6={m["n6"]}')
    else:
        print('  （無結果嚴格優於 v5 — v5 可能已接近此架構天花板）')
    print('=' * 90)

    # 儲存
    out = ROOT / 'data' / 'optimize_results' / 'signal_v5c_results.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    save, seen4 = [], set()
    for p, m, s in all_res_fixed:
        key = tuple(sorted(p.items()))
        if key in seen4: continue
        seen4.add(key)
        save.append({'params': p, **m, 'score': s, 'better_than_v5': is_better(m)})
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(save, f, ensure_ascii=False, indent=2)
    print(f'\n結果已儲存：{out}')


if __name__ == '__main__':
    main()
