"""NightORB 夜盤盤末強平 regression(TF=60 計時 bug)。

2026-06-02 事故:night_v3 paper(TF=60)04:00 ORB-LONG 進場,夜盤盤末強平(舊條件
04:55<=bt<=05:10)未觸發 → 裸抱過 05:00 收盤 + 05:00-08:45 無交易缺口 → 08:45 日盤
開盤跳空掃硬停損,-570pt(遠大於原訂 213pt 停損)。

根因:60m bar 時戳只落整點(…03:00、04:00,然後直接跳到日盤 08:00),永遠不會落在
[04:55,05:10] → 強平窗打不到。breakout_v7(TF=5)因 bar 落在 04:55/05:00/05:05 才正常。

修法:盤末窗下緣改整點(force_close_after=04:00)讓 60m bar 也打得到;並加「臨收盤
(>=04:00)不開新倉」cutoff。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timedelta, time

from strategy.night_orb import NightORBStrategy
from strategy.base import SignalDirection
from core.market_data import KBar, MarketSnapshot
from core.position import Position, Side


def _bar(dt, hi, lo):
    mid = (hi + lo) / 2
    return KBar(datetime=dt, open=mid, high=hi, low=lo, close=mid, volume=100, interval=60)


def _snap(price, atr=30.0, adx=40.0):
    return MarketSnapshot(price=price, atr=atr, adx=adx)


def _build_or(s, or_bars=8):
    """餵 or_bars 根夜盤 60m bar(15:00 起)建好開盤區間。OR=[45950,46050],寬 100。"""
    dt = datetime(2026, 6, 1, 15, 0)
    for _ in range(or_bars):
        s.on_kbar(_bar(dt, 46050, 45950), _snap(46000))
        dt += timedelta(hours=1)
    assert s._or_ready


def test_60m_bars_skip_old_0455_0510_window():
    """證明舊碼為何失效:夜盤 60m bar 時戳沒有一根落在 [04:55,05:10]。"""
    night_bar_times = [time(h, 0) for h in list(range(15, 24)) + list(range(0, 5))]  # 15:00..04:00
    assert all(not (time(4, 55) <= bt <= time(5, 10)) for bt in night_bar_times)
    # 新窗下緣 04:00 可被 04:00 那根夜盤 bar 命中
    assert any(time(4, 0) <= bt <= time(5, 10) for bt in night_bar_times)


def test_entry_on_03_bar_then_force_closed_at_04_bar():
    """重現事故時序:03:00 bar 突破進場 → 04:00 bar(最後一根夜盤)必須盤末強平,不可抱到日盤。"""
    s = NightORBStrategy(mode="breakout", or_bars=8, buf_atr=0.35,
                         min_or_atr=2.4, max_or_atr=6.5, min_adx=25.0)
    _build_or(s)
    # 03:00 bar:price 46150 > or_hi(46050)+0.35*30=46060.5 → ORB-LONG 進場
    sig = s.on_kbar(_bar(datetime(2026, 6, 2, 3, 0), 46200, 46100), _snap(46150))
    assert sig is not None and sig.direction == SignalDirection.BUY
    pos = Position(side=Side.LONG, entry_price=46150.0, quantity=2, bars_since_entry=1)
    # 04:00 bar(最後一根夜盤)完成 → _bar_time=04:00
    s.on_kbar(_bar(datetime(2026, 6, 2, 4, 0), 46180, 46120), _snap(46140))
    assert s._bar_time.time() == time(4, 0)
    # check_exit 在 04:00 bar → 盤末強平(而非裸抱到 08:45)
    exit_sig = s.check_exit(pos, _snap(46140))
    assert exit_sig is not None
    assert exit_sig.direction == SignalDirection.CLOSE
    assert "盤末" in exit_sig.reason


def test_no_new_entry_at_or_after_cutoff():
    """臨收盤(>=04:00)即使出現突破也不開新倉(無隔夜跑道)。"""
    s = NightORBStrategy(mode="breakout", or_bars=8, buf_atr=0.35,
                         min_or_atr=2.4, max_or_atr=6.5, min_adx=25.0)
    _build_or(s)
    sig = s.on_kbar(_bar(datetime(2026, 6, 2, 4, 0), 46300, 46200), _snap(46250))
    assert sig is None
    assert s._traded is False


def test_no_force_close_mid_night():
    """盤末窗之前(如 02:00)不可提前強平。"""
    s = NightORBStrategy(mode="breakout", or_bars=8)
    s._bar_time = datetime(2026, 6, 2, 2, 0)
    s._entry_atr = 30.0
    s._trail_best = 46150.0
    pos = Position(side=Side.LONG, entry_price=46150.0, quantity=2, bars_since_entry=1)
    exit_sig = s.check_exit(pos, _snap(46160))  # 小幅獲利、未觸發追蹤/金額止損
    assert exit_sig is None
