"""
Donchian Breakout 策略測試
P0: 成本會計驗證（動態稅模型，commit a95ae44 起）
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import math
from core.position import PositionManager, Side
from core.instrument_config import INSTRUMENT_SPECS


def test_cost_accounting_dynamic_tax():
    """@22000 → commission=46, net_pnl=54"""
    spec = INSTRUMENT_SPECS["TMF"]
    pm = PositionManager(instruments=["TMF"], configs={"TMF": spec}, initial_balance=200_000)
    pm.open_position("TMF", Side.LONG, price=22000.0, quantity=1,
                     stop_loss=21950, take_profit=22050, timestamp=None)
    trade = pm.close_position("TMF", 22010.0, "test", None)
    assert trade.pnl == 100.0
    assert trade.commission == 46.0
    assert trade.net_pnl == 54.0


def test_cost_accounting_high_price_tax_scales():
    """@45000 → 動態縮放"""
    spec = INSTRUMENT_SPECS["TMF"]
    pm = PositionManager(instruments=["TMF"], configs={"TMF": spec}, initial_balance=200_000)
    pm.open_position("TMF", Side.LONG, price=45000.0, quantity=1,
                     stop_loss=44950, take_profit=45050, timestamp=None)
    trade = pm.close_position("TMF", 45010.0, "test", None)
    expected_entry_tax = math.ceil(45000 * 10 * 0.00002)
    expected_exit_tax  = math.ceil(45010 * 10 * 0.00002)
    expected_comm = 36 + (expected_entry_tax + expected_exit_tax)
    assert trade.commission == float(expected_comm)


# ── P2a: _DonchianState helper tests ─────────────────────────────────────────

def test_donchian_not_warmup_initially():
    from strategy.donchian import _DonchianState
    import math
    s = _DonchianState(entry_n=3, exit_k=2)
    assert s.warmup_ready is False
    assert math.isnan(s.entry_n_high)
    assert math.isnan(s.exit_k_high)


def test_donchian_warmup_after_n_plus_1_bars():
    """warmup_ready 需 len > entry_n（要 entry_n+1 根才能算 entry_n 個 prior bars）"""
    from strategy.donchian import _DonchianState
    from datetime import datetime
    s = _DonchianState(entry_n=3, exit_k=2)
    for i, (h, l) in enumerate([(10, 8), (11, 7), (12, 6)]):
        s.update(datetime(2024, 1, 2, 9, i*5), h, l)
    assert s.warmup_ready is False
    s.update(datetime(2024, 1, 2, 9, 15), 9, 5)
    assert s.warmup_ready is True
    # deque 已 append 4 根、maxlen=max(3,2)+1=4，deque = [(10,8),(11,7),(12,6),(9,5)]
    # entry_n_high = max of bars[-4:-1] 的 high = max(10,11,12) = 12（第 4 根 high=9 不算）
    # entry_n_low  = min of bars[-4:-1] 的 low  = min(8,7,6)  = 6（第 4 根 low=5 不算）
    assert s.entry_n_high == 12
    assert s.entry_n_low  == 6


def test_donchian_entry_EXCLUDES_current_bar():
    """KEY lookahead 防線：剛 append 的 bar 的 high 不應出現在 entry_n_high"""
    from strategy.donchian import _DonchianState
    from datetime import datetime
    s = _DonchianState(entry_n=3, exit_k=2)
    for i, (h, l) in enumerate([(10, 8), (11, 7), (12, 6), (13, 5)]):
        s.update(datetime(2024, 1, 2, 9, i*5), h, l)
    assert s.warmup_ready
    # deque = [(10,8),(11,7),(12,6),(13,5)]
    # entry_n_high = max of bars[-4:-1] = max(10,11,12) = 12（NOT 13!）
    assert s.entry_n_high == 12, "entry_n_high MUST exclude current bar 13 (lookahead防線)"
    # Append 極端新高
    s.update(datetime(2024, 1, 2, 9, 20), 99, 1)
    # deque = [(11,7),(12,6),(13,5),(99,1)]（maxlen=4 推掉最舊）
    # entry_n_high = max of bars[-4:-1] = max(11,12,13) = 13（NOT 99!）
    assert s.entry_n_high == 13, "after new append, entry_n_high still EXCLUDES current bar"


def test_donchian_exit_INCLUDES_current_bar():
    """exit_k_high/low 含當前 bar（trailing 即時反應）"""
    from strategy.donchian import _DonchianState
    from datetime import datetime
    s = _DonchianState(entry_n=3, exit_k=2)
    for i, (h, l) in enumerate([(10, 8), (11, 7), (12, 6)]):
        s.update(datetime(2024, 1, 2, 9, i*5), h, l)
    # exit_k=2，含當前 → exit_k_high = max of last 2 highs = max(11, 12) = 12
    assert s.exit_k_high == 12
    assert s.exit_k_low  == 6
    # Append 新高
    s.update(datetime(2024, 1, 2, 9, 15), 99, 5)
    # exit_k_high = max of last 2 = max(12, 99) = 99（INCLUDES current!）
    assert s.exit_k_high == 99, "exit_k_high MUST include current bar 99"
    assert s.exit_k_low  == 5


def test_donchian_resets_across_days():
    from strategy.donchian import _DonchianState
    from datetime import datetime
    import math
    s = _DonchianState(entry_n=2, exit_k=2)
    for i, (h, l) in enumerate([(10, 8), (11, 7), (12, 6)]):
        s.update(datetime(2024, 1, 2, 9, i*5), h, l)
    assert s.warmup_ready
    # 換日第一根
    s.update(datetime(2024, 1, 3, 9, 0), 20, 18)
    assert s.warmup_ready is False
    assert math.isnan(s.entry_n_high)
    s.update(datetime(2024, 1, 3, 9, 5), 22, 17)
    s.update(datetime(2024, 1, 3, 9, 10), 21, 19)
    assert s.warmup_ready
    assert s.entry_n_high == max(20, 22)


def test_donchian_invalid_params():
    from strategy.donchian import _DonchianState
    import pytest
    with pytest.raises(ValueError):
        _DonchianState(entry_n=0, exit_k=2)
    with pytest.raises(ValueError):
        _DonchianState(entry_n=2, exit_k=0)


# ── Shared helpers (used by Tasks 4, 5) ────────────────────────────────────
def _make_snap(price, adx, atr, ts):
    from core.market_data import MarketSnapshot
    s = MarketSnapshot()
    s.price = price; s.adx = adx; s.atr = atr; s.timestamp = ts; s.volume = 100
    return s


def _kbar(ts, o=None, h=None, l=None, c=None, vol=100):
    """O/H/L/C 可指定；Donchian 對 high/low 敏感"""
    from core.market_data import KBar
    if c is None: c = 100.0
    if o is None: o = c
    if h is None: h = c + 1
    if l is None: l = c - 1
    return KBar(ts, o, h, l, c, vol)


# ── Task 3 entry tests ─────────────────────────────────────────────────────
def test_entry_window_dynamic_start():
    """entry_n=N → _ew_start = 08:45 + N*5min"""
    from strategy.donchian import DonchianStrategy
    from datetime import time
    assert DonchianStrategy(entry_n=10)._ew_start == time(9, 35)
    assert DonchianStrategy(entry_n=20)._ew_start == time(10, 25)
    assert DonchianStrategy(entry_n=30)._ew_start == time(11, 15)


def test_entry_blocked_until_warmup():
    """warmup 未達 (len <= entry_n) → 不進場"""
    from strategy.donchian import DonchianStrategy
    from datetime import datetime
    strat = DonchianStrategy(entry_n=3, allow_short=False)
    for i, p in enumerate([100, 101, 102]):
        ts = datetime(2024, 1, 2, 9, 0 + i*5)
        strat.on_kbar(_kbar(ts, c=p, h=p, l=p-1), _make_snap(p, 30, 2, ts))
    assert strat._trades_today == 0


def test_entry_long_on_breakout_above_n_bar_high():
    """收 N+1 根之後 close > prior-N-bar high → BUY"""
    from strategy.donchian import DonchianStrategy
    from strategy.base import SignalDirection
    from datetime import datetime
    strat = DonchianStrategy(entry_n=3, exit_k=2, sl_atr=2.0, allow_short=False)
    # entry_n=3 → _ew_start = 09:00
    prices = [100, 102, 105, 103]
    for i, p in enumerate(prices):
        ts = datetime(2024, 1, 2, 9, 0 + i*5)
        strat.on_kbar(_kbar(ts, c=p, h=p, l=p-1), _make_snap(p, 30, 2, ts))
    # deque highs = [100,102,105,103]
    # entry_n_high = max of bars[-4:-1] = max(100,102,105) = 105
    # 下一根 close=107 > 105 → BUY
    ts = datetime(2024, 1, 2, 9, 20)
    sig = strat.on_kbar(_kbar(ts, c=107, h=107, l=106), _make_snap(107, 30, 2, ts))
    assert sig is not None and sig.direction == SignalDirection.BUY
    assert sig.stop_loss < 107


def test_no_entry_when_close_inside_range():
    """close 在 N-bar range 內 → 不進場"""
    from strategy.donchian import DonchianStrategy
    from datetime import datetime
    strat = DonchianStrategy(entry_n=3, allow_short=True)
    for i, p in enumerate([100, 102, 105, 103]):
        ts = datetime(2024, 1, 2, 9, 0 + i*5)
        strat.on_kbar(_kbar(ts, c=p, h=p, l=p-1), _make_snap(p, 30, 2, ts))
    # close=104 介於 lows ~99 與 highs ~105 之間
    ts = datetime(2024, 1, 2, 9, 20)
    sig = strat.on_kbar(_kbar(ts, c=104, h=104, l=103), _make_snap(104, 30, 2, ts))
    assert sig is None


def test_short_blocked_when_allow_short_false():
    """allow_short=False → close < N-bar low 也不進場"""
    from strategy.donchian import DonchianStrategy
    from datetime import datetime
    strat = DonchianStrategy(entry_n=3, allow_short=False)
    for i, p in enumerate([100, 102, 105, 103]):
        ts = datetime(2024, 1, 2, 9, 0 + i*5)
        strat.on_kbar(_kbar(ts, c=p, h=p, l=p-1), _make_snap(p, 30, 2, ts))
    ts = datetime(2024, 1, 2, 9, 20)
    sig = strat.on_kbar(_kbar(ts, c=95, h=95, l=94), _make_snap(95, 30, 2, ts))
    assert sig is None


def test_short_fires_when_allow_short_true():
    from strategy.donchian import DonchianStrategy
    from strategy.base import SignalDirection
    from datetime import datetime
    strat = DonchianStrategy(entry_n=3, sl_atr=2.0, allow_short=True)
    for i, p in enumerate([100, 102, 105, 103]):
        ts = datetime(2024, 1, 2, 9, 0 + i*5)
        strat.on_kbar(_kbar(ts, c=p, h=p, l=p-1), _make_snap(p, 30, 2, ts))
    # entry_n_low = min of bars[-4:-1] lows = min(99, 101, 104) = 99
    ts = datetime(2024, 1, 2, 9, 20)
    sig = strat.on_kbar(_kbar(ts, c=98, h=98, l=97), _make_snap(98, 30, 2, ts))
    assert sig is not None and sig.direction == SignalDirection.SELL
    assert sig.stop_loss > 98


def test_entry_window_blocks_pre_open():
    """entry_n=3 → _ew_start=09:00；08:35~08:55 不該進場"""
    from strategy.donchian import DonchianStrategy
    from datetime import datetime
    strat = DonchianStrategy(entry_n=3, allow_short=False)
    for i, p in enumerate([100, 102, 105, 103, 107]):
        ts = datetime(2024, 1, 2, 8, 35 + i*5)
        strat.on_kbar(_kbar(ts, c=p, h=p, l=p-1), _make_snap(p, 30, 2, ts))
    assert strat._trades_today == 0


def test_max_trades_per_day_caps():
    """單日 _trades_today >= max_trades 後不再進新場"""
    from strategy.donchian import DonchianStrategy
    from datetime import datetime, timedelta
    strat = DonchianStrategy(entry_n=2, max_trades=2, allow_short=False)
    # ascending close 每根都會破前 N 高；用 timedelta 避免分鐘溢位
    base = datetime(2024, 1, 2, 9, 0)
    for i in range(20):
        ts = base + timedelta(minutes=i * 5)
        p = 100 + i * 2
        strat.on_kbar(_kbar(ts, c=p, h=p, l=p-1), _make_snap(p, 30, 2, ts))
    assert strat._trades_today == 2


def test_daily_reset_clears_state():
    """跨日：_trades_today=0、_session_bar=1（第一根後）；_donchian 自己 reset"""
    from strategy.donchian import DonchianStrategy
    from datetime import datetime
    strat = DonchianStrategy(entry_n=2, allow_short=False)
    for i, p in enumerate([100, 102, 105]):
        ts = datetime(2024, 1, 2, 9, i*5)
        strat.on_kbar(_kbar(ts, c=p, h=p, l=p-1), _make_snap(p, 30, 2, ts))
    ts2 = datetime(2024, 1, 3, 8, 45)
    strat.on_kbar(_kbar(ts2, c=200, h=200, l=199), _make_snap(200, 30, 2, ts2))
    assert strat._trades_today == 0
    assert strat._session_bar == 1
    assert strat._donchian.warmup_ready is False


# ── Task 4 exit tests (P2c) ────────────────────────────────────────────────

def _pos(side, entry, stop, bars):
    from core.position import PositionManager
    from core.instrument_config import INSTRUMENT_SPECS
    pm = PositionManager(instruments=["TMF"], configs={"TMF": INSTRUMENT_SPECS["TMF"]},
                         initial_balance=200_000)
    pm.open_position("TMF", side, price=entry, quantity=1,
                     stop_loss=stop, take_profit=0, timestamp=None)
    p = pm.positions["TMF"]
    p.bars_since_entry = bars
    return p


def _strat_with_warmed_donchian(entry_n=3, exit_k=2, allow_short=False, cooldown=3, max_bars=48):
    """灌 entry_n+1 根、讓 warmup 過、回 strat。
    註：warmup 過程中、若 close 突破 prior-N-bar high 會自然觸發 entry signal。
    `_trades_today` 可能 >= 1。Task 4 exit tests 不檢查 _trades_today、不影響。
    `_session_bar == entry_n+1` 穩定（check_exit 不增量）。"""
    from strategy.donchian import DonchianStrategy
    from datetime import datetime
    strat = DonchianStrategy(entry_n=entry_n, exit_k=exit_k, allow_short=allow_short,
                             cooldown=cooldown, max_bars=max_bars)
    for i in range(entry_n + 1):
        ts = datetime(2024, 1, 2, 9, i*5)
        p = 100 + i * 2
        strat.on_kbar(_kbar(ts, c=p, h=p+1, l=p-1), _make_snap(p, 30, 2, ts))
    assert strat._donchian.warmup_ready
    return strat


def test_exit_force_close_no_cooldown():
    from strategy.base import SignalDirection
    from core.position import Side
    from datetime import datetime
    strat = _strat_with_warmed_donchian()
    cd_before = strat._cooldown_until_bar
    snap = _make_snap(105, 30, 2, datetime(2024, 1, 2, 13, 25))
    sig = strat.check_exit(_pos(Side.LONG, 104, 100, 5), snap)
    assert sig is not None and sig.direction == SignalDirection.CLOSE and "盤末" in sig.reason
    assert strat._cooldown_until_bar == cd_before   # NOT set on force_close


def test_exit_atr_stop_long_sets_cooldown():
    from core.position import Side
    from datetime import datetime
    strat = _strat_with_warmed_donchian(cooldown=3)
    bar_at_stop = strat._session_bar
    snap = _make_snap(99, 30, 2, datetime(2024, 1, 2, 10, 0))
    sig = strat.check_exit(_pos(Side.LONG, 104, 100, 3), snap)
    assert sig is not None and "停損" in sig.reason
    assert strat._cooldown_until_bar == bar_at_stop + 3


def test_exit_atr_stop_short_sets_cooldown():
    from core.position import Side
    from datetime import datetime
    strat = _strat_with_warmed_donchian(cooldown=3, allow_short=True)
    bar_at_stop = strat._session_bar
    snap = _make_snap(112, 30, 2, datetime(2024, 1, 2, 10, 0))
    sig = strat.check_exit(_pos(Side.SHORT, 105, 110, 3), snap)
    assert sig is not None and "停損" in sig.reason
    assert strat._cooldown_until_bar == bar_at_stop + 3


def test_exit_donchian_k_low_trailing_long():
    """多單 price < exit_k_low → trailing 觸發"""
    from core.position import Side
    from datetime import datetime
    strat = _strat_with_warmed_donchian(exit_k=2)
    # entry_n=3 → 灌 4 根 (i=0..3)，p=100,102,104,106，l=p-1 → lows=[99,101,103,105]
    # exit_k=2 → exit_k_low = min(103, 105) = 103
    # price=102 < 103 → Donchian trailing 觸發
    snap = _make_snap(102, 30, 2, datetime(2024, 1, 2, 10, 0))
    sig = strat.check_exit(_pos(Side.LONG, 100, 90, 3), snap)
    assert sig is not None and "Donchian" in sig.reason


def test_exit_donchian_k_high_trailing_short():
    from core.position import Side
    from datetime import datetime
    strat = _strat_with_warmed_donchian(exit_k=2, allow_short=True)
    # entry_n=3 → 灌 4 根，h=p+1 → highs=[101,103,105,107]
    # exit_k=2 → exit_k_high = max(105, 107) = 107
    # price=108 > 107 → Donchian trailing 觸發
    snap = _make_snap(108, 30, 2, datetime(2024, 1, 2, 10, 0))
    sig = strat.check_exit(_pos(Side.SHORT, 100, 110, 3), snap)
    assert sig is not None and "Donchian" in sig.reason


def test_exit_time_stop():
    """time stop 觸發、避 trailing 與 stop"""
    from core.position import Side
    from datetime import datetime
    strat = _strat_with_warmed_donchian(exit_k=2, max_bars=18)
    snap = _make_snap(110, 30, 2, datetime(2024, 1, 2, 11, 0))
    # 多單 entry 100, stop 80（很遠不觸）；price 110 高於 exit_k_low ~104 不 trailing
    # bars=19 > max_bars=18 → time stop
    sig = strat.check_exit(_pos(Side.LONG, 100, 80, 19), snap)
    assert sig is not None and "時間停損" in sig.reason


def test_check_exit_does_not_mutate_state():
    """invariant：check_exit 不 update _donchian、不 increment _session_bar"""
    from core.position import Side
    from datetime import datetime
    strat = _strat_with_warmed_donchian()
    high_before = strat._donchian.entry_n_high
    low_before  = strat._donchian.entry_n_low
    session_bar_before = strat._session_bar
    snap = _make_snap(103, 30, 2, datetime(2024, 1, 2, 10, 0))
    strat.check_exit(_pos(Side.LONG, 100, 90, 3), snap)
    assert strat._donchian.entry_n_high == high_before
    assert strat._donchian.entry_n_low  == low_before
    assert strat._session_bar == session_bar_before
