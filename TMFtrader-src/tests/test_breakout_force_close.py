"""BreakoutTrend(v7 基底)盤末強平 regression。

2026-06-05:① 日盤強平時間 user 指定 13:25→13:30 ② 修 frozen bug——原本用
self._current_bar_time(進場後凍結在進場 bar、強平永不觸發、只靠引擎 backstop),
改用 snapshot.timestamp(引擎每 tick 餵的當下時間),與 day_orb/night_orb 一致。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime

from strategy.breakout import BreakoutTrendStrategy
from strategy.base import SignalDirection
from core.market_data import MarketSnapshot
from core.position import Position, Side


def _held(strat, entry=46000.0):
    strat.reset()
    strat._current_bar_time = datetime(2026, 6, 5, 11, 0)  # 凍結在進場 bar(早)
    strat._entry_atr = 30.0
    strat._trail_best = entry
    return Position(side=Side.LONG, entry_price=entry, quantity=1, bars_since_entry=5)


def _snap(ts, price=46010.0):
    return MarketSnapshot(price=price, atr=30.0, adx=30.0, timestamp=ts)


def test_day_force_close_at_1330_despite_frozen_bar_time():
    s = BreakoutTrendStrategy()
    pos = _held(s)
    sig = s.check_exit(pos, _snap(datetime(2026, 6, 5, 13, 31)))
    assert sig is not None and sig.direction == SignalDirection.CLOSE
    assert "盤末" in sig.reason


def test_no_day_force_close_before_1330():
    s = BreakoutTrendStrategy()
    pos = _held(s)
    assert s.check_exit(pos, _snap(datetime(2026, 6, 5, 13, 29))) is None


def test_night_force_close_window():
    s = BreakoutTrendStrategy()
    pos = _held(s)
    sig = s.check_exit(pos, _snap(datetime(2026, 6, 5, 4, 56)))
    assert sig is not None and "盤末" in sig.reason


def test_no_force_close_evening():
    """夜盤傍晚 20:00 不可誤平(夜盤窗有界 04:55-05:10)。"""
    s = BreakoutTrendStrategy()
    pos = _held(s)
    assert s.check_exit(pos, _snap(datetime(2026, 6, 5, 20, 0))) is None
