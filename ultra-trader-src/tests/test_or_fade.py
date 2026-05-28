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
