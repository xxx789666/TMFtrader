"""
比較 Version A (min_adx=23) vs Version C (開盤 08:45-09:15 ADX=20)
聚焦 2026-04-14 ~ 2026-04-25 (最近 2 週)
"""
import sys
from pathlib import Path
from datetime import time
from typing import Optional

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding='utf-8')

from core.logger import setup_logger
setup_logger(console_level='CRITICAL')

import pandas as pd
from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.breakout import BreakoutTrendStrategy
from strategy.base import Signal

# ── v6b 參數 ────────────────────────────────────────────────
PARAMS = dict(
    sl_atr=2.5, tp_atr=10.0,
    trail_trigger_atr=1.2, trail_dist_atr=1.25,
    max_bars=80,
    min_adx=23.0, afternoon_min_adx=30.0,
    min_di_gap=10.0,
    squeeze_ratio=0.90,
    expand_ratio=1.18,
    min_vol_ratio=1.0,
    pullback_ema_gap=0.20,
    breakeven_trigger_atr=999,
    early_cut_bars=40,
    early_cut_loss_atr=1.5,
    max_loss_twd=4000.0,
    squeeze_grace_bars=1,
    trend_filter=True, ema200_margin_atr=0.0,
    use_momentum_score=True,
    momentum_rsi_bull=52.0, momentum_rsi_bear=46.0,
    momentum_session_atr=0.5,
    point_value=10.0,
    scale_out_trigger_atr=0.0, scale_out_qty=1,
)

class BreakoutOpeningRelaxed(BreakoutTrendStrategy):
    """開盤 08:45-09:15 放寬 ADX 至 opening_adx，其餘不變"""
    def __init__(self, opening_adx: float = 20.0, **kwargs):
        self._opening_adx = opening_adx
        super().__init__(**kwargs)

    def on_kbar(self, kbar, snapshot, **kwargs) -> Optional[Signal]:
        bar_t = kbar.datetime.time()
        in_open_window = time(8, 45) <= bar_t <= time(9, 15)
        if in_open_window and self._opening_adx < self.min_adx:
            orig = self.min_adx
            self.min_adx = self._opening_adx
            result = super().on_kbar(kbar, snapshot, **kwargs)
            self.min_adx = orig
            return result
        return super().on_kbar(kbar, snapshot, **kwargs)


# ── 載入資料 ─────────────────────────────────────────────────
print('[1/3] 載入 5m parquet...')
df_5m = pd.read_parquet(ROOT / 'data' / 'historical' / 'tmf_5y_5m.parquet')
df_5m = df_5m.sort_values('datetime').reset_index(drop=True)

# 只取日盤 08:45-13:45
df_5m = df_5m.set_index('datetime')
df_day = df_5m.between_time('08:45', '13:45').reset_index()
print(f'  全期日盤 5m: {len(df_day):,} 根')
print(f'  資料範圍: {df_day["datetime"].iloc[0]} ~ {df_day["datetime"].iloc[-1]}')

# 需要足夠的暖機資料（strategy 需要 80 根 bars），取 2026-03-01 起
df_full = df_day[df_day['datetime'] >= '2026-03-01'].reset_index(drop=True)

# ── 預計算指標 ───────────────────────────────────────────────
print('[2/3] 預計算指標...')
ind = precompute_all(df_full, verbose=False)

INITIAL_BALANCE = 200_000
INSTRUMENT = 'TMF'
RISK_PROFILE = 'tmf_3x'

# ── 執行兩個版本 ─────────────────────────────────────────────
print('[3/3] 執行回測...')

engine_a = FastBacktestEngine(initial_balance=INITIAL_BALANCE, instrument=INSTRUMENT)
strat_a = BreakoutTrendStrategy(**PARAMS)
result_a = engine_a.run(df_full, ind, strat_a, RISK_PROFILE)

engine_c = FastBacktestEngine(initial_balance=INITIAL_BALANCE, instrument=INSTRUMENT)
strat_c = BreakoutOpeningRelaxed(opening_adx=20.0, **PARAMS)
result_c = engine_c.run(df_full, ind, strat_c, RISK_PROFILE)

# ── 篩選只看 04/14-04/25 ─────────────────────────────────────
TARGET_START = '2026-04-14'
TARGET_END   = '2026-04-25'

def filter_trades(trades, start, end):
    return [t for t in trades
            if start <= t['entry_time'][:10] <= end]

