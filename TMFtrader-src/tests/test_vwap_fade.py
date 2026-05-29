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
    驗證來回成本計算正確性（動態稅模型，commit a95ae44 起）。

    TMF 規格：point_value=10, commission=18/邊, tax_rate_pct=0.00002
    進場 22000，平倉 22010（+10 點），1 口

    毛利       = 10 點 × 10 元 × 1 口 = 100
    fee_comm   = 18 × 2 邊 × 1 口 = 36
    entry_tax  = ceil(22000 × 10 × 0.00002) = ceil(4.4) = 5
    exit_tax   = ceil(22010 × 10 × 0.00002) = ceil(4.402) = 5
    fee_tax    = (5 + 5) × 1 = 10
    commission = 36 + 10 = 46
    net_pnl    = 100 - 46 = 54
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
    # 來回成本 = 36 fee_comm + 10 fee_tax = 46（動態稅模型）
    assert trade.commission == 46.0, f"Expected commission=46.0, got {trade.commission}"
    # 淨利 = 100 - 46 = 54
    assert trade.net_pnl == 54.0, f"Expected net_pnl=54.0, got {trade.net_pnl}"


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


# ── P2b entry logic helpers ──────────────────────────────────────────────────

def _make_snap(price, adx, atr, ts):
    from core.market_data import MarketSnapshot
    s = MarketSnapshot()
    s.price = price
    s.adx = adx
    s.atr = atr
    s.timestamp = ts
    s.volume = 100
    return s


def _kbar(ts, p, vol=100):
    from core.market_data import KBar
    # field order: datetime, open, high, low, close, volume
    return KBar(ts, p, p + 1, p - 1, p, vol)


def test_entry_blocked_by_adx():
    from strategy.vwap_fade import VwapFadeStrategy
    from datetime import datetime
    strat = VwapFadeStrategy(k=2.0, adx_max=30, min_warmup=1)
    for m in range(0, 30, 5):
        ts = datetime(2024, 1, 2, 9, m)
        strat.on_kbar(_kbar(ts, 100), _make_snap(100, 20, 5, ts))
    ts = datetime(2024, 1, 2, 10, 0)
    sig = strat.on_kbar(_kbar(ts, 80), _make_snap(80, 45, 5, ts))   # large deviation but adx over limit
    assert sig is None


def test_entry_long_on_deviation():
    from strategy.vwap_fade import VwapFadeStrategy
    from strategy.base import SignalDirection
    from datetime import datetime
    strat = VwapFadeStrategy(k=2.0, adx_max=40, min_warmup=3, entry_window=("09:00", "13:00"))
    prices = [100, 101, 99, 100, 102, 98, 101, 99, 100, 101]
    for i, p in enumerate(prices):
        ts = datetime(2024, 1, 2, 9, i * 5)
        strat.on_kbar(_kbar(ts, p), _make_snap(p, 20, 2, ts))
    ts = datetime(2024, 1, 2, 10, 0)
    sig = strat.on_kbar(_kbar(ts, 90), _make_snap(90, 20, 2, ts))   # far below VWAP
    assert sig is not None and sig.direction == SignalDirection.BUY
    assert sig.stop_loss < 90    # 多單停損必須在進場價下方（這個 assertion 抓的就是上面 fix 的 bug）
    assert sig.take_profit > 90  # 多單 TP（=vwap）必須在進場價上方


def test_entry_stop_below_close_on_deep_deviation():
    """Regression: deep BUY deviation must not produce stop >= entry (instant stop-out bug)."""
    from strategy.vwap_fade import VwapFadeStrategy
    from strategy.base import SignalDirection
    from datetime import datetime
    strat = VwapFadeStrategy(k=2.0, k2=3.0, sl_atr=2.0, adx_max=99, min_warmup=3)
    # Build a session with low sigma, then a deep undershoot that breaches even the k2 band.
    for i, p in enumerate([100,100,100,100,100,100,100,100,100,100]):
        ts = datetime(2024,1,2,9,i*5)
        strat.on_kbar(_kbar(ts,p), _make_snap(p,20,2,ts))
    ts = datetime(2024,1,2,10,0)
    sig = strat.on_kbar(_kbar(ts, 80), _make_snap(80, 20, 2, ts))   # 深 fade
    assert sig is not None and sig.direction == SignalDirection.BUY
    assert sig.stop_loss < 80, f"Stop {sig.stop_loss} must be below entry 80 (long), else instant stop-out"


