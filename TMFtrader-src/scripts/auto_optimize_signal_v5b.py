"""
auto_optimize_signal_v5b.py — v5 精細掃描（二輪細化）

v5 粗掃發現的突破維度：
  expand_ratio   1.08→1.12~1.15   (+6,600~+12,580 net, RR突破1.5)
  pullback_ema_gap 0.30→0.20      (全面嚴格改善)
  min_di_gap     5.0→10.0         (+net, +wr)

細化目標：在已知最佳鄰域做 0.01/0.5 步長掃描，找到真正最優點

Phase 1 — expand_ratio × pullback_ema_gap 精細格
  expand_ratio:   [1.09, 1.10, 1.11, 1.12, 1.13, 1.14, 1.15, 1.16, 1.18]
  pullback_gap:   [0.10, 0.15, 0.18, 0.20, 0.22, 0.25, 0.28]

Phase 2 — min_di_gap 細化（固定 best expand+pb）
  min_di_gap:     [5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 14.0]

Phase 3 — 三維聯合驗證（Top3 × Top3 × Top3）

Phase 4 — 與其他已知最佳（rsi_bear=46, ec_bars=30）的相容性確認
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

# ── v4b 基準（已驗證）────────────────────────────────────────────────────────
BASE = dict(
    sl_atr=2.5, tp_atr=10.0,
    trail_trigger_atr=1.2, trail_dist_atr=1.25,
    max_bars=80,
    min_adx=20.0, afternoon_min_adx=32.0,
    min_di_gap=5.0,
    squeeze_ratio=0.90, expand_ratio=1.08,
    min_vol_ratio=1.0,
    pullback_ema_gap=0.3,
    breakeven_trigger_atr=999,
    early_cut_bars=30, early_cut_loss_atr=1.5,
    max_loss_twd=4000.0,
    trend_filter=True, ema200_margin_atr=0.0,
    use_momentum_score=True,
    momentum_rsi_bull=52.0, momentum_rsi_bear=46.0,
    momentum_session_atr=0.5, point_value=10.0,
    scale_out_trigger_atr=0.0, scale_out_qty=1,
)

BASELINE = dict(n=119, wr=57.1, pf=1.949, net=181130, rr=1.461, n6=8)

INSTRUMENT      = 'TMF'
INITIAL_BALANCE = 200_000
RISK_PROFILE    = 'tmf_3x'

# ── 細化掃描空間 ──────────────────────────────────────────────────────────────
EXPAND_FINE    = [1.09, 1.10, 1.11, 1.12, 1.13, 1.14, 1.15, 1.16, 1.18]
PULLBACK_FINE  = [0.10, 0.15, 0.18, 0.20, 0.22, 0.25, 0.28]
DI_GAP_FINE    = [5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0, 14.0]


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
        gp     = sum(t['pnl'] for t in wins)
        gl     = abs(sum(t['pnl'] for t in losses)) if losses else 1e-9
        aw     = gp / len(wins)   if wins   else 0.0
        al     = gl / len(losses)  if losses else 1e-9
        net    = r.final_balance - r.initial_balance
        monthly: dict[str, float] = {}
        for t in trades:
            ym = t['exit_time'][:7]
            monthly[ym] = monthly.get(ym, 0) + t['pnl']
        n6 = sum(1 for v in monthly.values() if v >= INITIAL_BALANCE * 0.06)
        return dict(
            n=n, wr=round(len(wins)/n*100, 1),
            pf=round(gp/gl, 3), net=round(net, 0),
            rr=round(aw/al, 3), n6=n6,
        )
    except Exception as e:
        print(f'  [ERR] {e}')
        return None


def score(m: dict) -> float:
    if m is None:
        return -99.0
    net_s = (m['net']  - 150_000) / 100_000
    rr_s  = (m['rr']   - 1.2) / 0.5
    wr_s  = (m['wr']   - 50.0) / 15.0
    pf_s  = (m['pf']   - 1.5) / 0.8
    n6_s  = m['n6'] / 10.0
    s = rr_s * 3.0 + net_s * 2.5 + n6_s * 3.0 + wr_s * 1.0 + pf_s * 0.5
    if m['wr']  < 50.0: s -= 2.0
    if m['pf']  < 1.5:  s -= 2.0
    if m['n']   < 60:   s -= 1.5
    return round(s, 4)


def is_strict_better(m: dict, bm: dict, rr_tol=0.0, net_tol=0) -> bool:
    """判斷 m 是否嚴格優於基準（RR↑ net↑ wr≈ pf≈）"""
    return (
        m['rr']  >= bm['rr']  + rr_tol
        and m['net'] >= bm['net'] - net_tol
        and m['wr']  >= bm['wr']  - 0.5
        and m['pf']  >= bm['pf']  - 0.05
        and m['n6']  >= bm['n6']  - 1
    )


def pr(label, m, s, bm):
    if m is None:
        print(f'  {label:<40}  [None]')
        return
    flag = ' ★' if is_strict_better(m, bm) else ''
    print(f'  {label:<40}  n={m["n"]:3d} wr={m["wr"]:.1f}% pf={m["pf"]:.3f}'
          f'  net={m["net"]:+,.0f} rr={m["rr"]:.3f} n6={m["n6"]}'
          f'  s={s:+.3f}{flag}')


def main():
    data_path = ROOT / 'data' / 'historical' / 'tmf_20260411_full_1m.csv'
    print('=' * 85)
    print('  v5 精細掃描（expand_ratio × pullback_ema_gap × min_di_gap 鄰域）')
    print('=' * 85)

    print('[INIT] 載入資料...')
    df_1m = pd.read_csv(data_path, parse_dates=['datetime']).sort_values('datetime').reset_index(drop=True)
    df    = df_1m.set_index('datetime')
    df_5m = df.resample('5min').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
    df_5m = df_5m.between_time('08:45','13:30').reset_index()
    print(f'  5分鐘K線: {len(df_5m):,} 根')
    print('[INIT] 預計算指標...')
    ind = precompute_all(df_5m, verbose=False)
    print('  完成')

    bm = run_backtest(df_5m, ind, BASE)
    bs = score(bm)
    print(f'\n[BASELINE v4b]  n={bm["n"]} wr={bm["wr"]}% pf={bm["pf"]:.3f}'
          f'  net={bm["net"]:+,} rr={bm["rr"]:.3f} n6={bm["n6"]}  score={bs:+.3f}\n')

    p1_results = []
    p2_results = []
    p3_results = []

    # ════════════════════════════════════════════════════════════════════════
    # Phase 1: expand_ratio × pullback_ema_gap 精細格
    # ════════════════════════════════════════════════════════════════════════
    print('─' * 85)
    print('  Phase 1: expand_ratio × pullback_ema_gap 精細格')
    print('─' * 85)
    for exp, pb in product(EXPAND_FINE, PULLBACK_FINE):
        params = {**BASE, 'expand_ratio': exp, 'pullback_ema_gap': pb}
        m = run_backtest(df_5m, ind, params)
        s = score(m)
        label = f'exp={exp:.2f} pb={pb:.2f}'
        pr(label, m, s, bm)
        if m:
            p1_results.append((exp, pb, m, s))

    p1_sorted = sorted(p1_results, key=lambda x: -x[3])
    print(f'\n  ▶ Phase 1 TOP 10:')
    for exp, pb, m, s in p1_sorted[:10]:
        flag = ' ★' if is_strict_better(m, bm) else ''
        print(f'    exp={exp:.2f} pb={pb:.2f}  '
              f'net={m["net"]:+,.0f} rr={m["rr"]:.3f} wr={m["wr"]:.1f}%'
              f'  s={s:+.3f}{flag}')

    best_exp = p1_sorted[0][0] if p1_sorted else BASE['expand_ratio']
    best_pb  = p1_sorted[0][1] if p1_sorted else BASE['pullback_ema_gap']

    # ════════════════════════════════════════════════════════════════════════
    # Phase 2: min_di_gap 細化（固定最佳 expand+pb）
    # ════════════════════════════════════════════════════════════════════════
    print(f'\n{"─"*85}')
    print(f'  Phase 2: min_di_gap 細化（fixed exp={best_exp:.2f} pb={best_pb:.2f}）')
    print('─' * 85)
    for dig in DI_GAP_FINE:
        params = {**BASE, 'expand_ratio': best_exp, 'pullback_ema_gap': best_pb, 'min_di_gap': dig}
        m = run_backtest(df_5m, ind, params)
        s = score(m)
        label = f'dig={dig:.1f} (exp={best_exp:.2f} pb={best_pb:.2f})'
        pr(label, m, s, bm)
        if m:
            p2_results.append((dig, m, s))

    p2_sorted = sorted(p2_results, key=lambda x: -x[2])
    print(f'\n  ▶ Phase 2 TOP 5:')
    for dig, m, s in p2_sorted[:5]:
        flag = ' ★' if is_strict_better(m, bm) else ''
        print(f'    dig={dig:.1f}  net={m["net"]:+,.0f} rr={m["rr"]:.3f}  s={s:+.3f}{flag}')

    best_dig = p2_sorted[0][0] if p2_sorted else BASE['min_di_gap']

    # ════════════════════════════════════════════════════════════════════════
    # Phase 3: 三維聯合驗證（Top4 expand × Top4 pb × Top4 dig）
    # ════════════════════════════════════════════════════════════════════════
    print(f'\n{"─"*85}')
    print('  Phase 3: 三維聯合驗證（Top4 × Top4 × Top4）')
    print('─' * 85)

    top_exps = list(dict.fromkeys(x[0] for x in p1_sorted[:8]))[:4]
    top_pbs  = list(dict.fromkeys(x[1] for x in p1_sorted[:8]))[:4]
    top_digs = [x[0] for x in p2_sorted[:4]]

    print(f'  expand_ratio 候選: {top_exps}')
    print(f'  pullback_gap 候選: {top_pbs}')
    print(f'  min_di_gap   候選: {top_digs}')
    print()

    joint_seen = set()
    for exp, pb, dig in product(top_exps, top_pbs, top_digs):
        key = (exp, pb, dig)
        if key in joint_seen:
            continue
        joint_seen.add(key)
        params = {**BASE, 'expand_ratio': exp, 'pullback_ema_gap': pb, 'min_di_gap': dig}
        m = run_backtest(df_5m, ind, params)
        s = score(m)
        label = f'exp={exp:.2f} pb={pb:.2f} dig={dig:.1f}'
        pr(label, m, s, bm)
        if m:
            p3_results.append((exp, pb, dig, m, s))

    # ════════════════════════════════════════════════════════════════════════
    # 最終綜合報告
    # ════════════════════════════════════════════════════════════════════════
    all_results = []
    for exp, pb, m, s in p1_results:
        all_results.append(({'expand_ratio': exp, 'pullback_ema_gap': pb}, m, s))
    for dig, m, s in p2_results:
        all_results.append(({'expand_ratio': best_exp, 'pullback_ema_gap': best_pb, 'min_di_gap': dig}, m, s))
    for exp, pb, dig, m, s in p3_results:
        all_results.append(({'expand_ratio': exp, 'pullback_ema_gap': pb, 'min_di_gap': dig}, m, s))

    all_results.sort(key=lambda x: -x[2])

    print(f'\n{"="*85}')
    print('  最終 TOP 15（全部 Phase 合併，依評分排序）')
    print('=' * 85)
    seen = set()
    shown = 0
    for p, m, s in all_results:
        key = (p.get('expand_ratio'), p.get('pullback_ema_gap'), p.get('min_di_gap'))
        if key in seen:
            continue
        seen.add(key)
        flag = ' ★' if is_strict_better(m, bm) else ''
        vary = {k: v for k, v in p.items() if abs(BASE.get(k, 999) - v) > 0.001}
        print(f'  #{shown+1:2d}  {str(vary):<65}  '
              f'n={m["n"]:3d} wr={m["wr"]:.1f}% rr={m["rr"]:.3f}'
              f'  net={m["net"]:+,.0f}  s={s:+.3f}{flag}')
        shown += 1
        if shown >= 15:
            break

    # 嚴格優於基準
    strict = [(p, m, s) for p, m, s in all_results if is_strict_better(m, bm)]
    strict_uniq = []
    seen2 = set()
    for p, m, s in strict:
        key = (p.get('expand_ratio'), p.get('pullback_ema_gap'), p.get('min_di_gap'))
        if key not in seen2:
            seen2.add(key)
            strict_uniq.append((p, m, s))

    print(f'\n{"="*85}')
    if strict_uniq:
        print(f'  ★ 嚴格優於 v4b 的組合（共 {len(strict_uniq)} 個）')
        print('=' * 85)
        print(f'  {"參數":<50}  {"net":>10}  {"rr":>6}  {"wr":>6}  {"pf":>6}  n6')
        print(f'  {"v4b 基準":<50}  {bm["net"]:>+10,.0f}  {bm["rr"]:>6.3f}  {bm["wr"]:>5.1f}%  {bm["pf"]:>6.3f}  {bm["n6"]}')
        print('  ' + '-' * 82)
        for p, m, s in strict_uniq[:20]:
            vary = {k: v for k, v in p.items() if abs(BASE.get(k, 999) - v) > 0.001}
            delta_net = m['net'] - bm['net']
            delta_rr  = m['rr']  - bm['rr']
            print(f'  {str(vary):<50}  {m["net"]:>+10,.0f}  {m["rr"]:>6.3f}  {m["wr"]:>5.1f}%  {m["pf"]:>6.3f}  {m["n6"]}'
                  f'  (Δnet={delta_net:+,.0f} Δrr={delta_rr:+.3f})')
    else:
        print('  （無結果嚴格優於 v4b）')
    print('=' * 85)

    # 找出最優 v5 推薦參數
    print()
    rr15_better = [(p, m, s) for p, m, s in all_results
                   if m and m['rr'] >= 1.5 and m['net'] >= bm['net'] - 5000
                   and m['n6'] >= 7]
    rr15_uniq = []
    seen3 = set()
    for p, m, s in rr15_better:
        key = (p.get('expand_ratio'), p.get('pullback_ema_gap'), p.get('min_di_gap'))
        if key not in seen3:
            seen3.add(key)
            rr15_uniq.append((p, m, s))
    rr15_uniq.sort(key=lambda x: -x[2])

    if rr15_uniq:
        print('  ★ RR≥1.5 且 net≥基準 的組合（v5 推薦候選）：')
        for p, m, s in rr15_uniq[:5]:
            vary = {k: v for k, v in p.items() if abs(BASE.get(k, 999) - v) > 0.001}
            print(f'    {vary}')
            print(f'      n={m["n"]} wr={m["wr"]}% pf={m["pf"]:.3f} net={m["net"]:+,.0f} rr={m["rr"]:.3f} n6={m["n6"]}')

    # 儲存
    out = ROOT / 'data' / 'optimize_results' / 'signal_v5b_results.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    save = []
    seen4 = set()
    for p, m, s in all_results:
        key = (p.get('expand_ratio'), p.get('pullback_ema_gap'), p.get('min_di_gap'))
        if key not in seen4:
            seen4.add(key)
            save.append({'params': p, **m, 'score': s,
                         'strict_better': is_strict_better(m, bm)})
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(save, f, ensure_ascii=False, indent=2)
    print(f'\n結果已儲存：{out}')


if __name__ == '__main__':
    main()
