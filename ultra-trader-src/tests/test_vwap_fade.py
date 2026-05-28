"""
VWAP Fade 策略測試
P0: 成本會計驗證 — 來回成本與點數換算
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.position import PositionManager, Side
from core.instrument_config import INSTRUMENT_SPECS


def test_cost_accounting():
    """
    驗證來回成本計算正確性。

    TMF 規格：point_value=10, commission=18, tax=7
    進場 22000，平倉 22010（+10 點），1 口

    毛利 = 10 點 × 10 元 × 1 口 = 100
    來回成本 = (18 手續費 + 7 稅) × 2 邊 × 1 口 = 50
    淨利 = 100 - 50 = 50
    """
    spec = INSTRUMENT_SPECS["TMF"]
    pm = PositionManager(
        instruments=["TMF"],
        configs={"TMF": spec},
        initial_balance=200_000,
    )
    # 進場 22000，平倉 22010（+10 點），1 口
    pm.open_position(
        "TMF", Side.LONG, price=22000.0, quantity=1,
        stop_loss=21950, take_profit=22050, timestamp=None,
    )
    trade = pm.close_position("TMF", 22010.0, "test", None)

    # 毛利 = 10 點 × 10 元 × 1 口 = 100
    assert trade.pnl == 100.0, f"Expected pnl=100.0, got {trade.pnl}"
    # 來回成本 = (18 手續費 + 7 稅) × 2 邊 × 1 口 = 50
    assert trade.commission == 50.0, f"Expected commission=50.0, got {trade.commission}"
    # 淨利 = 100 - 50 = 50（成本確實有扣）
    assert trade.net_pnl == 50.0, f"Expected net_pnl=50.0, got {trade.net_pnl}"
