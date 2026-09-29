"""v7 同方向當天限一次 guard 的單元測試(2026-06-16)。

驗證 BreakoutDualSlopeStrategy 的同向再進 guard:
- 記錄綁在「實際持倉」(check_exit 透過 _record_held_direction),非訊號發出。
- on_kbar 透過 _same_dir_already_done 擋同向;反向不受影響;跨日重置;
- 被鎖/風控擋掉(未走 check_exit)者不誤記、當天可重試。
"""
from datetime import datetime
from types import SimpleNamespace

import pytest

from strategy.breakout_dualslope import BreakoutDualSlopeStrategy
from strategy.base import SignalDirection
from core.position import Side

D1_AM = datetime(2026, 6, 16, 10, 5)
D1_LATER = datetime(2026, 6, 16, 10, 55)
D2_AM = datetime(2026, 6, 17, 10, 5)


@pytest.fixture(autouse=True)
def _no_owner_env(monkeypatch):
    # 確保 in-memory 測試不因環境 STRATEGY_OWNER 誤啟用落地
    monkeypatch.delenv("STRATEGY_OWNER", raising=False)


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


def test_no_state_path_stays_in_memory():
    # 無 STRATEGY_OWNER + 無 state_path → 純記憶體、不落地
    s = BreakoutDualSlopeStrategy()
    assert s._state_path is None
    s._record_held_direction(_pos(Side.SHORT), D1_AM)   # 不應拋例外/寫檔


def test_persistence_survives_restart(tmp_path):
    sp = str(tmp_path / "guard.json")
    s1 = BreakoutDualSlopeStrategy(state_path=sp)
    s1._record_held_direction(_pos(Side.SHORT), D1_AM)        # 做過空 → 寫檔
    # 模擬盤中重啟:全新實例、同 state_path → 載回
    s2 = BreakoutDualSlopeStrategy(state_path=sp)
    assert s2._same_dir_already_done(SignalDirection.SELL, D1_LATER) is True   # 空仍被擋
    assert s2._same_dir_already_done(SignalDirection.BUY, D1_LATER) is False   # 多仍可


def test_persistence_new_day_resets_file(tmp_path):
    sp = str(tmp_path / "guard.json")
    s1 = BreakoutDualSlopeStrategy(state_path=sp)
    s1._record_held_direction(_pos(Side.SHORT), D1_AM)
    s1._roll_day(D2_AM)                                       # 跨日 → 清空並覆寫檔
    s2 = BreakoutDualSlopeStrategy(state_path=sp)             # 重啟載回的是「隔天空集合」
    assert s2._same_dir_already_done(SignalDirection.SELL, D2_AM) is False


def test_corrupt_state_file_falls_back_empty(tmp_path):
    sp = tmp_path / "guard.json"
    sp.write_text("{ not valid json", encoding="utf-8")
    s = BreakoutDualSlopeStrategy(state_path=str(sp))         # 壞檔 → 不崩、改空集合
    assert s._same_dir_already_done(SignalDirection.SELL, D1_AM) is False
