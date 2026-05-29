"""
auto_optimize_signal_v5.py — 進場信號品質優化（v5 方向）

基準：v4b (trail_trigger=1.2, ec_bars=30, rsi_bear=46)
  n=119  WR=57.1%  PF=1.949  net=+181,130  RR=1.461  N6=8/28

目標：通過更嚴格的進場過濾，提升 RR ≥ 1.5 且不傷害 net / WR

未掃描維度（v3/v4/v4b 均未觸及）：
  Phase 1 — min_vol_ratio × min_di_gap   （成交量 + 方向清晰度）
  Phase 2 — squeeze_ratio × expand_ratio  （壓縮深度 + 突破強度）
  Phase 3 — pullback_ema_gap             （Mode B 回調靈敏度；0.0=停用 Mode B）
  Phase 4 — 聯合驗證 Top 組合

評分主軸：
  RR ≥ 1.5  → 主目標（reward 3.0）
  net 不掉  → 次要（reward 2.5）
  N6 ≥ 8    → 月度穩定（reward 3.0）
  WR ≥ 55%  → 底線（reward 1.0）
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

# ── v4b 基準參數（固定） ──────────────────────────────────────────────────────
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

INSTRUMENT     = "TMF"
INITIAL_BALANCE = 200_000
RISK_PROFILE   = "tmf_3x"


# ── 掃描空間 ─────────────────────────────────────────────────────────────────
VOL_RATIO_VALS    = [1.0, 1.1, 1.2, 1.3, 1.5]
DI_GAP_VALS       = [5.0, 7.0, 8.0, 10.0, 12.0]
SQUEEZE_VALS      = [0.75, 0.80, 0.85, 0.90]
EXPAND_VALS       = [1.08, 1.10, 1.12, 1.15]
PULLBACK_VALS     = [0.0, 0.15, 0.2, 0.3, 0.5]   # 0.0 = 停用 Mode B


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
        aw     = gp / len(wins)  if wins   else 0.0
        al     = gl / len(losses) if losses else 1e-9
        net    = r.final_balance - r.initial_balance

        monthly: dict[str, float] = {}
        for t in trades:
            ym = t['exit_time'][:7]
            monthly[ym] = monthly.get(ym, 0) + t['pnl']
        n6 = sum(1 for v in monthly.values() if v >= INITIAL_BALANCE * 0.06)

        return dict(
            n=n, wr=round(len(wins)/n*100, 1),
            pf=round(gp/gl, 3),
            net=round(net, 0),
            rr=round(aw/al, 3),
            n6=n6,
        )
    except Exception as e:
        print(f'  [ERR] {e}')
        return None


def score(m: dict) -> float:
    """RR 優先、net 次要、N6 穩定性"""
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
    if m['n']   < 60:   s -= 1.5   # 太少樣本不可信
    return round(s, 4)


def delta(m: dict, b: dict) -> str:
    return (f"Δnet={m['net']-b['net']:+,.0f} "
            f"Δrr={m['rr']-b['rr']:+.3f} "
            f"Δwr={m['wr']-b['wr']:+.1f}%")


def top5(results: list) -> list:
    return sorted(results, key=lambda x: -x[-1])[:5]


def print_result(label, m, s, bm):
    if m is None:
        print(f"  {label}  [ERROR]")
        return
    print(f"  {label}  n={m['n']:3d} wr={m['wr']:.1f}% pf={m['pf']:.3f}"
          f"  net={m['net']:+,.0f} rr={m['rr']:.3f} n6={m['n6']}"
          f"  score={s:+.3f}  {delta(m, bm)}")


def main():
    # 載入資料
    data_path = ROOT / 'data' / 'historical' / 'tmf_20260411_full_1m.csv'
    if not data_path.exists():
        print(f"[ERROR] 找不到 {data_path}")
        sys.exit(1)

    print('=' * 85)
    print('  進場信號品質優化 v5 — 成交量 / 方向 / 壓縮 / Mode B 維度')
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

    # 確認基準
    print('\n[0] 確認基準 v4b...')
    bm = run_backtest(df_5m, ind, BASE)
    bs = score(bm)
    print(f'  BASELINE  n={bm["n"]} wr={bm["wr"]}% pf={bm["pf"]:.3f}'
          f'  net={bm["net"]:+,} rr={bm["rr"]:.3f} n6={bm["n6"]}  score={bs:+.3f}')

    all_phase1 = []
    all_phase2 = []
    all_phase3 = []

    # ════════════════════════════════════════════════════════════════════════
    # Phase 1: min_vol_ratio × min_di_gap
    # ════════════════════════════════════════════════════════════════════════
    print('\n' + '─' * 85)
    print('  Phase 1: min_vol_ratio × min_di_gap')
    print('─' * 85)
    combos = list(product(VOL_RATIO_VALS, DI_GAP_VALS))
    for vol, dig in combos:
        params = {**BASE, 'min_vol_ratio': vol, 'min_di_gap': dig}
        m = run_backtest(df_5m, ind, params)
        s = score(m)
        label = f"vol={vol:.1f} dig={dig:.0f}"
        print_result(label, m, s, bm)
        if m:
            all_phase1.append((vol, dig, m, s))

    top_p1 = top5(all_phase1)
    print(f'\n  ▶ Phase 1 TOP 5:')
    for vol, dig, m, s in top_p1:
        print(f'    vol={vol:.1f} dig={dig:.0f}  net={m["net"]:+,.0f} rr={m["rr"]:.3f}  score={s:+.3f}')

    best_vol = top_p1[0][0] if top_p1 else BASE['min_vol_ratio']
    best_dig = top_p1[0][1] if top_p1 else BASE['min_di_gap']

    # ════════════════════════════════════════════════════════════════════════
    # Phase 2: squeeze_ratio × expand_ratio
    # ════════════════════════════════════════════════════════════════════════
    print('\n' + '─' * 85)
    print('  Phase 2: squeeze_ratio × expand_ratio')
    print('─' * 85)
    combos = list(product(SQUEEZE_VALS, EXPAND_VALS))
    for sqz, exp in combos:
        params = {**BASE, 'squeeze_ratio': sqz, 'expand_ratio': exp}
        m = run_backtest(df_5m, ind, params)
        s = score(m)
        label = f"sqz={sqz:.2f} exp={exp:.2f}"
        print_result(label, m, s, bm)
        if m:
            all_phase2.append((sqz, exp, m, s))

    top_p2 = top5(all_phase2)
    print(f'\n  ▶ Phase 2 TOP 5:')
    for sqz, exp, m, s in top_p2:
        print(f'    sqz={sqz:.2f} exp={exp:.2f}  net={m["net"]:+,.0f} rr={m["rr"]:.3f}  score={s:+.3f}')

    best_sqz = top_p2[0][0] if top_p2 else BASE['squeeze_ratio']
    best_exp = top_p2[0][1] if top_p2 else BASE['expand_ratio']

    # ════════════════════════════════════════════════════════════════════════
    # Phase 3: pullback_ema_gap（Mode B 靈敏度）
    # ════════════════════════════════════════════════════════════════════════
    print('\n' + '─' * 85)
    print('  Phase 3: pullback_ema_gap (0.0=停用 Mode B)')
    print('─' * 85)
    for pb in PULLBACK_VALS:
        params = {**BASE, 'pullback_ema_gap': pb}
        m = run_backtest(df_5m, ind, params)
        s = score(m)
        label = f"pb_gap={pb:.2f}"
        print_result(label, m, s, bm)
        if m:
            all_phase3.append((pb, m, s))

    top_p3 = top5(all_phase3)
    print(f'\n  ▶ Phase 3 TOP 5:')
    for pb, m, s in top_p3:
        print(f'    pb_gap={pb:.2f}  net={m["net"]:+,.0f} rr={m["rr"]:.3f}  score={s:+.3f}')

    best_pb = top_p3[0][0] if top_p3 else BASE['pullback_ema_gap']

    # ════════════════════════════════════════════════════════════════════════
    # Phase 4: 聯合驗證（Top 組合交叉）
    # ════════════════════════════════════════════════════════════════════════
    print('\n' + '─' * 85)
    print('  Phase 4: 聯合驗證 (vol × dig × sqz × exp × pb)')
    print('─' * 85)

    # 取各 phase TOP 3
    p1_top3 = [(v, d) for v, d, _, _ in top5(all_phase1)[:3]]
    p2_top3 = [(s, e) for s, e, _, _ in top5(all_phase2)[:3]]
    p3_top3 = [pb for pb, _, _ in top5(all_phase3)[:3]]

    joint_results = []
    count = 0
    for (vol, dig), (sqz, exp), pb in product(p1_top3, p2_top3, p3_top3):
        count += 1
        params = {
            **BASE,
            'min_vol_ratio': vol, 'min_di_gap': dig,
            'squeeze_ratio': sqz, 'expand_ratio': exp,
            'pullback_ema_gap': pb,
        }
        m = run_backtest(df_5m, ind, params)
        s = score(m)
        label = f"vol={vol:.1f} dig={dig:.0f} sqz={sqz:.2f} exp={exp:.2f} pb={pb:.2f}"
        print_result(label, m, s, bm)
        if m:
            joint_results.append((params, m, s))

    # ════════════════════════════════════════════════════════════════════════
    # 最終報告
    # ════════════════════════════════════════════════════════════════════════
    print('\n' + '=' * 85)
    print('  最終 TOP 10（所有 Phase 合併）')
    print('=' * 85)

    all_results = []
    for vol, dig, m, s in all_phase1:
        all_results.append(({'min_vol_ratio': vol, 'min_di_gap': dig}, m, s))
    for sqz, exp, m, s in all_phase2:
        all_results.append(({'squeeze_ratio': sqz, 'expand_ratio': exp}, m, s))
    for pb, m, s in all_phase3:
        all_results.append(({'pullback_ema_gap': pb}, m, s))
    for params, m, s in joint_results:
        all_results.append((params, m, s))

    all_results.sort(key=lambda x: -x[2])
    for i, (p, m, s) in enumerate(all_results[:10], 1):
        vary = {k: v for k, v in p.items() if BASE.get(k) != v}
        print(f'#{i:2d} {str(vary):<55}  '
              f'n={m["n"]:3d} wr={m["wr"]:.1f}% rr={m["rr"]:.3f} net={m["net"]:+,.0f}  '
              f'score={s:+.3f}')

    # 嚴格優於基準
    strict = [(p, m, s) for p, m, s in all_results
              if m['net'] >= bm['net'] and m['rr'] >= bm['rr']
              and m['wr'] >= bm['wr'] - 0.5 and m['pf'] >= bm['pf'] - 0.05]
    print()
    if strict:
        print('=' * 85)
        print('  ★ 嚴格優於 v4b 基準（net↑ rr↑ wr≈ pf≈）')
        print('=' * 85)
        for p, m, s in strict:
            vary = {k: v for k, v in p.items() if BASE.get(k) != v}
            print(f'  {str(vary):<55}  '
                  f'net={m["net"]:+,.0f}({m["net"]-bm["net"]:+,.0f})  '
                  f'rr={m["rr"]:.3f}({m["rr"]-bm["rr"]:+.3f})  '
                  f'wr={m["wr"]:.1f}%  pf={m["pf"]:.3f}')
    else:
        print('  （無結果嚴格優於 v4b，最佳近似：）')
        if all_results:
            p, m, s = all_results[0]
            vary = {k: v for k, v in p.items() if BASE.get(k) != v}
            print(f'  {vary}')
            print(f'  net={m["net"]:+,.0f} rr={m["rr"]:.3f} wr={m["wr"]:.1f}% pf={m["pf"]:.3f} n6={m["n6"]}')

    # 儲存
    out = ROOT / 'data' / 'optimize_results' / 'signal_v5_results.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    save_data = [
        {'params': {k: v for k, v in p.items() if BASE.get(k) != v},
         **m, 'score': s}
        for p, m, s in all_results
    ]
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(save_data, f, ensure_ascii=False, indent=2)
    print(f'\n結果已儲存：{out}')


if __name__ == '__main__':
    main()
