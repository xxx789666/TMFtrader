"""v7 同方向當天限一次 guard 的單元測試(2026-06-16)。

驗證 BreakoutDualSlopeStrategy 的同向再進 guard:
- 記錄綁在「實際持倉」(check_exit 透過 _record_held_direction),非訊號發出。
- on_kbar 透過 _same_dir_already_done 擋同向;反向不受影響;跨日重置;
- 被鎖/風控擋掉(未走 check_exit)者不誤記、當天可重試。
"""
from datetime import datetime
from types import SimpleNamespace

from strategy.breakout_dualslope import BreakoutDualSlopeStrategy
from strategy.base import SignalDirection
from core.position import Side

D1_AM = datetime(2026, 6, 16, 10, 5)
D1_LATER = datetime(2026, 6, 16, 10, 55)
D2_AM = datetime(2026, 6, 17, 10, 5)


def _pos(side):
    return SimpleNamespace(side=side)


def test_first_short_allowed_then_second_blocked_same_day():
    s = BreakoutDualSlopeStrategy()
    # 第一筆空:flat 時 on_kbar 評估 → 尚未持倉 → 不擋
    assert s._same_dir_already_done(SignalDirection.SELL, D1_AM) is False
    # 成交後持倉,引擎呼叫 check_exit → 記錄方向
    s._record_held_direction(_pos(Side.SHORT), D1_AM)
    # 平倉後當天再出同向空訊號 → 擋
    assert s._same_dir_already_done(SignalDirection.SELL, D1_LATER) is True


def test_opposite_direction_still_allowed():
    s = BreakoutDualSlopeStrategy()
    s._record_held_direction(_pos(Side.SHORT), D1_AM)     # 今天做過空
    assert s._same_dir_already_done(SignalDirection.SELL, D1_LATER) is True   # 空被擋
    assert s._same_dir_already_done(SignalDirection.BUY, D1_LATER) is False   # 多仍可


def test_new_day_resets():
    s = BreakoutDualSlopeStrategy()
    s._record_held_direction(_pos(Side.SHORT), D1_AM)
    assert s._same_dir_already_done(SignalDirection.SELL, D1_AM) is True
    # 隔天同向 → 重置後放行
    assert s._same_dir_already_done(SignalDirection.SELL, D2_AM) is False


def test_rejected_entry_not_recorded_can_retry():
    s = BreakoutDualSlopeStrategy()
    # 模擬:on_kbar 發出空訊號但被單池鎖/風控擋掉 → 從未走 check_exit(無持倉)
    assert s._same_dir_already_done(SignalDirection.SELL, D1_AM) is False
    # 下一根仍可重試(未誤記)
    assert s._same_dir_already_done(SignalDirection.SELL, D1_LATER) is False


def test_reset_clears_state():
    s = BreakoutDualSlopeStrategy()
    s._record_held_direction(_pos(Side.LONG), D1_AM)
    assert s._same_dir_already_done(SignalDirection.BUY, D1_AM) is True
    s.reset()
    assert s._same_dir_already_done(SignalDirection.BUY, D1_AM) is False
