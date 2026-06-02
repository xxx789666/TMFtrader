"""DayORB 日盤盤末強平 regression。

2026-06-02:day_orb(TF=30)11:30 進場,日盤盤末強平(force_close 13:25)未在 13:45 收盤前
觸發 → 裸抱過收盤 → 14:50 夜盤撮合才平(這次跳空向上幸運 +16468,但與 night_v3 同病:
命運交給撮合跳空方向)。

根因同 night_orb:進場後 engine 不再呼叫 on_kbar → strategy._bar_time 凍結在進場 bar
(11:00),`_bar_time >= 13:25` 永不成立。

正解:改比對 snapshot.timestamp(引擎每 tick 餵的當下時間)。日盤同一日曆日,>= 即可。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime

from strategy.day_orb import DayORBStrategy
from strategy.base import SignalDirection
from core.market_data import MarketSnapshot
from core.position import Position, Side


def _snap(price, ts, atr=30.0, adx=40.0):
    return MarketSnapshot(price=price, atr=atr, adx=adx, timestamp=ts)


def _held_long(strat, entry=45427.0):
    """已進場、_bar_time 凍結在進場 bar(11:00)的多單。"""
    strat._bar_time = datetime(2026, 6, 2, 11, 0)
    strat._entry_atr = 30.0
    strat._trail_best = entry
    return Position(side=Side.LONG, entry_price=entry, quantity=2, bars_since_entry=1)


def test_force_close_uses_live_snapshot_time_not_frozen_bar_time():
    """核心:_bar_time 凍結在 11:00,但 snapshot.timestamp=13:26(>=13:25)→ 盤末強平。"""
    s = DayORBStrategy(mode="fade")
    pos = _held_long(s)
    sig = s.check_exit(pos, _snap(46103.0, datetime(2026, 6, 2, 13, 26)))
    assert sig is not None
    assert sig.direction == SignalDirection.CLOSE
    assert "盤末" in sig.reason


def test_no_force_close_before_1325():
    """12:00(13:25 之前)不強平。"""
    s = DayORBStrategy(mode="fade")
    pos = _held_long(s)
    sig = s.check_exit(pos, _snap(45500.0, datetime(2026, 6, 2, 12, 0)))
    assert sig is None
