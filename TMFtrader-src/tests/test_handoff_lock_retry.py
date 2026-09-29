"""單池交班 regression — 被鎖擋下後重置 _traded、鎖放開能再進場。

2026-06-05:engine.py L1154 先呼叫 on_kbar(產訊號即設 _traded=True)、L1213 才 is_blocked。
若被「同模式他策略」持鎖擋下、_traded 殘留 True → on_kbar 整 session return None → 鎖放開後
永不進場(aft_orb 抱倉擋 night_v3 → night_v3 整夜不交班接手的 bug)。
修法:_execute_entry 被擋時重置 _strat._traded=False。本測試驗證該重置確實讓策略能再進。
"""
import sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from strategy.night_orb import NightORBStrategy
from strategy.base import SignalDirection
from core.market_data import KBar, MarketSnapshot


def _bar(h, m=0, hi=46010.0, lo=45990.0):
    return KBar(datetime=datetime(2026, 6, 5, h, m), open=46000.0, high=hi, low=lo,
                close=46000.0, volume=100, interval=60)


def _snap(price, atr=20.0, adx=30.0):
    return MarketSnapshot(price=price, atr=atr, adx=adx, timestamp=None)


def _build_or(s):
    """or_bars=3:15/16/17 點建 OR=[45990,46010];回傳 18 點上破訊號。"""
    s.on_kbar(_bar(15), _snap(46000))   # 新夜 reset、or_n=1
    s.on_kbar(_bar(16), _snap(46000))   # or_n=2
    s.on_kbar(_bar(17), _snap(46000))   # or_n=3 → OR ready
    return s.on_kbar(_bar(18), _snap(46100))   # 上破(46100 > 46010+buf)


def test_traded_set_on_signal():
    s = NightORBStrategy(mode="breakout", or_bars=3, min_adx=0.0)
    sig = _build_or(s)
    assert sig is not None and sig.direction == SignalDirection.BUY
    assert s._traded is True   # 訊號一出就設 _traded(這正是被擋會殘留的旗標)


def test_blocked_without_reset_never_retries():
    """重現 bug:_traded 殘留 True → 後續突破一律 None(整夜不再進)。"""
    s = NightORBStrategy(mode="breakout", or_bars=3, min_adx=0.0)
    _build_or(s)
    assert s.on_kbar(_bar(19), _snap(46100)) is None   # 仍在突破、卻因 _traded=True 被擋死


def test_reset_traded_allows_handoff_entry():
    """修法:被鎖擋下後 engine 重置 _traded → 鎖放開、突破仍成立即可進場(交班)。"""
    s = NightORBStrategy(mode="breakout", or_bars=3, min_adx=0.0)
    _build_or(s)
    s._traded = False                                   # 模擬 _execute_entry 被擋時的重置
    sig = s.on_kbar(_bar(19), _snap(46100))
    assert sig is not None and sig.direction == SignalDirection.BUY   # 鎖放開後成功接手
