"""DayORB 日盤時段 gate regression。

背景:on_kbar 原本用 kbar.datetime.date() 當 session、且無盤中時段過濾。在 24h
餵 bar 下,00:00 換日會 reset → 開盤區間(OR)從半夜夜盤 bar 起算(bug)。平日靠每日
08:40 cron 重啟把 _cur_sess 清掉才碰巧正確,但跨午夜不重啟即錨歪。

修法(a):on_kbar 開頭加日盤時段 gate(08:30–13:45),盤外 bar 一律忽略。此後 OR
永遠從日盤開盤起算,且日盤不跨午夜 → date() session key 即正確。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timedelta

from strategy.day_orb import DayORBStrategy
from core.market_data import KBar, MarketSnapshot


def _bar(dt, hi, lo):
    mid = (hi + lo) / 2
    return KBar(datetime=dt, open=mid, high=hi, low=lo, close=mid, volume=100, interval=30)


def _snap(price):
    return MarketSnapshot(price=price, atr=20.0, adx=30.0)


def _feed(strat, start_dt, end_dt, hi, lo, step_min=30):
    dt = start_dt
    while dt <= end_dt:
        strat.on_kbar(_bar(dt, hi, lo), _snap((hi + lo) / 2))
        dt += timedelta(minutes=step_min)


def test_night_bars_ignored_no_session_started():
    """純夜盤 bar(跨午夜連續餵)應全被 gate 擋下:session 不設、OR 不開始。"""
    s = DayORBStrategy(or_bars=6)
    # 前日 22:00 → 隔日 05:00,全程夜盤
    _feed(s, datetime(2026, 5, 31, 22, 0), datetime(2026, 6, 1, 5, 0), hi=45050, lo=44950)
    assert s._cur_sess is None
    assert s._or_n == 0
    assert s._or_ready is False


def test_or_anchors_at_day_open_not_midnight():
    """跨午夜連續餵(不重啟):OR 必須錨在日盤開盤、用日盤 bar,而非半夜夜盤。"""
    s = DayORBStrategy(or_bars=6)
    # 夜盤價區 45000,日盤價區 46000,藉價區證明 OR 只取日盤 bar
    _feed(s, datetime(2026, 5, 31, 22, 0), datetime(2026, 6, 1, 5, 0), hi=45050, lo=44950)
    # 午夜換日後仍是夜盤 → 不可 reset/不可開始建 OR
    assert s._cur_sess is None and s._or_n == 0
    # 進入日盤:08:30 起每 30m 一根
    _feed(s, datetime(2026, 6, 1, 8, 30), datetime(2026, 6, 1, 11, 0), hi=46050, lo=45950)
    # session 應設為日盤交易日;OR 已用滿 6 根 day bar 並 ready
    assert s._cur_sess == datetime(2026, 6, 1).date()
    assert s._or_n == 6
    assert s._or_ready is True
    # OR 區間必須落在日盤價區(46000),完全不含夜盤(45000)
    assert s._or_hi == 46050
    assert s._or_lo == 45950


def test_or_ready_after_exactly_or_bars_day_bars():
    """OR 在收滿 or_bars 根日盤 bar 後才 ready,之前不 ready。"""
    s = DayORBStrategy(or_bars=6)
    # 餵 5 根日盤 bar → 還沒 ready
    _feed(s, datetime(2026, 6, 1, 8, 30), datetime(2026, 6, 1, 10, 30), hi=46050, lo=45950)
    assert s._or_n == 5 and s._or_ready is False
    # 第 6 根 → ready
    s.on_kbar(_bar(datetime(2026, 6, 1, 11, 0), 46050, 45950), _snap(46000))
    assert s._or_n == 6 and s._or_ready is True


def test_post_close_bars_ignored():
    """收盤後(>=13:45)的 bar 不再參與 OR 建構。"""
    s = DayORBStrategy(or_bars=6)
    _feed(s, datetime(2026, 6, 1, 8, 30), datetime(2026, 6, 1, 11, 0), hi=46050, lo=45950)
    n_before = s._or_n
    # 收盤後夜盤 bar
    s.on_kbar(_bar(datetime(2026, 6, 1, 22, 0), 47000, 40000), _snap(43500))
    # OR 區間不應被盤後 bar 汙染
    assert s._or_hi == 46050 and s._or_lo == 45950
    assert s._or_n == n_before
