"""
BreakoutTrend v3 自動循環優化系統
===================================
目標：
  1. 盈虧比 avg_w/avg_l : 1.40 → ≥ 1.60
  2. 總報酬              : +78.6% → ≥ +100%（淨利 ≥ 200,000 TWD）
  3. 勝率                : 維持 ≥ 53%（基準 56.2%）
  4. ≥6% 達標月數        : 8/28 → ≥ 10/28
  5. PF                  : 維持 ≥ 1.60

優化邏輯（三個杠杆）：
  A. trail_trigger_atr / trail_dist_atr    → 拉高平均獲利（avg_w）
  B. early_cut_bars / early_cut_loss_atr  → 壓低平均虧損（avg_l）
  C. max_loss_twd                          → 硬性控制最大單筆損失

循環策略：
  Phase 1 — Trail 參數粗搜  (固定 early_cut/hard_stop 基準)
  Phase 2 — Early Cut 精細化 (用 Phase1 最佳 trail 參數)
  Phase 3 — Hard Stop 調校  (用 Phase2 最佳組合)
  Phase 4 — 聯合驗證        (Phase1 Top5 × Phase2 Top5 全組合)
  → 多輪循環：Phase4 最佳 → 再次縮小搜索範圍 → 重複

執行：
  cd C:/Users/xx/Desktop/永豐-自動化交易/ultra-trader-src
  python scripts/auto_optimize_v3.py [--rounds 3]
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

# ── 基準參數（v3 當前最佳，固定不動） ──────────────────────────────────────
BASE = dict(
    sl_atr=2.5, tp_atr=10.0,
    min_adx=20.0, afternoon_min_adx=32.0, min_di_gap=5.0,
    squeeze_ratio=0.90, expand_ratio=1.08, min_vol_ratio=1.0,
    pullback_ema_gap=0.3, breakeven_trigger_atr=999,
    trend_filter=True, ema200_margin_atr=0.0,
    use_momentum_score=True,
    momentum_rsi_bull=52.0, momentum_rsi_bear=48.0, momentum_session_atr=0.5,
    point_value=10.0,
)

# 基準值（v3 已知結果）
BASELINE = dict(
    n=121, wr=56.2, pf=1.796, net=157_250,
    avg_w=5219, avg_l=3729, rr=1.40, n6=8
)

INITIAL_BALANCE = 200_000
INSTRUMENT      = 'TMF'
RISK_PROFILE    = 'tmf_3x'

# ── 搜索空間定義 ─────────────────────────────────────────────────────────────
# Phase 1: Trail 參數（主要影響 avg_w）
TRAIL_TRIGGER_VALS = [0.5, 0.7, 0.8, 1.0, 1.2, 1.5]
TRAIL_DIST_VALS    = [1.0, 1.25, 1.5, 1.75, 2.0, 2.5]

# Phase 2: Early Cut 參數（主要影響 avg_l）
EARLY_BARS_VALS    = [12, 15, 20, 25, 30]
EARLY_LOSS_VALS    = [0.8, 1.0, 1.2, 1.5, 1.8]

# Phase 3: Hard Stop（補充控制）
HARD_STOP_VALS     = [2500, 3000, 3500, 4000, 5000]

RESULTS_DIR = ROOT / 'data' / 'optimizer_results_v3'
RESULTS_DIR.mkdir(parents=True, exist_ok=True)


# ── 評分函數 ──────────────────────────────────────────────────────────────────
def composite_score(r: dict) -> float:
    """
    綜合評分（越高越好）
    主要維度：
      rr_score   — 盈虧比，目標 1.6+，權重 30%
      net_score  — 淨利，目標 ≥200K（100% 報酬），權重 25%
      n6_score   — ≥6%月數，目標 ≥10/28，權重 30%
      wr_score   — 勝率加分（≥55% 基準），權重 10%
      pf_score   — PF 加分，權重 5%
    約束懲罰：
      WR < 50% → 大幅懲罰
      PF < 1.5 → 中度懲罰
      net < 120K → 懲罰
    """
    rr  = r.get('rr', 0.0)
    net = r.get('net', 0.0)
    n6  = r.get('n6', 0)
    wr  = r.get('wr', 0.0)
    pf  = r.get('pf', 0.0)

    rr_score  = min(rr / 2.0, 1.0)          # 0→0%, 1.6→80%, 2.0→100%
    net_score = min(max(net, 0) / 200_000, 1.0)  # 0→0%, 200K→100%
    n6_score  = n6 / 28                     # 0→0%, 28→100%
    wr_score  = max(0, wr - 50) / 15        # 50%→0, 65%→100%
    pf_score  = min(max(pf - 1.0, 0) / 1.0, 1.0)  # 1.0→0, 2.0→100%

    score = (rr_score * 3.0 + net_score * 2.5 + n6_score * 3.0
             + wr_score * 1.0 + pf_score * 0.5)

    # 懲罰
    if wr < 50:
        score -= (50 - wr) * 0.3
    if pf < 1.5:
        score -= (1.5 - pf) * 0.5
    if net < 120_000:
        score -= (120_000 - net) / 10_000

    return round(score, 4)


# ── 執行單一回測 ──────────────────────────────────────────────────────────────
def run_backtest(df_5m, ind, params: dict) -> dict | None:
    """執行回測並計算所有評估指標，失敗回傳 None"""
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

        # 月度計算
        monthly = defaultdict(lambda: {'n':0,'wins':0,'pnl':0.0,'bal_start':0.0})
        bal = float(INITIAL_BALANCE)
        month_order = []
        for t in trades:
            ym = t['entry_time'][:7]
            if ym not in monthly:
                monthly[ym]['bal_start'] = bal
                month_order.append(ym)
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

        result = dict(
            n=n, wr=round(wr,1), pf=round(pf,3),
            net=round(net,0), avg_w=round(avg_w,0), avg_l=round(avg_l,0),
            rr=round(rr,3), n6=n6, ng=ng, worst_m=round(worst_m,1),
            **{k: params[k] for k in params if k not in BASE},
        )
        result['score'] = composite_score(result)
        return result

    except Exception as e:
        return None


# ── 批次掃描 ──────────────────────────────────────────────────────────────────
def sweep(df_5m, ind, param_list: list[dict], phase_name: str) -> list[dict]:
    results = []
    total = len(param_list)
    t0 = time.time()
    for i, p in enumerate(param_list, 1):
        r = run_backtest(df_5m, ind, p)
        if r:
            results.append(r)
        elapsed = time.time() - t0
        eta     = elapsed / i * (total - i) if i > 0 else 0
        print(f'\r  [{phase_name}] {i}/{total} '
              f'OK:{len(results)} ETA:{eta:.0f}s  ', end='', flush=True)
    print()
    return sorted(results, key=lambda x: x['score'], reverse=True)


# ── 結果印表 ──────────────────────────────────────────────────────────────────
def print_top(results: list, n: int = 10, phase: str = ''):
    header = f'  {"trail_t":>7} {"trail_d":>7} {"ec_bars":>7} {"ec_loss":>7} {"hard":>6} | {"n":>4} {"WR":>5} {"PF":>6} {"淨利":>9} {"報酬":>7} | {"RR":>5} {"N6":>4} {"最差月":>7} | {"Score":>7}'
    print(f'\n  ─ Top {n} [{phase}] ─')
    print(header)
    for r in results[:n]:
        tt = r.get('trail_trigger_atr', BASE.get('trail_trigger_atr','?'))
        td = r.get('trail_dist_atr', BASE.get('trail_dist_atr','?'))
        eb = r.get('early_cut_bars', BASE.get('early_cut_bars','?'))
        el = r.get('early_cut_loss_atr', BASE.get('early_cut_loss_atr','?'))
        hs = r.get('max_loss_twd', BASE.get('max_loss_twd','?'))
        vs_rr  = f"+{r['rr']-BASELINE['rr']:+.2f}"  if r['rr']  > BASELINE['rr']  else f"{r['rr']-BASELINE['rr']:+.2f}"
        vs_net = f"+{(r['net']-BASELINE['net'])//1000:.0f}K" if r['net'] > BASELINE['net'] else f"{(r['net']-BASELINE['net'])//1000:.0f}K"
        print(f'  {tt:>7} {td:>7} {eb:>7} {el:>7} {hs:>6} | '
              f'{r["n"]:>4} {r["wr"]:>5.1f}% {r["pf"]:>6.3f} {r["net"]:>+9,.0f} {r["net"]/INITIAL_BALANCE*100:>+6.1f}% | '
              f'{r["rr"]:>5.3f} {r["n6"]:>4}/28 {r["worst_m"]:>+7.1f}% | {r["score"]:>7.3f}')
    print()


# ── 主循環 ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rounds', type=int, default=3, help='優化輪數（預設 3）')
    parser.add_argument('--phase1-only', action='store_true', help='只執行 Phase1 Trail 掃描')
    args = parser.parse_args()

    print('\n' + '='*80)
    print('  BreakoutTrend v3 自動循環優化系統')
    print(f'  目標: RR≥1.60 | 淨利≥200K(+100%) | WR≥53% | N6≥10/28')
    print(f'  基準: RR=1.40 | 淨利=157K(+78.6%) | WR=56.2% | N6=8/28')
    print('='*80)

    # ── 資料載入 ──────────────────────────────────────────────────────────────
    DATA_PATH = ROOT / 'data' / 'historical' / 'tmf_20260411_full_1m.csv'
    print(f'\n[INIT] 載入資料 {DATA_PATH.name}...')
    df_1m = pd.read_csv(DATA_PATH, parse_dates=['datetime']).sort_values('datetime').reset_index(drop=True)
    df    = df_1m.set_index('datetime')
    df_5m = df.resample('5min').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
    df_5m = df_5m.between_time('08:45', '13:30').reset_index()
    print(f'  5分鐘K線: {len(df_5m):,} 根')

    print('[INIT] 預計算指標...')
    ind = precompute_all(df_5m, verbose=False)
    print('  完成')

    global_best   = None
    all_top_trail = []  # 跨輪保留最佳 trail 組合
    all_top_early = []  # 跨輪保留最佳 early 組合
    ts = datetime.now().strftime('%Y%m%d_%H%M')

    # 當前最佳 trail/early/hard（Phase1-3 迭代更新）
    best_trail_trigger = 1.0
    best_trail_dist    = 1.25
    best_early_bars    = 25
    best_early_loss    = 1.5
    best_hard_stop     = 4000.0

    for round_num in range(1, args.rounds + 1):
        print(f'\n{"="*80}')
        print(f'  ROUND {round_num}/{args.rounds}')
        print(f'{"="*80}')

        # ════════════════════════════════════════════════════════════════
        # Phase 1: Trail 參數掃描  (固定 early_cut + hard_stop)
        # ════════════════════════════════════════════════════════════════
        print(f'\n  ── Phase 1: Trail 參數掃描 ({len(TRAIL_TRIGGER_VALS)}×{len(TRAIL_DIST_VALS)}={len(TRAIL_TRIGGER_VALS)*len(TRAIL_DIST_VALS)} 組) ──')

        if round_num == 1:
            tt_vals = TRAIL_TRIGGER_VALS
            td_vals = TRAIL_DIST_VALS
        else:
            # Zoom-in: 以上一輪最佳為中心 ±2 步
            step_t = 0.1
            step_d = 0.15
            tt_vals = sorted(set(round(best_trail_trigger + i*step_t, 2)
                                 for i in range(-2, 3) if 0.3 <= best_trail_trigger + i*step_t <= 2.0))
            td_vals = sorted(set(round(best_trail_dist + i*step_d, 2)
                                 for i in range(-2, 3) if 0.5 <= best_trail_dist + i*step_d <= 3.0))
            print(f'    Zoom: trail_trigger={tt_vals}')
            print(f'    Zoom: trail_dist={td_vals}')

        p1_params = []
        for tt, td in itertools.product(tt_vals, td_vals):
            p = dict(BASE,
                     trail_trigger_atr=tt, trail_dist_atr=td,
                     early_cut_bars=best_early_bars, early_cut_loss_atr=best_early_loss,
                     max_loss_twd=best_hard_stop)
            p1_params.append(p)

        r1 = sweep(df_5m, ind, p1_params, f'R{round_num}-P1-Trail')
        all_top_trail = sorted(all_top_trail + r1[:5], key=lambda x: x['score'], reverse=True)[:10]
        if r1:
            best_trail_trigger = r1[0].get('trail_trigger_atr', best_trail_trigger)
            best_trail_dist    = r1[0].get('trail_dist_atr', best_trail_dist)
            print_top(r1, n=8, phase=f'R{round_num} Phase1 Trail')

        if args.phase1_only:
            print('  (--phase1-only 模式，跳過 Phase2/3/4)')
            global_best = r1[0] if r1 else global_best
            continue

        # ════════════════════════════════════════════════════════════════
        # Phase 2: Early Cut 掃描  (固定最佳 trail + hard_stop)
        # ════════════════════════════════════════════════════════════════
        print(f'\n  ── Phase 2: Early Cut 參數掃描 ({len(EARLY_BARS_VALS)}×{len(EARLY_LOSS_VALS)}={len(EARLY_BARS_VALS)*len(EARLY_LOSS_VALS)} 組) ──')

        if round_num == 1:
            eb_vals = EARLY_BARS_VALS
            el_vals = EARLY_LOSS_VALS
        else:
            step_b = 3
            step_l = 0.15
            eb_vals = sorted(set(max(8, min(50, best_early_bars + i*step_b))
                                 for i in range(-2, 3)))
            el_vals = sorted(set(round(max(0.5, min(3.0, best_early_loss + i*step_l)), 2)
                                 for i in range(-2, 3)))

        p2_params = []
        for eb, el in itertools.product(eb_vals, el_vals):
            p = dict(BASE,
                     trail_trigger_atr=best_trail_trigger, trail_dist_atr=best_trail_dist,
                     early_cut_bars=eb, early_cut_loss_atr=el,
                     max_loss_twd=best_hard_stop)
            p2_params.append(p)

        r2 = sweep(df_5m, ind, p2_params, f'R{round_num}-P2-EarlyCut')
        all_top_early = sorted(all_top_early + r2[:5], key=lambda x: x['score'], reverse=True)[:10]
        if r2:
            best_early_bars = r2[0].get('early_cut_bars', best_early_bars)
            best_early_loss = r2[0].get('early_cut_loss_atr', best_early_loss)
            print_top(r2, n=8, phase=f'R{round_num} Phase2 EarlyCut')

        # ════════════════════════════════════════════════════════════════
        # Phase 3: Hard Stop 調校  (固定最佳 trail + early_cut)
        # ════════════════════════════════════════════════════════════════
        print(f'\n  ── Phase 3: Hard Stop 調校 ({len(HARD_STOP_VALS)} 組) ──')

        p3_params = []
        for hs in HARD_STOP_VALS:
            p = dict(BASE,
                     trail_trigger_atr=best_trail_trigger, trail_dist_atr=best_trail_dist,
                     early_cut_bars=best_early_bars, early_cut_loss_atr=best_early_loss,
                     max_loss_twd=float(hs))
            p3_params.append(p)

        r3 = sweep(df_5m, ind, p3_params, f'R{round_num}-P3-HardStop')
        if r3:
            best_hard_stop = r3[0].get('max_loss_twd', best_hard_stop)
            print_top(r3, n=5, phase=f'R{round_num} Phase3 HardStop')

        # ════════════════════════════════════════════════════════════════
        # Phase 4: 聯合驗證 (Top5 trail × Top5 early_cut 全組合)
        # ════════════════════════════════════════════════════════════════
        print(f'\n  ── Phase 4: 聯合驗證 (Top5 Trail × Top5 EarlyCut) ──')

        top_trail = all_top_trail[:5]
        top_early = all_top_early[:5]

        p4_params = []
        seen = set()
        for tr in top_trail:
            for ec in top_early:
                tt = tr.get('trail_trigger_atr', best_trail_trigger)
                td = tr.get('trail_dist_atr', best_trail_dist)
                eb = ec.get('early_cut_bars', best_early_bars)
                el = ec.get('early_cut_loss_atr', best_early_loss)
                key = (tt, td, eb, el, best_hard_stop)
                if key in seen:
                    continue
                seen.add(key)
                p = dict(BASE,
                         trail_trigger_atr=tt, trail_dist_atr=td,
                         early_cut_bars=eb, early_cut_loss_atr=el,
                         max_loss_twd=best_hard_stop)
                p4_params.append(p)

        r4 = sweep(df_5m, ind, p4_params, f'R{round_num}-P4-Joint')
        if r4:
            print_top(r4, n=10, phase=f'R{round_num} Phase4 Joint')

            round_best = r4[0]
            if global_best is None or round_best['score'] > global_best['score']:
                global_best = round_best
                best_trail_trigger = round_best.get('trail_trigger_atr', best_trail_trigger)
                best_trail_dist    = round_best.get('trail_dist_atr', best_trail_dist)
                best_early_bars    = round_best.get('early_cut_bars', best_early_bars)
                best_early_loss    = round_best.get('early_cut_loss_atr', best_early_loss)
                best_hard_stop     = round_best.get('max_loss_twd', best_hard_stop)
                print(f'  ★ 新全域最佳！Score={round_best["score"]:.3f}  '
                      f'RR={round_best["rr"]:.3f}  淨利={round_best["net"]:+,.0f}  N6={round_best["n6"]}/28')

            # 達標判斷
            gb = global_best
            target_met = (gb['rr'] >= 1.6 and gb['net'] >= 200_000 and gb['wr'] >= 53 and gb['n6'] >= 10)
            if target_met:
                print(f'\n  ★★★ 所有目標達成！提前結束循環 ★★★')
                break
        else:
            print('  (無有效結果)')

    # ════════════════════════════════════════════════════════════════════
    # 最終報告
    # ════════════════════════════════════════════════════════════════════
    print('\n' + '='*80)
    print('  最終結果')
    print('='*80)

    if global_best:
        gb = global_best
        print(f'\n  最佳參數組合：')
        print(f'    trail_trigger_atr  = {gb.get("trail_trigger_atr", "?")} （基準 1.00）')
        print(f'    trail_dist_atr     = {gb.get("trail_dist_atr", "?")} （基準 1.25）')
        print(f'    early_cut_bars     = {gb.get("early_cut_bars", "?")} （基準 25）')
        print(f'    early_cut_loss_atr = {gb.get("early_cut_loss_atr", "?")} （基準 1.50）')
        print(f'    max_loss_twd       = {gb.get("max_loss_twd", "?")} （基準 4000）')
        print()
        print(f'  績效對比：')
        print(f'    {"指標":<12} {"基準 v3":>10} {"優化後":>10} {"差異":>10}')
        print(f'    {"─"*44}')
        print(f'    {"筆數":<12} {BASELINE["n"]:>10} {gb["n"]:>10}')
        print(f'    {"勝率":<12} {BASELINE["wr"]:>9.1f}% {gb["wr"]:>9.1f}%  {gb["wr"]-BASELINE["wr"]:>+8.1f}%')
        print(f'    {"PF":<12} {BASELINE["pf"]:>10.3f} {gb["pf"]:>10.3f}  {gb["pf"]-BASELINE["pf"]:>+8.3f}')
        print(f'    {"淨利(TWD)":<12} {BASELINE["net"]:>+10,.0f} {gb["net"]:>+10,.0f}  {gb["net"]-BASELINE["net"]:>+8,.0f}')
        print(f'    {"總報酬":<12} {BASELINE["net"]/INITIAL_BALANCE*100:>9.1f}% {gb["net"]/INITIAL_BALANCE*100:>9.1f}%  {(gb["net"]-BASELINE["net"])/INITIAL_BALANCE*100:>+8.1f}%')
        print(f'    {"盈虧比(RR)":<12} {BASELINE["rr"]:>10.2f} {gb["rr"]:>10.3f}  {gb["rr"]-BASELINE["rr"]:>+8.3f}')
        print(f'    {"≥6%月數":<12} {BASELINE["n6"]:>9}/28 {gb["n6"]:>9}/28  {gb["n6"]-BASELINE["n6"]:>+8}')
        print(f'    {"最差月":<12} {"-6.9":>9}% {gb["worst_m"]:>9.1f}%')
        print()

        # 目標達成檢查
        checks = [
            ('RR ≥ 1.60',       gb['rr']  >= 1.60,  f'{gb["rr"]:.3f}'),
            ('淨利 ≥ 200K',     gb['net'] >= 200_000, f'{gb["net"]:+,.0f}'),
            ('WR ≥ 53%',        gb['wr']  >= 53,     f'{gb["wr"]:.1f}%'),
            ('≥6%月數 ≥ 10/28', gb['n6']  >= 10,     f'{gb["n6"]}/28'),
            ('PF ≥ 1.60',       gb['pf']  >= 1.60,   f'{gb["pf"]:.3f}'),
        ]
        print('  目標達成狀況：')
        for label, ok, val in checks:
            status = '✅' if ok else '❌'
            print(f'    {status} {label:<20} → {val}')

        # 儲存結果
        result_path = RESULTS_DIR / f'best_v3_{ts}.json'
        with open(result_path, 'w', encoding='utf-8') as f:
            json.dump(global_best, f, indent=2, ensure_ascii=False, default=str)
        print(f'\n  結果已儲存：{result_path}')

        # 輸出可直接貼入代碼的參數
        print('\n  ─ 可貼入 breakout.py 的參數 ─')
        print(f'  trail_trigger_atr={gb.get("trail_trigger_atr")},')
        print(f'  trail_dist_atr={gb.get("trail_dist_atr")},')
        print(f'  early_cut_bars={gb.get("early_cut_bars")},')
        print(f'  early_cut_loss_atr={gb.get("early_cut_loss_atr")},')
        print(f'  max_loss_twd={gb.get("max_loss_twd")},')

    else:
        print('  無有效結果（資料問題？）')

    print('\n' + '='*80)


if __name__ == '__main__':
    main()