# ── P2c exit logic helpers + tests ──────────────────────────────────────────

def _pos(side, entry, stop, tp, bars):
    from core.position import PositionManager
    from core.instrument_config import INSTRUMENT_SPECS
    pm = PositionManager(instruments=["TMF"], configs={"TMF": INSTRUMENT_SPECS["TMF"]},
                         initial_balance=200_000)
    pm.open_position("TMF", side, price=entry, quantity=1,
                     stop_loss=stop, take_profit=tp, timestamp=None)
    p = pm.positions["TMF"]
    p.bars_since_entry = bars
    return p


def test_exit_force_close_at_1325():
    from strategy.vwap_fade import VwapFadeStrategy
    from strategy.base import SignalDirection
    from core.position import Side
    from datetime import datetime
    strat = VwapFadeStrategy()
    strat._vwap.update(datetime(2024, 1, 2, 9, 0), 100, 100, 100, 100)
    snap = _make_snap(100, 20, 5, datetime(2024, 1, 2, 13, 25))
    sig = strat.check_exit(_pos(Side.LONG, 95, 90, 101, 5), snap)
    assert sig is not None and sig.direction == SignalDirection.CLOSE and "盤末" in sig.reason


def test_exit_stop_loss_long():
    from strategy.vwap_fade import VwapFadeStrategy
    from strategy.base import SignalDirection
    from core.position import Side
    from datetime import datetime
    strat = VwapFadeStrategy()
    strat._vwap.update(datetime(2024, 1, 2, 9, 0), 100, 100, 100, 100)
    # long entry 95, stop 90; price 89 <= stop -> triggers
    snap = _make_snap(89, 20, 5, datetime(2024, 1, 2, 10, 0))
    sig = strat.check_exit(_pos(Side.LONG, 95, 90, 101, 5), snap)
    assert sig is not None and sig.direction == SignalDirection.CLOSE and "停損" in sig.reason


def test_exit_vwap_profit_target_long():
    from strategy.vwap_fade import VwapFadeStrategy
    from strategy.base import SignalDirection
    from core.position import Side
    from datetime import datetime
    strat = VwapFadeStrategy()
    # Feed bars so VWAP stabilises near 100
    for m in range(0, 25, 5):
        ts = datetime(2024, 1, 2, 9, m)
        strat.on_kbar(_kbar(ts, 100), _make_snap(100, 20, 2, ts))
    # long entry 95; price 100 >= vwap -> profit target
    snap = _make_snap(100, 20, 2, datetime(2024, 1, 2, 10, 0))
    sig = strat.check_exit(_pos(Side.LONG, 95, 90, 100, 3), snap)
    assert sig is not None and "VWAP" in sig.reason


def test_exit_time_stop():
    from strategy.vwap_fade import VwapFadeStrategy
    from strategy.base import SignalDirection
    from core.position import Side
    from datetime import datetime
    strat = VwapFadeStrategy(max_bars=24)
    strat._vwap.update(datetime(2024, 1, 2, 9, 0), 100, 100, 100, 100)
    # 25 bars held > max_bars 24; price at 95 doesn't breach stop(90) or reach vwap(~100)
    snap = _make_snap(95, 20, 5, datetime(2024, 1, 2, 11, 0))
    sig = strat.check_exit(_pos(Side.LONG, 95, 90, 100, 25), snap)
    assert sig is not None and "時間停損" in sig.reason