trades_a = filter_trades(result_a.trades, TARGET_START, TARGET_END)
trades_c = filter_trades(result_c.trades, TARGET_START, TARGET_END)

# ── 也看全期差異 ─────────────────────────────────────────────
all_a = result_a.trades
all_c = result_c.trades

def stats(trades):
    if not trades:
        return dict(n=0, wins=0, wr=0, pnl=0, pf=0, avg_r=0)
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    gp = sum(t['pnl'] for t in wins)
    gl = abs(sum(t['pnl'] for t in losses))
    pf = gp / gl if gl else 0
    avg_r = sum(t['pnl'] for t in trades) / len(trades)
    return dict(n=len(trades), wins=len(wins),
                wr=len(wins)/len(trades)*100,
                pnl=sum(t['pnl'] for t in trades),
                pf=pf, avg_r=avg_r)

sa2 = stats(trades_a)
sc2 = stats(trades_c)
sa_all = stats(all_a)
sc_all = stats(all_c)

print()
print('=' * 65)
print(f'  聚焦 {TARGET_START} ~ {TARGET_END}（最近 2 週）')
print('=' * 65)
print(f'  {"":20} {"Version A (ADX=23)":>22} {"Version C (ADX=20 開盤)":>22}')
print(f'  {"交易筆數":20} {sa2["n"]:>22} {sc2["n"]:>22}')
print(f'  {"勝率":20} {sa2["wr"]:>21.1f}% {sc2["wr"]:>21.1f}%')
print(f'  {"淨損益 (TWD)":20} {sa2["pnl"]:>+22,.0f} {sc2["pnl"]:>+22,.0f}')
print(f'  {"PF":20} {sa2["pf"]:>22.3f} {sc2["pf"]:>22.3f}')
print(f'  {"平均每筆 (TWD)":20} {sa2["avg_r"]:>+22,.0f} {sc2["avg_r"]:>+22,.0f}')
print()
print('  --- 個別交易明細（Version C 新增的單） ---')

# 找 Version C 多出來的交易
a_times = {t['entry_time'][:16] for t in trades_a}
c_new = [t for t in trades_c if t['entry_time'][:16] not in a_times]

if not trades_a and not trades_c:
    print('  兩個版本在此期間均無交易')
elif not c_new:
    if trades_c:
        print('  Version C 無新增交易（與 A 完全相同）')
    else:
        print('  兩個版本均無交易')
else:
    for t in c_new:
        side = 'LONG' if t['side'] == 'long' else 'SHORT'
        pnl_s = f"+{t['pnl']:,.0f}" if t['pnl'] >= 0 else f"{t['pnl']:,.0f}"
        print(f"  {t['entry_time'][:16]} {side:5} {t['entry_price']:,.0f}→{t['exit_price']:,.0f}  {pnl_s} TWD  [{t['reason'][:30]}]")

if trades_a:
    print()
    print('  --- Version A 交易 ---')
    for t in trades_a:
        side = 'LONG' if t['side'] == 'long' else 'SHORT'
        pnl_s = f"+{t['pnl']:,.0f}" if t['pnl'] >= 0 else f"{t['pnl']:,.0f}"
        print(f"  {t['entry_time'][:16]} {side:5} {t['entry_price']:,.0f}→{t['exit_price']:,.0f}  {pnl_s} TWD  [{t['reason'][:30]}]")

print()
print('=' * 65)
print(f'  全期（2026-03-01 ~ {df_full["datetime"].iloc[-1].strftime("%Y-%m-%d")}）比對')
print('=' * 65)
print(f'  {"":20} {"Version A":>22} {"Version C":>22}')
print(f'  {"交易筆數":20} {sa_all["n"]:>22} {sc_all["n"]:>22}')
print(f'  {"勝率":20} {sa_all["wr"]:>21.1f}% {sc_all["wr"]:>21.1f}%')
print(f'  {"淨損益 (TWD)":20} {sa_all["pnl"]:>+22,.0f} {sc_all["pnl"]:>+22,.0f}')
print(f'  {"PF":20} {sa_all["pf"]:>22.3f} {sc_all["pf"]:>22.3f}')
print(f'  {"平均每筆 (TWD)":20} {sa_all["avg_r"]:>+22,.0f} {sc_all["avg_r"]:>+22,.0f}')
