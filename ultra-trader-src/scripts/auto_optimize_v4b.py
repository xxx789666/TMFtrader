"""
BreakoutTrend v4b 自動循環優化 — 進場品質維度
==============================================
基準：v4（trail_t=1.2, ec_bars=30）
  → PF=1.917 | WR=57.0% | RR=1.444 | 淨利=+179,750(+89.9%) | N6=8/28

目標：找到讓淨利突破 +200,000 TWD（+100%）的進場品質參數
探索維度（v3/v4 未掃描）：
  Phase 1 — min_adx × afternoon_min_adx  (進場強度門檻)
  Phase 2 — ema200_margin_atr            (EMA200 緩衝帶)
  Phase 3 — momentum_rsi_bull/bear       (動能分數閾值)
  Phase 4 — 聯合驗證 Top5×Top5

評分目標（升序優先）：
  1. 淨利 ≥ 200,000 TWD（+100%）
  2. WR 不掉（≥ 57.0%）
  3. RR 不掉（≥ 1.44）
  4. N6 不掉（≥ 8/28）

執行：
  cd C:/Users/xx/Desktop/永豐-自動化交易/ultra-trader-src
  python scripts/auto_optimize_v4b.py [--rounds 3]
"""

import sys, time, json, itertools, argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict

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

# ── v4 基準參數（固定不動） ────────────────────────────────────────────────────
BASE_V4 = dict(
    sl_atr=2.5, tp_atr=10.0,
    trail_trigger_atr=1.2,
    trail_dist_atr=1.25,
    max_bars=80,
    min_adx=20.0,
    afternoon_min_adx=32.0,
    min_di_gap=5.0,
    squeeze_ratio=0.90, expand_ratio=1.08, min_vol_ratio=1.0,
    pullback_ema_gap=0.3, breakeven_trigger_atr=999,
    early_cut_bars=30,
    early_cut_loss_atr=1.5,
    max_loss_twd=4000.0,
    trend_filter=True,
    ema200_margin_atr=0.0,
    use_momentum_score=True,
    momentum_rsi_bull=52.0,
    momentum_rsi_bear=48.0,
    momentum_session_atr=0.5,
    point_value=10.0,
)

# v4 已知基準值
BASELINE = dict(n=121, wr=57.0, pf=1.917, net=179_750, rr=1.444, n6=8, wm=-7.3)

INITIAL_BALANCE = 200_000
INSTRUMENT      = 'TMF'
RISK_PROFILE    = 'tmf_3x'

# ── 搜索空間 ──────────────────────────────────────────────────────────────────
# Phase 1: ADX 門檻（進場品質 vs 數量的平衡）
MIN_ADX_VALS      = [15.0, 17.0, 18.0, 20.0, 22.0, 25.0]
AFT_ADX_VALS      = [26.0, 28.0, 30.0, 32.0, 35.0, 38.0]

# Phase 2: EMA200 緩衝帶
EMA200_MARGIN_VALS = [0.0, 0.1, 0.2, 0.3, 0.5, 0.8, 1.0]

# Phase 3: 動能分數閾值
RSI_BULL_VALS     = [50.0, 51.0, 52.0, 53.0, 54.0]
RSI_BEAR_VALS     = [46.0, 47.0, 48.0, 49.0, 50.0]

RESULTS_DIR = ROOT / 'data' / 'optimizer_results_v4b'
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


