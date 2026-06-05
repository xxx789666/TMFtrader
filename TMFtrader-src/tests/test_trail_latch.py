"""trail arming latch regression(DayORB + NightORB 共有)。

2026-06-05:trail arming 原用「當下獲利≥trigger」未 latch → 反彈到出場線時當下獲利已縮回
<trigger、條件失效 → trail 幾乎不觸發(等價要 ≥trigger+dist×ATR 才可能)。改 latch:獲利曾達
trigger 即武裝、之後從最佳點反彈 dist 即出。lab realism 驗證 latch 後 PF↑/DD↓/7/7 全正維持。
"""
import sys
from datetime import datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from strategy.day_orb import DayORBStrategy
from strategy.night_orb import NightORBStrategy
from core.market_data import MarketSnapshot
from core.position import Position, Side

TS_DAY = datetime(2026, 6, 5, 11, 0)    # 日盤中、未到 DayORB force_close 13:25
TS_NIGHT = datetime(2026, 6, 5, 20, 0)  # 夜盤中、不在 NightORB 盤末窗 [04:55,05:10]


def _snap(p, ts, atr=100.0):
    return MarketSnapshot(price=p, atr=atr, adx=30.0, timestamp=ts)


def _short_strat(cls):
    s = cls(mode="breakout", sl_atr=1.5, trail_trigger_atr=1.0, trail_dist_atr=1.2)
    s._entry_atr = 100.0
    s._trail_best = 46000.0
    s._trail_armed = False
    return s


def _short_pos():
    return Position(side=Side.SHORT, entry_price=46000.0, quantity=1, bars_since_entry=5)


def test_dayorb_trail_arms_then_fires_on_retrace():
    s = _short_strat(DayORBStrategy)
    pos = _short_pos()
    # 跌到 45850(+150pt = 1.5×ATR > trigger)→ 武裝、尚未反彈足夠 → 不出
    assert s.check_exit(pos, _snap(45850.0, TS_DAY)) is None
    assert s._trail_armed is True
    # 反彈到 45980(當下獲利僅 20pt < trigger,但已武裝;從最低 45850 反彈 130 ≥ 120)→ trail 出場
    sig = s.check_exit(pos, _snap(45980.0, TS_DAY))
    assert sig is not None and "追蹤" in sig.reason


def test_nightorb_trail_arms_then_fires_on_retrace():
    s = _short_strat(NightORBStrategy)
    pos = _short_pos()
    assert s.check_exit(pos, _snap(45850.0, TS_NIGHT)) is None
    assert s._trail_armed is True
    sig = s.check_exit(pos, _snap(45980.0, TS_NIGHT))
    assert sig is not None and "追蹤" in sig.reason


def test_trail_not_armed_before_reaching_trigger():
    """獲利未達 trigger(1×ATR)前不武裝、反彈也不因 trail 出場。"""
    s = _short_strat(DayORBStrategy)
    pos = _short_pos()
    # 只跌 50pt(0.5×ATR < trigger)→ 不武裝
    assert s.check_exit(pos, _snap(45950.0, TS_DAY)) is None
    assert s._trail_armed is False
    # 反彈回 45990 → 未武裝 → 不因 trail 出場(None)
    assert s.check_exit(pos, _snap(45990.0, TS_DAY)) is None
