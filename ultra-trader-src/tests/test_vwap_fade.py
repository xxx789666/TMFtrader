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


def test_session_resample():
    """
    P1: 驗證日盤時段過濾 + 5m resample 純函式
    """
    import pandas as pd
    from scripts.prepare_vwap_data import resample_day_session
    idx = pd.date_range("2024-01-02 08:40", "2024-01-02 13:50", freq="1min")
    df = pd.DataFrame({"datetime": idx, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 1})
    out = resample_day_session(df, freq="5min")
    assert out["datetime"].dt.time.min().strftime("%H:%M") == "08:45"
    assert out["datetime"].dt.time.max().strftime("%H:%M") <= "13:45"
    assert (out["high"] == 2.0).all() and (out["low"] == 0.5).all()
    assert out["volume"].iloc[0] == 5


def test_session_vwap_cumulative_and_reset():
    from strategy.vwap_fade import _SessionVwap
    from datetime import datetime
    sv = _SessionVwap(sigma_window=20, use_volume=True)
    sv.update(datetime(2024,1,2,9,0), high=10, low=8, close=9, volume=2)   # typical=9
    sv.update(datetime(2024,1,2,9,5), high=12, low=10, close=11, volume=2) # typical=11
    assert abs(sv.vwap - 10.0) < 1e-9     # (9*2 + 11*2)/4 = 10
    sv.update(datetime(2024,1,3,9,0), high=20, low=20, close=20, volume=1) # day reset
    assert abs(sv.vwap - 20.0) < 1e-9


def test_session_vwap_close_proxy_update():
    from strategy.vwap_fade import _SessionVwap
    from datetime import datetime
    sv = _SessionVwap(sigma_window=20, use_volume=True)
    sv.update(datetime(2024,1,2,9,0), high=10, low=8, close=9, volume=2)
    sv.update_close_proxy(datetime(2024,1,2,9,5), close=11, volume=2)
    assert abs(sv.vwap - 10.0) < 1e-9     # (9*2 + 11*2)/4 = 10