# ── 評分函數（以 v4 基準為參照系） ────────────────────────────────────────────
def composite_score(r: dict) -> float:
    """
    v4b 評分：以突破 +100% 淨利為核心目標
    同時懲罰 WR/RR/N6 低於 v4 基準
    """
    net = r.get('net', 0.0)
    wr  = r.get('wr',  0.0)
    pf  = r.get('pf',  0.0)
    rr  = r.get('rr',  0.0)
    n6  = r.get('n6',  0)

    # 主要指標分數
    net_score = min(max(net, 0) / 200_000, 1.2)   # 超過 200K 仍給分
    wr_score  = max(wr - 50, 0) / 20              # 50%→0, 70%→100%
    pf_score  = min(max(pf - 1.0, 0) / 1.2, 1.0)
    n6_score  = n6 / 28

    score = (net_score * 4.0 + pf_score * 2.0 + n6_score * 3.0 + wr_score * 1.0)

    # 懲罰低於 v4 基準的維度
    if wr  < BASELINE['wr']  - 2.0:  score -= (BASELINE['wr']  - 2.0 - wr)  * 0.2
    if rr  < BASELINE['rr']  - 0.1:  score -= (BASELINE['rr']  - 0.1 - rr)  * 0.5
    if n6  < BASELINE['n6']  - 1:    score -= (BASELINE['n6']  - 1 - n6)    * 0.3
    if pf  < 1.60:                   score -= (1.60 - pf) * 0.5
    if net < 120_000:                score -= (120_000 - net) / 10_000

    return round(score, 4)


# ── 執行單一回測 ──────────────────────────────────────────────────────────────
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
        pf     = gp / gl
        wr     = len(wins) / n * 100
        avg_w  = gp / len(wins) if wins else 0
        avg_l  = gl / len(losses) if losses else 1e-9
        rr     = avg_w / avg_l
        net    = r.final_balance - INITIAL_BALANCE

        monthly = defaultdict(lambda: {'n':0,'wins':0,'pnl':0.0,'bal_start':0.0})
        bal = float(INITIAL_BALANCE)
        for t in trades:
            ym = t['entry_time'][:7]
            if monthly[ym]['bal_start'] == 0.0:
                monthly[ym]['bal_start'] = bal
            monthly[ym]['n']    += 1
            monthly[ym]['pnl']  += t['pnl']
            if t['pnl'] > 0:
                monthly[ym]['wins'] += 1

        n6 = 0; ng = 0; worst_m = 0.0
        for ym in sorted(monthly):
            m = monthly[ym]
            bstart = m['bal_start'] if m['bal_start'] > 0 else INITIAL_BALANCE
            ret = m['pnl'] / bstart * 100
            if ret >= 6.0: n6 += 1
            if m['pnl']  > 0: ng += 1
            if ret < worst_m: worst_m = ret
            bal += m['pnl']

        # 只取本 Phase 的可變參數
        varying = {k: params[k] for k in params if k not in BASE_V4 or params[k] != BASE_V4[k]}

        result = dict(
            n=n, wr=round(wr,1), pf=round(pf,3),
            net=round(net,0), avg_w=round(avg_w,0), avg_l=round(avg_l,0),
            rr=round(rr,3), n6=n6, ng=ng, worst_m=round(worst_m,1),
            **varying,
        )
        result['score'] = composite_score(result)
        return result

    except Exception:
        return None


# ── 批次掃描 ──────────────────────────────────────────────────────────────────
def sweep(df_5m, ind, param_list: list, phase_name: str) -> list:
    results = []
    total = len(param_list)
    t0 = time.time()
    for i, p in enumerate(param_list, 1):
        r = run_backtest(df_5m, ind, p)
        if r:
            results.append(r)
        elapsed = time.time() - t0
        eta     = elapsed / i * (total - i) if i > 0 else 0
        print(f'\r  [{phase_name}] {i}/{total} OK:{len(results)} ETA:{eta:.0f}s  ',
              end='', flush=True)
    print()
    return sorted(results, key=lambda x: x['score'], reverse=True)