def test_cooldown_blocks_reentry_after_stop():
    """Cooldown wiring: after a stop-loss exit _cooldown_until_bar is set correctly.

    check_exit calls update_close_proxy internally, which increments session_bar by 1
    before setting _cooldown_until_bar = session_bar + cooldown.  So the expected value
    is (bar_before_stop + 1) + cooldown.
    """
    from strategy.vwap_fade import VwapFadeStrategy
    from core.position import Side
    from datetime import datetime
    strat = VwapFadeStrategy(cooldown=3, min_warmup=1, adx_max=99)
    # Build VWAP state
    for i in range(5):
        ts = datetime(2024, 1, 2, 9, i * 5)
        strat.on_kbar(_kbar(ts, 100), _make_snap(100, 20, 2, ts))
    bar_before_stop = strat._vwap.session_bar
    # SHORT entry 100, stop 102 (above entry, correct for short);
    # price 103 >= 102 -> stop triggered
    snap = _make_snap(103, 20, 2, datetime(2024, 1, 2, 9, 30))
    strat.check_exit(_pos(Side.SHORT, 100, 102, 90, 1), snap)
    # update_close_proxy inside check_exit increments session_bar by 1 before cooldown is set
    assert strat._cooldown_until_bar == bar_before_stop + 1 + 3


# ── P2d: FastBacktestEngine 整合冒煙測試 ────────────────────────────────────


def test_grid_smoke_runs_tiny():
    """Task 7 (P3) smoke: 2-combo grid via _run_one on real MXF data (no multiprocessing)."""
    import pandas as pd
    from pathlib import Path
    p = Path("data/vwap_fade/MXF_day_5m.parquet")
    if not p.exists():
        import pytest
        pytest.skip("data missing — run prepare_vwap_data.py first")

    from scripts.optimize_vwap_fade import _run_one

    df = pd.read_parquet(p).head(2000).reset_index(drop=True)

    for params in [
        {"k": 2.0, "k2": 3.0, "sigma_window": 20, "adx_max": 30, "max_bars": 24},
        {"k": 1.5, "k2": 2.5, "sigma_window": 40, "adx_max": 25, "max_bars": 18},
    ]:
        res = _run_one(params, df, split_idx=1000)
        assert "wf_score" in res, f"missing wf_score in result: {res}"
        assert "test_n" in res,   f"missing test_n in result: {res}"
        assert isinstance(res["wf_score"], float), f"wf_score not float: {res}"


def test_engine_smoke_runs():
    """P2d: vwap_fade in FastBacktestEngine on real MXF 5m data — smoke check."""
    import pandas as pd
    from pathlib import Path
    p = Path("data/vwap_fade/MXF_day_5m.parquet")
    if not p.exists():
        import pytest; pytest.skip(f"資料未準備：{p}（先跑 prepare_vwap_data.py）")
    from core.gpu_indicators import precompute_all
    from backtest.fast_engine import FastBacktestEngine
    from strategy.vwap_fade import VwapFadeStrategy

    df = pd.read_parquet(p).head(3000).reset_index(drop=True)
    indicators = precompute_all(df, verbose=False)
    engine = FastBacktestEngine(initial_balance=200_000, instrument="TMF")
    result = engine.run(df, indicators, VwapFadeStrategy(), "balanced")

    # 1) 必須有交易（補進場機會是本策略目的）
    assert len(result.trades) > 0, "vwap_fade 在 3000 根 bar 上零交易，檢查 k/min_warmup/entry_window"
    # 2) 不過度交易（max_trades 把關有生效）
    days = df["datetime"].dt.date.nunique()
    avg_per_day = len(result.trades) / max(days, 1)
    assert avg_per_day < 10, f"平均 {avg_per_day:.1f} 筆/日 過高，max_trades 守衛沒生效"
    # 3) 健康性：最終餘額為有限數
    assert result.final_balance > 0 and result.final_balance < 10 * 200_000
