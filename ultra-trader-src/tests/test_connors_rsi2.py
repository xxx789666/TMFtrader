"""
Connors RSI(2) 策略測試
P0: 成本會計驗證（動態稅模型，commit a95ae44 起）
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import math
from core.position import PositionManager, Side
from core.instrument_config import INSTRUMENT_SPECS


def test_cost_accounting_dynamic_tax():
    """
    TMF spec：point_value=10, commission=18/邊, tax_rate_pct=0.00002
    進場 22000，平倉 22010，1 口：
      毛利       = (22010-22000) × 10 × 1 = 100
      fee_comm   = 18 × 2 邊 × 1 口 = 36
      entry_tax  = ceil(22000 × 10 × 0.00002) = ceil(4.4) = 5
      exit_tax   = ceil(22010 × 10 × 0.00002) = ceil(4.402) = 5
      fee_tax    = (5 + 5) × 1 = 10
      commission(全部扣項) = 36 + 10 = 46
      net_pnl   = 100 - 46 = 54
    """
    spec = INSTRUMENT_SPECS["TMF"]
    pm = PositionManager(instruments=["TMF"], configs={"TMF": spec}, initial_balance=200_000)
    pm.open_position("TMF", Side.LONG, price=22000.0, quantity=1,
                     stop_loss=21950, take_profit=22050, timestamp=None)
    trade = pm.close_position("TMF", 22010.0, "test", None)

    assert trade.pnl == 100.0,        f"pnl: expected 100.0, got {trade.pnl}"
    assert trade.commission == 46.0,  f"commission(全扣項): expected 46.0, got {trade.commission}"
    assert trade.net_pnl == 54.0,     f"net_pnl: expected 54.0, got {trade.net_pnl}"


def test_cost_accounting_high_price_tax_scales():
    """指數越高、稅越高。@45000 → tax=9/邊、commission=18*2+9*2=54、net_pnl 受影響"""
    spec = INSTRUMENT_SPECS["TMF"]
    pm = PositionManager(instruments=["TMF"], configs={"TMF": spec}, initial_balance=200_000)
    pm.open_position("TMF", Side.LONG, price=45000.0, quantity=1,
                     stop_loss=44950, take_profit=45050, timestamp=None)
    trade = pm.close_position("TMF", 45010.0, "test", None)
    # entry_tax = ceil(45000*10*0.00002) = ceil(9.0) = 9
    # exit_tax  = ceil(45010*10*0.00002) = ceil(9.002) = 10
    # fee_tax = 19, fee_comm = 36, commission = 55
    expected_entry_tax = math.ceil(45000 * 10 * 0.00002)
    expected_exit_tax  = math.ceil(45010 * 10 * 0.00002)
    expected_comm = 36 + (expected_entry_tax + expected_exit_tax)
    assert trade.commission == float(expected_comm), \
        f"@45k commission: expected {expected_comm}, got {trade.commission}"


def test_rsi_warmup_returns_50():
    """暖身期（update 數 < period）回中性 50.0"""
    from strategy.connors_rsi2 import _RsiState
    s = _RsiState(period=2)
    assert s.rsi == 50.0      # 還沒 update
    s.update(100.0)
    assert s.rsi == 50.0      # 才 1 個 update，不足 period


def test_rsi_full_gain_approaches_100():
    """連續上漲 → RSI 趨向 100"""
    from strategy.connors_rsi2 import _RsiState
    s = _RsiState(period=2)
    for p in [100, 101, 102, 103, 104, 105]:
        s.update(float(p))
    assert s.rsi > 95.0


def test_rsi_full_loss_approaches_0():
    """連續下跌 → RSI 趨向 0"""
    from strategy.connors_rsi2 import _RsiState
    s = _RsiState(period=2)
    for p in [100, 99, 98, 97, 96, 95]:
        s.update(float(p))
    assert s.rsi < 5.0


def test_rsi_state_does_NOT_reset_on_call():
    """_RsiState 本身無 reset/換日邏輯 — 連續 Wilder smoothing。
    換日 reset 是策略層 (_trades_today / _cooldown / _session_bar) 才做。"""
    from strategy.connors_rsi2 import _RsiState
    s = _RsiState(period=2)
    for p in [100, 101, 102, 103, 104]:
        s.update(float(p))
    high_rsi = s.rsi
    assert high_rsi > 80.0
    # 沒有 reset() method
    assert not hasattr(s, "reset"), "_RsiState 不應有 reset() — 跨日連續是設計"
