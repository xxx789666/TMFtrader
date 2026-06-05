"""NightORB 夜盤交易時段 gate regression。

2026-06-05 事故:night_v3(夜盤策略、應只 15:00–05:00 交易)在中午 12:00 誤進 ORB-SHORT。
根因:_sess_of 把 00:00–15:00 歸前一夜(供跨午夜群組),OR ready 後若無「進場時段 gate」,
會在日盤(05:00–15:00)拿昨夜舊 OR 評估突破而誤進場。修法:on_kbar 進場前加 15:00–05:00 gate。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timedelta

from strategy.night_orb import NightORBStrategy
from strategy.base import SignalDirection
from core.market_data import KBar, MarketSnapshot


def _bar(dt, hi, lo):
    mid = (hi + lo) / 2
    return KBar(datetime=dt, open=mid, high=hi, low=lo, close=mid, volume=100, interval=60)


def _snap(p, atr=30.0, adx=40.0):
    return MarketSnapshot(price=p, atr=atr, adx=adx)


def _mk(**kw):
    return NightORBStrategy(mode="breakout", or_bars=8, min_or_atr=0.5,
                            max_or_atr=6.5, min_adx=25.0, buf_atr=0.1, **kw)


def _build_or(s):
    """夜盤 15:00 起 8 根 60m → OR ready;OR=[45950,46050]。"""
    dt = datetime(2026, 6, 4, 15, 0)
    for _ in range(s.or_bars):
        s.on_kbar(_bar(dt, 46050, 45950), _snap(46000))
        dt += timedelta(hours=1)
    assert s._or_ready


def test_no_entry_during_day_session():
    """OR ready 後,日盤 12:00 出現突破也不進場(2026-06-05 事故防護)。"""
    s = _mk()
    _build_or(s)
    sig = s.on_kbar(_bar(datetime(2026, 6, 5, 12, 0), 45900, 45800), _snap(45820))  # 跌破 OR_lo
    assert sig is None
    assert s._traded is False


def test_no_entry_at_0600_after_night_close():
    """夜盤 05:00 收盤後(06:00)即使突破也不進場。"""
    s = _mk()
    _build_or(s)
    sig = s.on_kbar(_bar(datetime(2026, 6, 5, 6, 0), 46300, 46200), _snap(46250))
    assert sig is None and s._traded is False


def test_entry_allowed_in_night_window():
    """夜盤時段(23:00)突破則正常進場。"""
    s = _mk()
    _build_or(s)
    sig = s.on_kbar(_bar(datetime(2026, 6, 4, 23, 0), 46200, 46100), _snap(46150))  # 突破 OR_hi
    assert sig is not None and sig.direction == SignalDirection.BUY


def test_entry_allowed_early_morning_in_window():
    """凌晨(02:00、仍夜盤窗 15:00–05:00)突破可進場。"""
    s = _mk()
    _build_or(s)
    sig = s.on_kbar(_bar(datetime(2026, 6, 5, 2, 0), 46200, 46100), _snap(46150))
    assert sig is not None and sig.direction == SignalDirection.BUY
