"""
Connors RSI(2) 策略測試
P0: 成本會計驗證（動態稅模型，commit a95ae44 起）
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import math
from core.position import PositionManager, Side
from core.instrument_config import INSTRUMENT_SPECS


def test_cost_accounting_dynamic_tax():
    """
    TMF spec：point_value=10, commission=18/邊, tax_rate_pct=0.00002
    進場 22000，平倉 22010，1 口：
      毛利       = (22010-22000) × 10 × 1 = 100
      fee_comm   = 18 × 2 邊 × 1 口 = 36
      entry_tax  = ceil(22000 × 10 × 0.00002) = ceil(4.4) = 5
      exit_tax   = ceil(22010 × 10 × 0.00002) = ceil(4.402) = 5
      fee_tax    = (5 + 5) × 1 = 10
      commission(全部扣項) = 36 + 10 = 46
      net_pnl   = 100 - 46 = 54
    """
    spec = INSTRUMENT_SPECS["TMF"]
    pm = PositionManager(instruments=["TMF"], configs={"TMF": spec}, initial_balance=200_000)
    pm.open_position("TMF", Side.LONG, price=22000.0, quantity=1,
                     stop_loss=21950, take_profit=22050, timestamp=None)
    trade = pm.close_position("TMF", 22010.0, "test", None)

    assert trade.pnl == 100.0,        f"pnl: expected 100.0, got {trade.pnl}"
    assert trade.commission == 46.0,  f"commission(全扣項): expected 46.0, got {trade.commission}"
    assert trade.net_pnl == 54.0,     f"net_pnl: expected 54.0, got {trade.net_pnl}"


def test_cost_accounting_high_price_tax_scales():
    """指數越高、稅越高。@45000 → tax=9/邊、commission=18*2+9*2=54、net_pnl 受影響"""
    spec = INSTRUMENT_SPECS["TMF"]
    pm = PositionManager(instruments=["TMF"], configs={"TMF": spec}, initial_balance=200_000)
    pm.open_position("TMF", Side.LONG, price=45000.0, quantity=1,
                     stop_loss=44950, take_profit=45050, timestamp=None)
    trade = pm.close_position("TMF", 45010.0, "test", None)
    # entry_tax = ceil(45000*10*0.00002) = ceil(9.0) = 9
    # exit_tax  = ceil(45010*10*0.00002) = ceil(9.002) = 10
    # fee_tax = 19, fee_comm = 36, commission = 55
    expected_entry_tax = math.ceil(45000 * 10 * 0.00002)
    expected_exit_tax  = math.ceil(45010 * 10 * 0.00002)
    expected_comm = 36 + (expected_entry_tax + expected_exit_tax)
    assert trade.commission == float(expected_comm), \
        f"@45k commission: expected {expected_comm}, got {trade.commission}"


def test_rsi_warmup_returns_50():
    """暖身期（update 數 < period）回中性 50.0"""
    from strategy.connors_rsi2 import _RsiState
    s = _RsiState(period=2)
    assert s.rsi == 50.0      # 還沒 update
    s.update(100.0)
    assert s.rsi == 50.0      # 才 1 個 update，不足 period


def test_rsi_full_gain_approaches_100():
    """連續上漲 → RSI 趨向 100"""
    from strategy.connors_rsi2 import _RsiState
    s = _RsiState(period=2)
    for p in [100, 101, 102, 103, 104, 105]:
        s.update(float(p))
    assert s.rsi > 95.0


def test_rsi_full_loss_approaches_0():
    """連續下跌 → RSI 趨向 0"""
    from strategy.connors_rsi2 import _RsiState
    s = _RsiState(period=2)
    for p in [100, 99, 98, 97, 96, 95]:
        s.update(float(p))
    assert s.rsi < 5.0


def test_rsi_state_does_NOT_reset_on_call():
    """_RsiState 本身無 reset/換日邏輯 — 連續 Wilder smoothing。
    換日 reset 是策略層 (_trades_today / _cooldown / _session_bar) 才做。"""
    from strategy.connors_rsi2 import _RsiState
    s = _RsiState(period=2)
    for p in [100, 101, 102, 103, 104]:
        s.update(float(p))
    high_rsi = s.rsi
    assert high_rsi > 80.0
    # 沒有 reset() method
    assert not hasattr(s, "reset"), "_RsiState 不應有 reset() — 跨日連續是設計"


# ── Shared helpers (also used by Tasks 4, 5) ────────────────────────────────
def _make_snap(price, adx, atr, ts):
    from core.market_data import MarketSnapshot
    s = MarketSnapshot()
    s.price = price; s.adx = adx; s.atr = atr; s.timestamp = ts; s.volume = 100
    return s


def _kbar(ts, p, vol=100):
    from core.market_data import KBar
    return KBar(ts, p, p+1, p-1, p, vol)


# ── Task 3 entry tests ──────────────────────────────────────────────────────
def test_entry_long_on_low_rsi():
    """RSI(2) 跌破閾值 → BUY，stop 在進場價下方"""
    from strategy.connors_rsi2 import ConnorsRsi2Strategy
    from datetime import datetime
    strat = ConnorsRsi2Strategy(rsi_low=10, sl_atr=2.0, allow_short=False)
    prices = [100, 100, 99, 98, 97, 96, 95, 94, 93, 92, 91]
    for i, p in enumerate(prices):
        ts = datetime(2024, 1, 2, 9, i*5)
        strat.on_kbar(_kbar(ts, p), _make_snap(p, 20, 2, ts))
    assert strat._trades_today > 0


def test_short_blocked_when_allow_short_false():
    """allow_short=False → RSI 高也不做空"""
    from strategy.connors_rsi2 import ConnorsRsi2Strategy
    from datetime import datetime
    strat = ConnorsRsi2Strategy(rsi_high=90, allow_short=False)
    for i, p in enumerate([100, 100, 101, 102, 103, 104, 105, 106, 107, 108]):
        ts = datetime(2024, 1, 2, 9, i*5)
        strat.on_kbar(_kbar(ts, p), _make_snap(p, 20, 2, ts))
    assert strat._trades_today == 0


def test_short_fires_when_allow_short_true():
    from strategy.connors_rsi2 import ConnorsRsi2Strategy
    from strategy.base import SignalDirection
    from datetime import datetime
    strat = ConnorsRsi2Strategy(rsi_high=90, allow_short=True)
    sig = None
    for i, p in enumerate([100, 100, 101, 102, 103, 104, 105, 106, 107, 108]):
        ts = datetime(2024, 1, 2, 9, i*5)
        s = strat.on_kbar(_kbar(ts, p), _make_snap(p, 20, 2, ts))
        if s is not None:
            sig = s
    assert sig is not None and sig.direction == SignalDirection.SELL
    assert sig.stop_loss > 108   # 空單 stop 在進場價之上


def test_entry_window_filter_blocks_before_open():
    """entry_window=09:00-13:00 → 08:40~08:55 即使 RSI 觸發也不進場"""
    from strategy.connors_rsi2 import ConnorsRsi2Strategy
    from datetime import datetime
    strat = ConnorsRsi2Strategy(rsi_low=50, entry_window=("09:00", "13:00"))
    for i, p in enumerate([100, 99, 98, 97]):
        ts = datetime(2024, 1, 2, 8, 40 + i*5)
        strat.on_kbar(_kbar(ts, p), _make_snap(p, 20, 2, ts))
    assert strat._trades_today == 0, "entry_window 守衛失效：盤前不該有交易"


def test_max_trades_per_day_caps():
    """單日 _trades_today >= max_trades 後不再進新場"""
    from strategy.connors_rsi2 import ConnorsRsi2Strategy
    from datetime import datetime
    strat = ConnorsRsi2Strategy(rsi_low=99, max_trades=2)
    for i in range(30):
        # 簡化時間：起點 9:00、5 分一根、保持時間單調遞增
        ts = datetime(2024, 1, 2, 9 + (i*5)//60, (i*5) % 60)
        strat.on_kbar(_kbar(ts, 100 - i*0.1), _make_snap(100, 20, 2, ts))
    assert strat._trades_today == 2


def test_daily_reset_clears_trades_count_but_not_rsi():
    """跨日：_trades_today 歸 0、_session_bar 歸 1、_RsiState 持續累積（_prev_close 帶入新日的價）"""
    from strategy.connors_rsi2 import ConnorsRsi2Strategy
    from datetime import datetime
    strat = ConnorsRsi2Strategy(rsi_low=99, max_trades=10)
    for i in range(5):
        ts = datetime(2024, 1, 2, 9, i*5)
        strat.on_kbar(_kbar(ts, 100 - i), _make_snap(100, 20, 2, ts))
    ts2 = datetime(2024, 1, 3, 9, 0)
    strat.on_kbar(_kbar(ts2, 95), _make_snap(95, 20, 2, ts2))
    assert strat._trades_today <= 1
    assert strat._session_bar == 1
    # RSI 不重置：_prev_close 已被更新為新日的價（95）、_count 沒歸零
    assert strat._rsi._prev_close == 95.0
    assert strat._rsi._count >= 2


# ── Shared helper (also used by Task 5 if needed) ──────────────────────────
def _pos(side, entry, stop, bars):
    """用 PositionManager 建真實 Position（dataclass 無 instrument 欄位）"""
    from core.position import PositionManager
    from core.instrument_config import INSTRUMENT_SPECS
    pm = PositionManager(instruments=["TMF"], configs={"TMF": INSTRUMENT_SPECS["TMF"]},
                         initial_balance=200_000)
    pm.open_position("TMF", side, price=entry, quantity=1,
                     stop_loss=stop, take_profit=0, timestamp=None)
    p = pm.positions["TMF"]
    p.bars_since_entry = bars
    return p


# ── Task 4 exit tests ──────────────────────────────────────────────────────
def test_exit_force_close_at_1325():
    from strategy.connors_rsi2 import ConnorsRsi2Strategy
    from strategy.base import SignalDirection
    from core.position import Side
    from datetime import datetime
    strat = ConnorsRsi2Strategy()
    snap = _make_snap(100, 20, 2, datetime(2024, 1, 2, 13, 25))
    sig = strat.check_exit(_pos(Side.LONG, 95, 90, 5), snap)
    assert sig is not None and sig.direction == SignalDirection.CLOSE and "盤末" in sig.reason


def test_exit_atr_stop_long_sets_cooldown():
    """多單 price 跌破 stop_loss → CLOSE + cooldown_until_bar 設定"""
    from strategy.connors_rsi2 import ConnorsRsi2Strategy
    from core.position import Side
    from datetime import datetime
    strat = ConnorsRsi2Strategy(cooldown=3)
    for i in range(5):
        ts = datetime(2024, 1, 2, 9, i*5)
        strat.on_kbar(_kbar(ts, 100), _make_snap(100, 20, 2, ts))
    bar_at_stop = strat._session_bar   # 5
    snap = _make_snap(89, 20, 2, datetime(2024, 1, 2, 10, 0))
    sig = strat.check_exit(_pos(Side.LONG, 95, 90, 3), snap)
    assert sig is not None and "停損" in sig.reason
    assert strat._cooldown_until_bar == bar_at_stop + 3   # cooldown=3 → block 後續 3 根


def test_exit_atr_stop_short_sets_cooldown():
    """空單 price 漲破 stop_loss → CLOSE + cooldown"""
    from strategy.connors_rsi2 import ConnorsRsi2Strategy
    from core.position import Side
    from datetime import datetime
    strat = ConnorsRsi2Strategy(cooldown=3, allow_short=True)
    for i in range(5):
        ts = datetime(2024, 1, 2, 9, i*5)
        strat.on_kbar(_kbar(ts, 100), _make_snap(100, 20, 2, ts))
    bar_at_stop = strat._session_bar
    snap = _make_snap(103, 20, 2, datetime(2024, 1, 2, 10, 0))
    sig = strat.check_exit(_pos(Side.SHORT, 100, 102, 3), snap)
    assert sig is not None and "停損" in sig.reason
    assert strat._cooldown_until_bar == bar_at_stop + 3


def test_exit_rsi_mid_long():
    """多單 RSI 回 >= 50 → 停利"""
    from strategy.connors_rsi2 import ConnorsRsi2Strategy
    from core.position import Side
    from datetime import datetime
    strat = ConnorsRsi2Strategy()
    for i, p in enumerate([100, 100, 101, 102, 103, 104, 105]):
        ts = datetime(2024, 1, 2, 9, i*5)
        strat.on_kbar(_kbar(ts, p), _make_snap(p, 20, 2, ts))
    assert strat._rsi.rsi > 50
    snap = _make_snap(105, 20, 2, datetime(2024, 1, 2, 10, 0))
    sig = strat.check_exit(_pos(Side.LONG, 100, 95, 3), snap)
    assert sig is not None and "RSI" in sig.reason


def test_exit_time_stop():
    """time stop 觸發；先讓 RSI < 50（避開 RSI 出場分支）"""
    from strategy.connors_rsi2 import ConnorsRsi2Strategy
    from core.position import Side
    from datetime import datetime
    strat = ConnorsRsi2Strategy(max_bars=24)
    for i, p in enumerate([100, 99, 98, 97, 96]):
        ts = datetime(2024, 1, 2, 9, i*5)
        strat.on_kbar(_kbar(ts, p), _make_snap(p, 20, 2, ts))
    assert strat._rsi.rsi < 50
    snap = _make_snap(95, 20, 2, datetime(2024, 1, 2, 11, 0))
    # 注意 check_exit 內 _rsi.update(95) 會再餵一根；RSI 視之為小漲（從 96 -> 95，其實是跌）
    # 多單 stop=90，當前價 95 > 90 不觸停損；bars=25 > max_bars=24
    sig = strat.check_exit(_pos(Side.LONG, 95, 90, 25), snap)
    assert sig is not None and "時間停損" in sig.reason