# ── 結果列印 ──────────────────────────────────────────────────────────────────
def print_top(results: list, n: int = 10, phase: str = ''):
    if not results:
        print('  (無結果)')
        return

    # 動態抓取可變鍵
    all_keys = list(results[0].keys())
    var_keys = [k for k in all_keys if k not in
                ('n','wr','pf','net','avg_w','avg_l','rr','n6','ng','worst_m','score')]

    def vs(val, base_key, fmt='.2f'):
        bv = BASELINE.get(base_key, None)
        if bv is None: return ''
        diff = val - bv
        return f'({diff:+.2f})' if '.' in fmt else f'({diff:+.0f})'

    print(f'\n  ─ Top {n} [{phase}] ─')
    header_vars = '  '.join(f'{k:>14}' for k in var_keys)
    print(f'  {header_vars} | {"n":>4} {"WR":>6} {"PF":>6} {"淨利":>10} {"報酬":>7} | {"RR":>5} {"N6":>4} {"最差月":>7} | {"Score":>7}')

    for r in results[:n]:
        var_vals = '  '.join(f'{r.get(k, "?"):>14}' for k in var_keys)
        net_delta = f'{(r["net"]-BASELINE["net"])//1000:+.0f}K'
        wr_flag  = '✅' if r['wr']  >= BASELINE['wr']  else '❌'
        rr_flag  = '✅' if r['rr']  >= BASELINE['rr']-0.05 else '❌'
        net_flag = '✅' if r['net'] >= 200_000 else ('🔶' if r['net'] >= 185_000 else '❌')
        print(f'  {var_vals} | {r["n"]:>4} {wr_flag}{r["wr"]:>5.1f}% {r["pf"]:>6.3f} '
              f'{net_flag}{r["net"]:>+9,.0f} {r["net"]/INITIAL_BALANCE*100:>+6.1f}% | '
              f'{rr_flag}{r["rr"]:>5.3f} {r["n6"]:>4}/28 {r["worst_m"]:>+7.1f}% | {r["score"]:>7.3f}')
    print()


