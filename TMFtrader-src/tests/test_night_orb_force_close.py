"""NightORB 夜盤盤末強平 regression。

2026-06-02 事故 + 深掘:night_v3(TF=60)04:00 進場,夜盤盤末強平未觸發 → 裸抱過 05:00
收盤 → 隔日撮合跳空掃損。最初(commit a6efa77)誤改 K 棒時戳窗,但根因更深:

  engine `_process_kbar` 只在「無倉」時呼叫 on_kbar(L1118),而 strategy 的 _bar_time
  只在 on_kbar 裡更新 → 進場後 _bar_time **凍結在進場那根 bar**,任何 `_bar_time >= 盤末`
  的時間型強平永不觸發。

正解:check_exit 盤末強平改比對 `snapshot.timestamp`——引擎在 _process_tick(L995)每個
tick(含死區的 WallClock 合成 tick)都把當下時間塞進 snapshot.timestamp,且每 tick 都呼叫
check_exit。夜盤跨午夜,故用有界窗 [04:55,05:10](傍晚 20:00 數值上也 > 04:55)。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, time

from strategy.night_orb import NightORBStrategy
from strategy.base import SignalDirection
from core.market_data import MarketSnapshot
from core.position import Position, Side


def _snap(price, ts, atr=30.0, adx=40.0):
    return MarketSnapshot(price=price, atr=atr, adx=adx, timestamp=ts)


def _held_long(strat, entry=46420.0):
    """模擬一個已進場、_bar_time 凍結在進場 bar(03:00)的多單。"""
    strat._bar_time = datetime(2026, 6, 2, 3, 0)   # 進場那根 bar,之後 on_kbar 不再被呼叫 → 凍結
    strat._entry_atr = 30.0
    strat._trail_best = entry
    return Position(side=Side.LONG, entry_price=entry, quantity=2, bars_since_entry=1)


def test_force_close_uses_live_snapshot_time_not_frozen_bar_time():
    """核心 regression:_bar_time 凍結在 03:00,但 snapshot.timestamp=04:56(窗內)→ 必須強平。
    這正是 a6efa77 治不了的點(它比對凍結的 _bar_time)。"""
    s = NightORBStrategy(mode="breakout")
    pos = _held_long(s)
    sig = s.check_exit(pos, _snap(46553.0, datetime(2026, 6, 2, 4, 56)))
    assert sig is not None
    assert sig.direction == SignalDirection.CLOSE
    assert "盤末" in sig.reason


def test_force_close_at_0500_close_via_synth():
    """05:00 收盤(WallClock 合成 tick 帶 timestamp=05:00)→ 窗內 → 強平。"""
    s = NightORBStrategy(mode="breakout")
    pos = _held_long(s)
    sig = s.check_exit(pos, _snap(46553.0, datetime(2026, 6, 2, 5, 0)))
    assert sig is not None and sig.direction == SignalDirection.CLOSE


def test_no_force_close_before_window():
    """04:30(窗前)不強平。"""
    s = NightORBStrategy(mode="breakout")
    pos = _held_long(s)
    sig = s.check_exit(pos, _snap(46430.0, datetime(2026, 6, 2, 4, 30)))
    assert sig is None


def test_no_force_close_evening_despite_numeric_gt():
    """夜盤傍晚 20:00:數值上 20:00 > 04:55,但不在有界窗 [04:55,05:10] → 不可誤平(跨午夜)。"""
    s = NightORBStrategy(mode="breakout")
    pos = _held_long(s)
    sig = s.check_exit(pos, _snap(46430.0, datetime(2026, 6, 1, 20, 0)))
    assert sig is None
