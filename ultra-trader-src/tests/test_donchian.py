"""
Donchian Breakout 策略測試
P0: 成本會計驗證（動態稅模型，commit a95ae44 起）
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import math
from core.position import PositionManager, Side
from core.instrument_config import INSTRUMENT_SPECS


def test_cost_accounting_dynamic_tax():
    """@22000 → commission=46, net_pnl=54"""
    spec = INSTRUMENT_SPECS["TMF"]
    pm = PositionManager(instruments=["TMF"], configs={"TMF": spec}, initial_balance=200_000)
    pm.open_position("TMF", Side.LONG, price=22000.0, quantity=1,
                     stop_loss=21950, take_profit=22050, timestamp=None)
    trade = pm.close_position("TMF", 22010.0, "test", None)
    assert trade.pnl == 100.0
    assert trade.commission == 46.0
    assert trade.net_pnl == 54.0


def test_cost_accounting_high_price_tax_scales():
    """@45000 → 動態縮放"""
    spec = INSTRUMENT_SPECS["TMF"]
    pm = PositionManager(instruments=["TMF"], configs={"TMF": spec}, initial_balance=200_000)
    pm.open_position("TMF", Side.LONG, price=45000.0, quantity=1,
                     stop_loss=44950, take_profit=45050, timestamp=None)
    trade = pm.close_position("TMF", 45010.0, "test", None)
    expected_entry_tax = math.ceil(45000 * 10 * 0.00002)
    expected_exit_tax  = math.ceil(45010 * 10 * 0.00002)
    expected_comm = 36 + (expected_entry_tax + expected_exit_tax)
    assert trade.commission == float(expected_comm)


# ── P2a: _DonchianState helper tests ─────────────────────────────────────────

def test_donchian_not_warmup_initially():
    from strategy.donchian import _DonchianState
    import math
    s = _DonchianState(entry_n=3, exit_k=2)
    assert s.warmup_ready is False
    assert math.isnan(s.entry_n_high)
    assert math.isnan(s.exit_k_high)


def test_donchian_warmup_after_n_plus_1_bars():
    """warmup_ready 需 len > entry_n（要 entry_n+1 根才能算 entry_n 個 prior bars）"""
    from strategy.donchian import _DonchianState
    from datetime import datetime
    s = _DonchianState(entry_n=3, exit_k=2)
    for i, (h, l) in enumerate([(10, 8), (11, 7), (12, 6)]):
        s.update(datetime(2024, 1, 2, 9, i*5), h, l)
    assert s.warmup_ready is False
    s.update(datetime(2024, 1, 2, 9, 15), 9, 5)
    assert s.warmup_ready is True
    # deque 已 append 4 根、maxlen=max(3,2)+1=4，deque = [(10,8),(11,7),(12,6),(9,5)]
    # entry_n_high = max of bars[-4:-1] 的 high = max(10,11,12) = 12（第 4 根 high=9 不算）
    # entry_n_low  = min of bars[-4:-1] 的 low  = min(8,7,6)  = 6（第 4 根 low=5 不算）
    assert s.entry_n_high == 12
    assert s.entry_n_low  == 6


def test_donchian_entry_EXCLUDES_current_bar():
    """KEY lookahead 防線：剛 append 的 bar 的 high 不應出現在 entry_n_high"""
    from strategy.donchian import _DonchianState
    from datetime import datetime
    s = _DonchianState(entry_n=3, exit_k=2)
    for i, (h, l) in enumerate([(10, 8), (11, 7), (12, 6), (13, 5)]):
        s.update(datetime(2024, 1, 2, 9, i*5), h, l)
    assert s.warmup_ready
    # deque = [(10,8),(11,7),(12,6),(13,5)]
    # entry_n_high = max of bars[-4:-1] = max(10,11,12) = 12（NOT 13!）
    assert s.entry_n_high == 12, "entry_n_high MUST exclude current bar 13 (lookahead防線)"
    # Append 極端新高
    s.update(datetime(2024, 1, 2, 9, 20), 99, 1)
    # deque = [(11,7),(12,6),(13,5),(99,1)]（maxlen=4 推掉最舊）
    # entry_n_high = max of bars[-4:-1] = max(11,12,13) = 13（NOT 99!）
    assert s.entry_n_high == 13, "after new append, entry_n_high still EXCLUDES current bar"


def test_donchian_exit_INCLUDES_current_bar():
    """exit_k_high/low 含當前 bar（trailing 即時反應）"""
    from strategy.donchian import _DonchianState
    from datetime import datetime
    s = _DonchianState(entry_n=3, exit_k=2)
    for i, (h, l) in enumerate([(10, 8), (11, 7), (12, 6)]):
        s.update(datetime(2024, 1, 2, 9, i*5), h, l)
    # exit_k=2，含當前 → exit_k_high = max of last 2 highs = max(11, 12) = 12
    assert s.exit_k_high == 12
    assert s.exit_k_low  == 6
    # Append 新高
    s.update(datetime(2024, 1, 2, 9, 15), 99, 5)
    # exit_k_high = max of last 2 = max(12, 99) = 99（INCLUDES current!）
    assert s.exit_k_high == 99, "exit_k_high MUST include current bar 99"
    assert s.exit_k_low  == 5


def test_donchian_resets_across_days():
    from strategy.donchian import _DonchianState
    from datetime import datetime
    import math
    s = _DonchianState(entry_n=2, exit_k=2)
    for i, (h, l) in enumerate([(10, 8), (11, 7), (12, 6)]):
        s.update(datetime(2024, 1, 2, 9, i*5), h, l)
    assert s.warmup_ready
    # 換日第一根
    s.update(datetime(2024, 1, 3, 9, 0), 20, 18)
    assert s.warmup_ready is False
    assert math.isnan(s.entry_n_high)
    s.update(datetime(2024, 1, 3, 9, 5), 22, 17)
    s.update(datetime(2024, 1, 3, 9, 10), 21, 19)
    assert s.warmup_ready
    assert s.entry_n_high == max(20, 22)


def test_donchian_invalid_params():
    from strategy.donchian import _DonchianState
    import pytest
    with pytest.raises(ValueError):
        _DonchianState(entry_n=0, exit_k=2)
    with pytest.raises(ValueError):
        _DonchianState(entry_n=2, exit_k=0)