# ── 主循環 ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rounds', type=int, default=3)
    args = parser.parse_args()

    print('\n' + '='*85)
    print('  BreakoutTrend v4b 自動循環優化（進場品質維度）')
    print(f'  v4基準: WR={BASELINE["wr"]}% | PF={BASELINE["pf"]} | RR={BASELINE["rr"]} | 淨利=+{BASELINE["net"]:,}(+89.9%) | N6={BASELINE["n6"]}/28')
    print(f'  目標  : 淨利≥200K(+100%) | WR≥{BASELINE["wr"]}% | RR≥{BASELINE["rr"]:.2f} | N6≥{BASELINE["n6"]}/28')
    print('='*85)

    # 資料載入
    DATA_PATH = ROOT / 'data' / 'historical' / 'tmf_20260411_full_1m.csv'
    print(f'\n[INIT] 載入資料...')
    df_1m = pd.read_csv(DATA_PATH, parse_dates=['datetime']).sort_values('datetime').reset_index(drop=True)
    df    = df_1m.set_index('datetime')
    df_5m = df.resample('5min').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
    df_5m = df_5m.between_time('08:45', '13:30').reset_index()
    print(f'  5分鐘K線: {len(df_5m):,} 根')
    print('[INIT] 預計算指標...')
    ind = precompute_all(df_5m, verbose=False)
    print('  完成')

    global_best   = None
    all_top_adx   = []
    all_top_ema   = []
    all_top_rsi   = []
    ts = datetime.now().strftime('%Y%m%d_%H%M')

    best_min_adx    = BASE_V4['min_adx']
    best_aft_adx    = BASE_V4['afternoon_min_adx']
    best_ema_margin = BASE_V4['ema200_margin_atr']
    best_rsi_bull   = BASE_V4['momentum_rsi_bull']
    best_rsi_bear   = BASE_V4['momentum_rsi_bear']

    for round_num in range(1, args.rounds + 1):
        print(f'\n{"="*85}')
        print(f'  ROUND {round_num}/{args.rounds}')
        print(f'{"="*85}')

        # ════════════════════════════════════════════════════════════════
        # Phase 1: ADX 門檻掃描（進場強度 vs 頻率）
        # ════════════════════════════════════════════════════════════════
        if round_num == 1:
            adx_vals = MIN_ADX_VALS
            aft_vals = AFT_ADX_VALS
        else:
            step_a = 1.0
            step_f = 2.0
            adx_vals = sorted(set(max(10.0, min(30.0, round(best_min_adx + i*step_a, 1)))
                                  for i in range(-2, 3)))
            aft_vals = sorted(set(max(20.0, min(45.0, round(best_aft_adx + i*step_f, 1)))
                                  for i in range(-2, 3)))
            print(f'    Zoom: min_adx={adx_vals}  aft_adx={aft_vals}')

        p1_combos = len(adx_vals) * len(aft_vals)
        print(f'\n  ── Phase 1: ADX 門檻掃描 ({len(adx_vals)}×{len(aft_vals)}={p1_combos} 組) ──')
        p1_params = []
        for adx, aft in itertools.product(adx_vals, aft_vals):
            p = dict(BASE_V4,
                     min_adx=adx, afternoon_min_adx=aft,
                     ema200_margin_atr=best_ema_margin,
                     momentum_rsi_bull=best_rsi_bull, momentum_rsi_bear=best_rsi_bear)
            p1_params.append(p)

        r1 = sweep(df_5m, ind, p1_params, f'R{round_num}-P1-ADX')
        all_top_adx = sorted(all_top_adx + r1[:6], key=lambda x: x['score'], reverse=True)[:12]
        if r1:
            best_min_adx = r1[0].get('min_adx', best_min_adx)
            best_aft_adx = r1[0].get('afternoon_min_adx', best_aft_adx)
            print_top(r1, n=8, phase=f'R{round_num} Phase1 ADX')

        # ════════════════════════════════════════════════════════════════
        # Phase 2: EMA200 緩衝帶掃描
        # ════════════════════════════════════════════════════════════════
        if round_num == 1:
            ema_vals = EMA200_MARGIN_VALS
        else:
            step_e = 0.1
            ema_vals = sorted(set(round(max(0.0, min(2.0, best_ema_margin + i*step_e)), 2)
                                  for i in range(-2, 3)))
            print(f'    Zoom: ema200_margin={ema_vals}')

        print(f'\n  ── Phase 2: EMA200 緩衝帶掃描 ({len(ema_vals)} 組) ──')
        p2_params = []
        for em in ema_vals:
            p = dict(BASE_V4,
                     min_adx=best_min_adx, afternoon_min_adx=best_aft_adx,
                     ema200_margin_atr=em,
                     momentum_rsi_bull=best_rsi_bull, momentum_rsi_bear=best_rsi_bear)
            p2_params.append(p)

        r2 = sweep(df_5m, ind, p2_params, f'R{round_num}-P2-EMA')
        all_top_ema = sorted(all_top_ema + r2[:5], key=lambda x: x['score'], reverse=True)[:10]
        if r2:
            best_ema_margin = r2[0].get('ema200_margin_atr', best_ema_margin)
            print_top(r2, n=7, phase=f'R{round_num} Phase2 EMA200')

        # ════════════════════════════════════════════════════════════════
        # Phase 3: RSI 動能閾值掃描
        # ════════════════════════════════════════════════════════════════
        if round_num == 1:
            bull_vals = RSI_BULL_VALS
            bear_vals = RSI_BEAR_VALS
        else:
            step_r = 0.5
            bull_vals = sorted(set(round(max(48.0, min(58.0, best_rsi_bull + i*step_r)), 1)
                                   for i in range(-2, 3)))
            bear_vals = sorted(set(round(max(42.0, min(52.0, best_rsi_bear + i*step_r)), 1)
                                   for i in range(-2, 3)))
            print(f'    Zoom: rsi_bull={bull_vals}  rsi_bear={bear_vals}')

        p3_combos = len(bull_vals) * len(bear_vals)
        print(f'\n  ── Phase 3: RSI 動能閾值掃描 ({len(bull_vals)}×{len(bear_vals)}={p3_combos} 組) ──')
        p3_params = []
        for bull, bear in itertools.product(bull_vals, bear_vals):
            if bull <= bear:   # 邏輯約束: bull > bear
                continue
            p = dict(BASE_V4,
                     min_adx=best_min_adx, afternoon_min_adx=best_aft_adx,
                     ema200_margin_atr=best_ema_margin,
                     momentum_rsi_bull=bull, momentum_rsi_bear=bear)
            p3_params.append(p)

        r3 = sweep(df_5m, ind, p3_params, f'R{round_num}-P3-RSI')
        all_top_rsi = sorted(all_top_rsi + r3[:5], key=lambda x: x['score'], reverse=True)[:10]
        if r3:
            best_rsi_bull = r3[0].get('momentum_rsi_bull', best_rsi_bull)
            best_rsi_bear = r3[0].get('momentum_rsi_bear', best_rsi_bear)
            print_top(r3, n=7, phase=f'R{round_num} Phase3 RSI')

        # ════════════════════════════════════════════════════════════════
        # Phase 4: 全維度聯合驗證 (Top4 ADX × Top4 EMA × Top3 RSI)
        # ════════════════════════════════════════════════════════════════
        print(f'\n  ── Phase 4: 全維度聯合驗證 ──')
        top_adx = all_top_adx[:4]
        top_ema = all_top_ema[:4]
        top_rsi = all_top_rsi[:3]

        p4_params = []
        seen = set()
        for adx_r in top_adx:
            for ema_r in top_ema:
                for rsi_r in top_rsi:
                    adx = adx_r.get('min_adx', best_min_adx)
                    aft = adx_r.get('afternoon_min_adx', best_aft_adx)
                    em  = ema_r.get('ema200_margin_atr', best_ema_margin)
                    bull = rsi_r.get('momentum_rsi_bull', best_rsi_bull)
                    bear = rsi_r.get('momentum_rsi_bear', best_rsi_bear)
                    key = (adx, aft, em, bull, bear)
                    if key in seen or bull <= bear:
                        continue
                    seen.add(key)
                    p4_params.append(dict(BASE_V4,
                                         min_adx=adx, afternoon_min_adx=aft,
                                         ema200_margin_atr=em,
                                         momentum_rsi_bull=bull, momentum_rsi_bear=bear))

        # 加入單維度最佳的各項組合
        for adx_r in top_adx[:3]:
            key = (adx_r.get('min_adx', best_min_adx), adx_r.get('afternoon_min_adx', best_aft_adx),
                   best_ema_margin, best_rsi_bull, best_rsi_bear)
            if key not in seen:
                seen.add(key)
                p4_params.append(dict(BASE_V4,
                                      min_adx=adx_r.get('min_adx', best_min_adx),
                                      afternoon_min_adx=adx_r.get('afternoon_min_adx', best_aft_adx),
                                      ema200_margin_atr=best_ema_margin,
                                      momentum_rsi_bull=best_rsi_bull,
                                      momentum_rsi_bear=best_rsi_bear))

        print(f'  ({len(p4_params)} 組聯合組合)')
        r4 = sweep(df_5m, ind, p4_params, f'R{round_num}-P4-Joint')
        if r4:
            print_top(r4, n=10, phase=f'R{round_num} Phase4 Joint')

            round_best = r4[0]
            if global_best is None or round_best['score'] > global_best['score']:
                global_best = round_best
                best_min_adx    = round_best.get('min_adx', best_min_adx)
                best_aft_adx    = round_best.get('afternoon_min_adx', best_aft_adx)
                best_ema_margin = round_best.get('ema200_margin_atr', best_ema_margin)
                best_rsi_bull   = round_best.get('momentum_rsi_bull', best_rsi_bull)
                best_rsi_bear   = round_best.get('momentum_rsi_bear', best_rsi_bear)
                print(f'  ★ 新全域最佳！Score={round_best["score"]:.3f}  '
                      f'淨利={round_best["net"]:+,.0f}  WR={round_best["wr"]:.1f}%  '
                      f'RR={round_best["rr"]:.3f}  N6={round_best["n6"]}/28')

            if round_best['net'] >= 200_000 and round_best['wr'] >= BASELINE['wr'] - 1.0:
                print(f'\n  ★★★ 目標達成：淨利≥200K + WR維持！提前結束 ★★★')
                break

    # ════════════════════════════════════════════════════════════════════
    # 最終報告
    # ════════════════════════════════════════════════════════════════════
    print('\n' + '='*85)
    print('  最終結果')
    print('='*85)

    if global_best:
        gb = global_best
        print(f'\n  最佳進場品質參數組合：')
        print(f'    min_adx           = {gb.get("min_adx", BASE_V4["min_adx"])} （v4基準 20.0）')
        print(f'    afternoon_min_adx = {gb.get("afternoon_min_adx", BASE_V4["afternoon_min_adx"])} （v4基準 32.0）')
        print(f'    ema200_margin_atr = {gb.get("ema200_margin_atr", BASE_V4["ema200_margin_atr"])} （v4基準 0.0）')
        print(f'    momentum_rsi_bull = {gb.get("momentum_rsi_bull", BASE_V4["momentum_rsi_bull"])} （v4基準 52.0）')
        print(f'    momentum_rsi_bear = {gb.get("momentum_rsi_bear", BASE_V4["momentum_rsi_bear"])} （v4基準 48.0）')
        print()
        print(f'  績效對比（v4 → v4b）：')
        print(f'    {"指標":<14} {"v4基準":>10} {"v4b優化":>10} {"差異":>10}')
        print(f'    {"─"*46}')
        metrics = [
            ('筆數',      'n',   gb['n'],               BASELINE['n'],   ''),
            ('勝率',      'wr',  f'{gb["wr"]:.1f}%',    f'{BASELINE["wr"]:.1f}%', f'{gb["wr"]-BASELINE["wr"]:+.1f}%'),
            ('PF',        'pf',  f'{gb["pf"]:.3f}',     f'{BASELINE["pf"]:.3f}',  f'{gb["pf"]-BASELINE["pf"]:+.3f}'),
            ('淨利(TWD)', 'net', f'+{gb["net"]:,.0f}',  f'+{BASELINE["net"]:,.0f}', f'{gb["net"]-BASELINE["net"]:+,.0f}'),
            ('總報酬',    '',    f'{gb["net"]/200000*100:.1f}%', f'{BASELINE["net"]/200000*100:.1f}%', f'{(gb["net"]-BASELINE["net"])/200000*100:+.1f}%'),
            ('盈虧比RR',  'rr',  f'{gb["rr"]:.3f}',     f'{BASELINE["rr"]:.3f}',  f'{gb["rr"]-BASELINE["rr"]:+.3f}'),
            ('≥6%月數',   'n6',  f'{gb["n6"]}/28',      f'{BASELINE["n6"]}/28',   f'{gb["n6"]-BASELINE["n6"]:+d}'),
            ('最差月',    'wm',  f'{gb["worst_m"]:.1f}%', f'{BASELINE["wm"]:.1f}%', f'{gb["worst_m"]-BASELINE["wm"]:+.1f}%'),
        ]
        for label, _, v4b, v4, diff in metrics:
            print(f'    {label:<14} {v4:>10} {v4b:>10} {diff:>10}')
        print()

        # 目標達成
        checks = [
            ('淨利 ≥ 200K', gb['net'] >= 200_000, f'+{gb["net"]:,.0f} TWD'),
            ('WR ≥ 57.0%',  gb['wr']  >= 57.0,   f'{gb["wr"]:.1f}%'),
            ('RR ≥ 1.44',   gb['rr']  >= 1.44,   f'{gb["rr"]:.3f}'),
            ('N6 ≥ 8/28',   gb['n6']  >= 8,       f'{gb["n6"]}/28'),
            ('PF ≥ 1.90',   gb['pf']  >= 1.90,   f'{gb["pf"]:.3f}'),
        ]
        print('  目標達成狀況：')
        for label, ok, val in checks:
            print(f'    {"✅" if ok else "❌"} {label:<20} → {val}')

        # 儲存結果
        result_path = RESULTS_DIR / f'best_v4b_{ts}.json'
        with open(result_path, 'w', encoding='utf-8') as f:
            json.dump(global_best, f, indent=2, ensure_ascii=False, default=str)
        print(f'\n  結果已儲存：{result_path}')

        # 完整參數輸出（v4 + v4b）
        final_params = dict(BASE_V4)
        for k in ('min_adx', 'afternoon_min_adx', 'ema200_margin_atr',
                  'momentum_rsi_bull', 'momentum_rsi_bear'):
            if k in gb:
                final_params[k] = gb[k]

        print('\n  ─ v4b 完整參數（可貼入 gen_backtest_report_v4b.py） ─')
        for k, v in sorted(final_params.items()):
            mark = ' ← v4b改' if (k in gb and gb[k] != BASE_V4.get(k)) else ''
            print(f'  {k}={v},{mark}')

    else:
        print('  無有效結果')

    print('\n' + '='*85)


if __name__ == '__main__':
    main()
