"""
OR Fade 策略測試
P0: 成本會計驗證（動態稅模型，commit a95ae44 起）
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import math
from core.position import PositionManager, Side
from core.instrument_config import INSTRUMENT_SPECS


def test_cost_accounting_dynamic_tax():
    """同 connors_rsi2 P0：@22000 → commission=46, net_pnl=54"""
    spec = INSTRUMENT_SPECS["TMF"]
    pm = PositionManager(instruments=["TMF"], configs={"TMF": spec}, initial_balance=200_000)
    pm.open_position("TMF", Side.LONG, price=22000.0, quantity=1,
                     stop_loss=21950, take_profit=22050, timestamp=None)
    trade = pm.close_position("TMF", 22010.0, "test", None)
    assert trade.pnl == 100.0
    assert trade.commission == 46.0
    assert trade.net_pnl == 54.0


def test_cost_accounting_high_price_tax_scales():
    """@45000 → tax=9-10/邊；驗證動態縮放"""
    spec = INSTRUMENT_SPECS["TMF"]
    pm = PositionManager(instruments=["TMF"], configs={"TMF": spec}, initial_balance=200_000)
    pm.open_position("TMF", Side.LONG, price=45000.0, quantity=1,
                     stop_loss=44950, take_profit=45050, timestamp=None)
    trade = pm.close_position("TMF", 45010.0, "test", None)
    expected_entry_tax = math.ceil(45000 * 10 * 0.00002)
    expected_exit_tax  = math.ceil(45010 * 10 * 0.00002)
    expected_comm = 36 + (expected_entry_tax + expected_exit_tax)
    assert trade.commission == float(expected_comm)


# ── P2a: _OrSession state helper ─────────────────────────────────────────────

def test_or_session_not_locked_initially():
    from strategy.or_fade import _OrSession
    s = _OrSession(or_bars=3)
    assert s.locked is False


def test_or_session_locks_after_n_bars():
    from strategy.or_fade import _OrSession
    from datetime import datetime
    s = _OrSession(or_bars=3)
    s.update(datetime(2024,1,2,9,0),  high=10, low=8)
    assert s.locked is False
    s.update(datetime(2024,1,2,9,5),  high=11, low=7)
    assert s.locked is False
    s.update(datetime(2024,1,2,9,10), high=9,  low=6)
    assert s.locked is True
    assert s.or_high == 11.0  # max(10, 11, 9)
    assert s.or_low  == 6.0   # min(8, 7, 6)
    assert s.or_mid  == 8.5   # (11+6)/2


def test_or_session_locked_ignores_further_updates():
    """OR 鎖住後新 bar 不應改 high/low/mid"""
    from strategy.or_fade import _OrSession
    from datetime import datetime
    s = _OrSession(or_bars=2)
    s.update(datetime(2024,1,2,9,0), high=10, low=8)
    s.update(datetime(2024,1,2,9,5), high=12, low=7)
    assert s.locked
    locked_high, locked_low, locked_mid = s.or_high, s.or_low, s.or_mid
    s.update(datetime(2024,1,2,9,10), high=99, low=1)
    assert s.or_high == locked_high
    assert s.or_low  == locked_low
    assert s.or_mid  == locked_mid


def test_or_session_resets_across_days():
    """換日 → OR 狀態歸零、locked=False、重新收集"""
    from strategy.or_fade import _OrSession
    from datetime import datetime
    s = _OrSession(or_bars=2)
    s.update(datetime(2024,1,2,9,0), high=10, low=8)
    s.update(datetime(2024,1,2,9,5), high=12, low=7)
    assert s.locked
    # Day 2 第一根
    s.update(datetime(2024,1,3,9,0), high=20, low=18)
    assert s.locked is False           # reset
    import math
    assert math.isnan(s.or_high)       # 鎖前回 nan
    # 第二根（or_bars=2 → 此根鎖）
    s.update(datetime(2024,1,3,9,5), high=22, low=17)
    assert s.locked
    assert s.or_high == 22.0
    assert s.or_low  == 17.0


def test_or_session_invalid_or_bars():
    from strategy.or_fade import _OrSession
    import pytest
    with pytest.raises(ValueError):
        _OrSession(or_bars=0)


# ── Shared helpers (also used by Tasks 4, 5) ────────────────────────────────
def _make_snap(price, adx, atr, ts, vol_ratio=1.0, volume=100):
    """注意：volume_ratio 是 OR fade 主要過濾條件，預設 1.0 = 中性"""
    from core.market_data import MarketSnapshot
    s = MarketSnapshot()
    s.price = price; s.adx = adx; s.atr = atr; s.timestamp = ts
    s.volume = volume; s.volume_ratio = vol_ratio
    return s


def _kbar(ts, o=None, h=None, l=None, c=None, vol=100):
    """O/H/L/C 可指定（OR fade 對 high/low 敏感）；預設用 c 等於 o，h=c+1,l=c-1"""
    from core.market_data import KBar
    if c is None: c = 100.0
    if o is None: o = c
    if h is None: h = c + 1
    if l is None: l = c - 1
    return KBar(ts, o, h, l, c, vol)


# ── Task 3 entry tests ──────────────────────────────────────────────────────
def test_entry_blocked_until_or_locked():
    """OR 尚未鎖 → 即使觸邊+量縮也不進場"""
    from strategy.or_fade import OrFadeStrategy
    from datetime import datetime
    strat = OrFadeStrategy(or_bars=3, vol_ratio_max=0.9, wait_bars=0, allow_short=True)
    for i in range(2):  # 只 update 2 根（不到 or_bars=3）
        ts = datetime(2024, 1, 2, 8, 45 + i*5)
        strat.on_kbar(_kbar(ts, c=100, h=105, l=95), _make_snap(100, 20, 2, ts, vol_ratio=0.5))
    assert strat._trades_today == 0


def test_entry_long_on_touch_low_with_vol_drop():
    """OR 鎖後觸下緣 + 量縮 + wait=0 → BUY"""
    from strategy.or_fade import OrFadeStrategy
    from strategy.base import SignalDirection
    from datetime import datetime
    strat = OrFadeStrategy(or_bars=3, vol_ratio_max=0.7, wait_bars=0,
                           sl_atr=2.0, allow_short=False)
    for i in range(3):  # OR 收集：3 根，high/low = 105/95
        ts = datetime(2024, 1, 2, 8, 45 + i*5)
        strat.on_kbar(_kbar(ts, c=100, h=105, l=95), _make_snap(100, 20, 2, ts, vol_ratio=1.0))
    ts = datetime(2024, 1, 2, 9, 0)   # _ew_start = 08:45 + 3*5 = 09:00
    sig = strat.on_kbar(_kbar(ts, c=96, h=98, l=95), _make_snap(96, 20, 2, ts, vol_ratio=0.5))
    assert sig is not None and sig.direction == SignalDirection.BUY
    assert sig.stop_loss < 96


def test_entry_blocked_when_volume_too_high():
    from strategy.or_fade import OrFadeStrategy
    from datetime import datetime
    strat = OrFadeStrategy(or_bars=3, vol_ratio_max=0.7, wait_bars=0)
    for i in range(3):
        ts = datetime(2024, 1, 2, 8, 45 + i*5)
        strat.on_kbar(_kbar(ts, c=100, h=105, l=95), _make_snap(100, 20, 2, ts, vol_ratio=1.0))
    ts = datetime(2024, 1, 2, 9, 0)
    sig = strat.on_kbar(_kbar(ts, c=96, h=98, l=95), _make_snap(96, 20, 2, ts, vol_ratio=1.5))
    assert sig is None
    assert strat._trades_today == 0


def test_short_blocked_when_allow_short_false():
    from strategy.or_fade import OrFadeStrategy
    from datetime import datetime
    strat = OrFadeStrategy(or_bars=3, vol_ratio_max=0.9, allow_short=False)
    for i in range(3):
        ts = datetime(2024, 1, 2, 8, 45 + i*5)
        strat.on_kbar(_kbar(ts, c=100, h=105, l=95), _make_snap(100, 20, 2, ts, vol_ratio=1.0))
    ts = datetime(2024, 1, 2, 9, 0)
    sig = strat.on_kbar(_kbar(ts, c=104, h=105, l=103), _make_snap(104, 20, 2, ts, vol_ratio=0.5))
    assert strat._trades_today == 0


def test_wait_bars_1_confirm_success_enters_next_bar():
    """wait=1：觸下緣那根記 pending；下根 low > 觸邊 low → 進場"""
    from strategy.or_fade import OrFadeStrategy
    from strategy.base import SignalDirection
    from datetime import datetime
    strat = OrFadeStrategy(or_bars=3, vol_ratio_max=0.7, wait_bars=1, allow_short=False)
    for i in range(3):
        ts = datetime(2024, 1, 2, 8, 45 + i*5)
        strat.on_kbar(_kbar(ts, c=100, h=105, l=95), _make_snap(100, 20, 2, ts, vol_ratio=1.0))
    ts1 = datetime(2024, 1, 2, 9, 0)
    sig = strat.on_kbar(_kbar(ts1, c=96, h=98, l=95), _make_snap(96, 20, 2, ts1, vol_ratio=0.5))
    assert sig is None
    assert strat._pending_long is not None
    ts2 = datetime(2024, 1, 2, 9, 5)
    sig = strat.on_kbar(_kbar(ts2, c=98, h=99, l=97), _make_snap(98, 20, 2, ts2, vol_ratio=0.8))
    assert sig is not None and sig.direction == SignalDirection.BUY
    assert strat._pending_long is None   # 進場後清空


def test_wait_bars_1_confirm_fail_clears_old_pending():
    """wait=1：下根若再創新低 → 不進場 + 舊 pending 被清。
    注意：若該根本身也觸下緣 + 量縮，會設一個**新** pending（touch_low=新低），
    這是預期行為（新 bar 觸發新 pending，與「舊 pending 被清」是兩件事）。"""
    from strategy.or_fade import OrFadeStrategy
    from datetime import datetime
    strat = OrFadeStrategy(or_bars=3, vol_ratio_max=0.7, wait_bars=1)
    for i in range(3):
        ts = datetime(2024, 1, 2, 8, 45 + i*5)
        strat.on_kbar(_kbar(ts, c=100, h=105, l=95), _make_snap(100, 20, 2, ts, vol_ratio=1.0))
    ts1 = datetime(2024, 1, 2, 9, 0)
    strat.on_kbar(_kbar(ts1, c=96, h=98, l=95), _make_snap(96, 20, 2, ts1, vol_ratio=0.5))
    assert strat._pending_long == {"touch_low": 95}
    # 下根 low=93 ≤ 95 → 確認失敗 + 再次觸下緣 + 量縮 → 設新 pending
    ts2 = datetime(2024, 1, 2, 9, 5)
    sig = strat.on_kbar(_kbar(ts2, c=93, h=95, l=93), _make_snap(93, 20, 2, ts2, vol_ratio=0.5))
    assert sig is None
    assert strat._pending_long == {"touch_low": 93}   # 舊清+新設
    assert strat._trades_today == 0


def test_wait_bars_1_confirm_fail_no_new_touch_clears_pending():
    """wait=1 確認失敗、且該根新觸發條件不滿足 → pending 完全清空（None）。
    用 vol_ratio 過高來卡掉新觸發 path、確保 pending 真的被清為 None。"""
    from strategy.or_fade import OrFadeStrategy
    from datetime import datetime
    strat = OrFadeStrategy(or_bars=3, vol_ratio_max=0.9, wait_bars=1)
    for i, l in enumerate([95, 92, 90]):
        ts = datetime(2024, 1, 2, 8, 45 + i*5)
        strat.on_kbar(_kbar(ts, c=100, h=105, l=l), _make_snap(100, 20, 2, ts, vol_ratio=1.0))
    assert strat._or.or_low == 90
    ts1 = datetime(2024, 1, 2, 9, 0)
    strat.on_kbar(_kbar(ts1, c=91, h=92, l=90), _make_snap(91, 20, 2, ts1, vol_ratio=0.5))
    assert strat._pending_long == {"touch_low": 90}
    # 下根 vol_ratio=1.5 > vol_ratio_max=0.9 → 新觸發 path 不執行 → pending 維持 None
    ts2 = datetime(2024, 1, 2, 9, 5)
    sig = strat.on_kbar(_kbar(ts2, c=89, h=90, l=89), _make_snap(89, 20, 2, ts2, vol_ratio=1.5))
    assert sig is None
    assert strat._pending_long is None
    assert strat._trades_today == 0


def test_daily_reset_clears_or_and_pending():
    """換日：OR 狀態 + pending state + 計數全清"""
    from strategy.or_fade import OrFadeStrategy
    from datetime import datetime
    strat = OrFadeStrategy(or_bars=3, vol_ratio_max=0.7, wait_bars=1)
    for i in range(3):
        ts = datetime(2024, 1, 2, 8, 45 + i*5)
        strat.on_kbar(_kbar(ts, c=100, h=105, l=95), _make_snap(100, 20, 2, ts, vol_ratio=1.0))
    ts1 = datetime(2024, 1, 2, 9, 0)
    strat.on_kbar(_kbar(ts1, c=96, h=98, l=95), _make_snap(96, 20, 2, ts1, vol_ratio=0.5))
    assert strat._pending_long is not None
    ts2 = datetime(2024, 1, 3, 8, 45)
    strat.on_kbar(_kbar(ts2, c=200, h=205, l=195), _make_snap(200, 20, 2, ts2, vol_ratio=1.0))
    assert strat._pending_long is None
    assert strat._or.locked is False
    assert strat._trades_today == 0
    assert strat._session_bar == 1


def test_entry_window_dynamic_start():
    """or_bars=6 → _ew_start = 08:45 + 6*5min = 09:15；09:10 不該進場"""
    from strategy.or_fade import OrFadeStrategy
    from datetime import time
    strat = OrFadeStrategy(or_bars=6)
    assert strat._ew_start == time(9, 15)
    strat2 = OrFadeStrategy(or_bars=3)
    assert strat2._ew_start == time(9, 0)
    strat3 = OrFadeStrategy(or_bars=12)
    assert strat3._ew_start == time(9, 45)
