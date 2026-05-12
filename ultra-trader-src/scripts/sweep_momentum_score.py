"""
動能評分參數掃描
比較：EMA200-only vs EMA200+動能評分（RSI+盤中漲幅）的各種組合
基準：ec=25 loss=1.5 早切止損，5min K線
"""
import sys, json
from pathlib import Path
from collections import defaultdict
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

from core.logger import setup_logger
setup_logger(console_level='CRITICAL')

import pandas as pd
from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.breakout import BreakoutTrendStrategy

# ── 載入 5min 資料
DATA_PATH = Path('data/historical/tmf_20260411_full_1m.csv')
print("Loading data...")
df_1m = pd.read_csv(DATA_PATH, parse_dates=['datetime']).sort_values('datetime').reset_index(drop=True)
df = df_1m.set_index('datetime')
df_5m = df.resample('5min').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
df_5m = df_5m.between_time('08:45','13:30').reset_index()
print(f"  5min bars: {len(df_5m)}")

ind = precompute_all(df_5m, verbose=False)

# 共用基礎參數
BASE = dict(
    sl_atr=2.5, tp_atr=10.0,
    trail_trigger_atr=1.0, trail_dist_atr=1.25, max_bars=80,
    min_adx=20.0, afternoon_min_adx=32.0, min_di_gap=5.0,
    squeeze_ratio=0.90, expand_ratio=1.08, min_vol_ratio=1.0,
    pullback_ema_gap=0.3, breakeven_trigger_atr=999,
    early_cut_bars=25, early_cut_loss_atr=1.5,
    trend_filter=True, ema200_margin_atr=0.0,
)


def run(label, **kwargs):
    p = dict(BASE, **kwargs)
    strat = BreakoutTrendStrategy(**p)
    engine = FastBacktestEngine(initial_balance=200_000, instrument='TMF')
    r = engine.run(df_5m, ind, strat, 'tmf_3x')
    trades = r.trades
    n = len(trades)
    if n == 0:
        print(f"{label}: 0 trades"); return None
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    gp = sum(t['pnl'] for t in wins)
    gl = abs(sum(t['pnl'] for t in losses)) if losses else 1
    pf = gp / gl
    wr = len(wins) / n * 100
    net = r.final_balance - 200_000
    monthly = defaultdict(lambda: {'n':0,'wins':0,'pnl':0})
    for t in trades:
        ym = t['entry_time'][:7]
        monthly[ym]['n'] += 1; monthly[ym]['pnl'] += t['pnl']
        if t['pnl'] > 0: monthly[ym]['wins'] += 1
    bal = 200_000.0; n6 = 0; ng = 0; worst = 0
    for ym in sorted(monthly):
        m = monthly[ym]
        ret = m['pnl'] / bal * 100
        if ret >= 6: n6 += 1
        if m['pnl'] > 0: ng += 1
        if ret < worst: worst = ret
        bal += m['pnl']
    print(f"{label:<55} | {n:>3}筆 WR={wr:>5.1f}% PF={pf:.3f} 淨利={net:>+8,.0f} "
          f"報酬={net/200_000*100:>+5.1f}% G={ng}/28 >=6%={n6:>2} 最差={worst:>+5.1f}%")
    return dict(label=label, n=n, wr=wr, pf=pf, net=net, n6=n6, ng=ng, worst=worst)


print("\n" + "="*110)
print("基準對比")
print("="*110)

# 0. 無趨勢過濾
run("0. 無 EMA200 過濾", trend_filter=False, use_momentum_score=False)

# 1. EMA200-only（上次最佳，baseline）
run("1. EMA200-only（baseline）", use_momentum_score=False)

# 2. EMA200 + 動能評分（預設參數）
run("2. EMA200 + 動能評分 rsi55/45 sess1.5", use_momentum_score=True,
    momentum_rsi_bull=55.0, momentum_rsi_bear=45.0, momentum_session_atr=1.5)

print("\n" + "="*110)
print("RSI 門檻掃描（session=1.5）")
print("="*110)

for rsi_bull, rsi_bear in [(52,48),(55,45),(58,42),(60,40),(62,38)]:
    run(f"   RSI bull={rsi_bull} bear={rsi_bear} sess=1.5",
        use_momentum_score=True,
        momentum_rsi_bull=rsi_bull, momentum_rsi_bear=rsi_bear,
        momentum_session_atr=1.5)

print("\n" + "="*110)
print("盤中動能閾值掃描（rsi=55/45）")
print("="*110)

for sess in [0.5, 1.0, 1.5, 2.0, 2.5, 3.0]:
    run(f"   sess={sess} rsi=55/45",
        use_momentum_score=True,
        momentum_rsi_bull=55.0, momentum_rsi_bear=45.0,
        momentum_session_atr=sess)

print("\n" + "="*110)
print("組合掃描（Top candidates）")
print("="*110)

combos = [
    (52, 48, 1.0), (52, 48, 1.5), (52, 48, 2.0),
    (55, 45, 1.0), (55, 45, 1.5), (55, 45, 2.0),
    (58, 42, 1.0), (58, 42, 1.5), (58, 42, 2.0),
    (60, 40, 1.0), (60, 40, 1.5), (60, 40, 2.0),
]
results = []
for rsi_bull, rsi_bear, sess in combos:
    r = run(f"   rsi={rsi_bull}/{rsi_bear} sess={sess}",
            use_momentum_score=True,
            momentum_rsi_bull=rsi_bull, momentum_rsi_bear=rsi_bear,
            momentum_session_atr=sess)
    if r:
        results.append(r)

print("\n" + "="*110)
print("Top 5（以 ≥6% 月數排序）")
print("="*110)
top5 = sorted(results, key=lambda x: (x['n6'], x['net']), reverse=True)[:5]
for r in top5:
    print(f"  {r['label']:<55} n6={r['n6']} net={r['net']:>+,.0f} PF={r['pf']:.3f} 最差={r['worst']:+.1f}%")
