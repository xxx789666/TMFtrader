"""
TMFtrader 快速回測引擎（預計算版）
從 gpu_indicators.precompute_all() 的輸出讀取預計算的指標陣列
每根 K 棒只做 O(1) 查表，省去重複計算 → 10~50x 加速

使用方式：
    from core.gpu_indicators import precompute_all
    from backtest.fast_engine import FastBacktestEngine

    indicators = precompute_all(df)  # GPU/JIT 一次算完
    engine = FastBacktestEngine()
    result = engine.run(df, indicators, strategy)
"""

from datetime import datetime, timedelta
from typing import Optional

import numpy as np

from core.market_data import KBar
from core.position import PositionManager
from core.instrument_config import INSTRUMENT_SPECS
from strategy.base import BaseStrategy, Signal, SignalDirection
from strategy.momentum import AdaptiveMomentumStrategy
from risk.manager import RiskManager
from risk.circuit_breaker import CircuitState
from core.broker import AccountInfo
from core.gpu_indicators import snapshot_from_precomputed
from backtest.engine import BacktestResult


class FastBacktestEngine:
    """
    預計算指標版回測引擎

    相比 BacktestEngine：
    - 每 bar 不再呼叫 IndicatorEngine.update()（原先需掃描 200 根窗口）
    - 改為 O(1) 查表 snapshot_from_precomputed()
    - 在 optimize_strategy.py 中先用 GPU 算好所有指標，再多核並行模擬
    """

    def __init__(
        self,
        initial_balance: float = 200_000.0,
        slippage: int = 1,
        commission: float = 18.0,
        instrument: str = "TMF",
        intrabar_hard_exits: bool = False,
    ):
        self.initial_balance = initial_balance
        self.slippage = slippage
        self.commission = commission
        self.instrument = instrument
        # 預設 False(維持既有回測行為)。True = 用 bar high/low 在盤中強制檢查
        # pos.stop_loss / pos.take_profit，複製 live core/engine.py 的 tick 層硬停損/止盈。
        self.intrabar_hard_exits = intrabar_hard_exits

    def run(
        self,
        df,                     # pd.DataFrame：datetime / open / high / low / close / volume
        indicators: dict,       # precompute_all() 的輸出
        strategy: Optional[BaseStrategy] = None,
        risk_profile: str = "balanced",
        start_idx: int = 0,     # 從哪一根 bar 開始（walk-forward 用）
        end_idx: int = -1,      # 到哪一根 bar 結束（-1 = 最後）
    ) -> BacktestResult:

        if strategy is None:
            strategy = AdaptiveMomentumStrategy()

        n = len(df)
        if end_idx < 0:
            end_idx = n
        start_idx = max(10, start_idx)  # 至少 10 根 warm-up

        inst = self.instrument
        spec = INSTRUMENT_SPECS.get(inst)
        configs = {inst: spec} if spec else {}

        position_manager = PositionManager(
            instruments=[inst], configs=configs,
            initial_balance=self.initial_balance,
        )
        risk_manager = RiskManager(profile=risk_profile, persist=False)
        risk_manager._peak_equity = self.initial_balance
        risk_manager._backtest_mode = True

        # Session manager 使用 K 棒時間
        if hasattr(strategy, "_session"):
            original_get_phase = strategy._session.get_phase
            def backtest_get_phase(now=None):
                return original_get_phase(now=self._current_bar_time)
            strategy._session.get_phase = backtest_get_phase
        self._current_bar_time = None

        balance = self.initial_balance
        equity_curve = [balance]
        daily_pnl = {}
        _last_backtest_date = None
        bar_count = 0

        datetimes = df["datetime"].values  # numpy array for fast access

        for i in range(start_idx, end_idx):
            bar_time = datetimes[i]
            # numpy.datetime64 / pandas Timestamp → Python datetime
            if hasattr(bar_time, "to_pydatetime"):
                bar_time = bar_time.to_pydatetime()
            elif hasattr(bar_time, "item"):
                # numpy.datetime64
                import pandas as pd
                bar_time = pd.Timestamp(bar_time).to_pydatetime()

            bar_count += 1
            self._current_bar_time = bar_time
            bar_date = bar_time.strftime("%Y-%m-%d")

            # ── 每日風控重置
            if bar_date != _last_backtest_date:
                _last_backtest_date = bar_date
                cb = risk_manager.circuit_breaker
                with cb._lock:
                    cb._today = bar_date
                    cb._daily_loss = 0
                    cb._consecutive_losses = 0
                    if cb._state == CircuitState.HALTED:
                        cb._state = CircuitState.ACTIVE
                        cb._halt_reason = ""

            # ── 冷卻時間（以 K 棒時間為基準）
            cb = risk_manager.circuit_breaker
            with cb._lock:
                cb._trade_timestamps = [
                    t for t in cb._trade_timestamps
                    if (bar_time - t) < timedelta(minutes=cb.trade_window_minutes)
                ]
                if cb._state == CircuitState.COOLDOWN and cb._cooldown_until:
                    if bar_time >= cb._cooldown_until:
                        cb._state = CircuitState.ACTIVE
                        cb._cooldown_until = None

            # ── O(1) 快照查表（不再重算指標）
            snapshot = snapshot_from_precomputed(i, indicators, bar_count, bar_time)

            close_price = float(indicators["close"][i])
            kbar = KBar(
                datetime=bar_time,
                open=float(indicators["open"][i]),
                high=float(indicators["high"][i]),
                low=float(indicators["low"][i]),
                close=close_price,
                volume=int(indicators["volume"][i]),
            )

            position_manager.update_price(inst, kbar.close)
            position_manager.increment_bars(inst)

            # ── 出場
            pos = position_manager.positions[inst]
            if not pos.is_flat:
                from core.position import Side
                # intrabar 硬停損/止盈（複製 live core/engine.py tick 層；停損優先=保守）
                hard_px, hard_reason = None, None
                if self.intrabar_hard_exits:
                    hi = float(indicators["high"][i]); lo = float(indicators["low"][i])
                    if pos.side == Side.LONG:
                        if pos.stop_loss > 0 and lo <= pos.stop_loss:
                            hard_px, hard_reason = pos.stop_loss, f"硬停損 @ {pos.stop_loss:.0f}"
                        elif pos.take_profit > 0 and hi >= pos.take_profit:
                            hard_px, hard_reason = pos.take_profit, f"硬停利 @ {pos.take_profit:.0f}"
                    else:
                        if pos.stop_loss > 0 and hi >= pos.stop_loss:
                            hard_px, hard_reason = pos.stop_loss, f"硬停損 @ {pos.stop_loss:.0f}"
                        elif pos.take_profit > 0 and lo <= pos.take_profit:
                            hard_px, hard_reason = pos.take_profit, f"硬停利 @ {pos.take_profit:.0f}"

                exit_signal = None if hard_px is not None else strategy.check_exit(pos, snapshot)
                if hard_px is not None or exit_signal:
                    if hard_px is not None:
                        ep, reason, close_qty = hard_px, hard_reason, 0
                    else:
                        ep = kbar.close
                        if self.slippage > 0:
                            ep = ep - self.slippage if pos.side == Side.LONG else ep + self.slippage
                        reason = exit_signal.reason
                        close_qty = getattr(exit_signal, 'close_quantity', 0)
                    trade = position_manager.close_position(inst, ep, reason, kbar.datetime, quantity=close_qty)
                    if trade:
                        balance += trade.net_pnl
                        # 僅全平時才觸發風控回調（部分平倉不計入連損）
                        if position_manager.positions[inst].is_flat:
                            risk_manager.on_trade_closed(trade.net_pnl)
                            with cb._lock:
                                if cb._trade_timestamps:
                                    cb._trade_timestamps[-1] = kbar.datetime
                                if cb._state == CircuitState.COOLDOWN and cb._cooldown_until:
                                    cb._cooldown_until = kbar.datetime + timedelta(minutes=cb.cooldown_minutes)
                        day = trade.exit_time.strftime("%Y-%m-%d")
                        daily_pnl[day] = daily_pnl.get(day, 0) + trade.net_pnl

            # ── 進場
            pos = position_manager.positions[inst]
            if pos.is_flat:
                entry_signal = strategy.on_kbar(kbar, snapshot)
                if entry_signal:
                    account = AccountInfo(
                        balance=balance, equity=balance, margin_available=balance,
                    )
                    decision = risk_manager.evaluate(
                        entry_signal, position_manager, account, snapshot, instrument=inst,
                    )
                    if decision.approved:
                        from core.position import Side
                        ep = kbar.close
                        if self.slippage > 0:
                            ep = ep + self.slippage if entry_signal.is_buy else ep - self.slippage
                        side = Side.LONG if entry_signal.is_buy else Side.SHORT
                        position_manager.open_position(
                            instrument=inst, side=side, price=ep,
                            quantity=decision.quantity,
                            stop_loss=entry_signal.stop_loss,
                            take_profit=entry_signal.take_profit,
                            timestamp=kbar.datetime,
                        )

            pos = position_manager.positions[inst]
            equity_curve.append(balance + pos.unrealized_pnl(kbar.close))

        # 收尾強制平倉
        pos = position_manager.positions[inst]
        if not pos.is_flat:
            last_close = float(indicators["close"][end_idx - 1])
            last_dt = datetimes[end_idx - 1]
            if hasattr(last_dt, "to_pydatetime"):
                last_dt = last_dt.to_pydatetime()
            elif hasattr(last_dt, "item"):
                import pandas as _pd
                last_dt = _pd.Timestamp(last_dt).to_pydatetime()
            trade = position_manager.close_position(inst, last_close, "回測結束", last_dt)
            if trade:
                balance += trade.net_pnl

        return BacktestResult(
            trades=[
                {
                    "entry_time": t.entry_time.isoformat() if isinstance(t.entry_time, datetime) else str(t.entry_time),
                    "exit_time":  t.exit_time.isoformat() if isinstance(t.exit_time, datetime) else str(t.exit_time),
                    "side":  t.side,
                    "entry_price": t.entry_price,
                    "exit_price":  t.exit_price,
                    "quantity": t.quantity,
                    "pnl": round(t.net_pnl, 0),
                    "pnl_points": round(t.pnl_points, 0),
                    "reason": t.reason,
                    "bars_held": t.bars_held,
                }
                for t in position_manager.trades
            ],
            equity_curve=equity_curve,
            daily_pnl=daily_pnl,
            total_bars=end_idx - start_idx,
            start_date=str(datetimes[start_idx]),
            end_date=str(datetimes[end_idx - 1]),
            initial_balance=self.initial_balance,
            final_balance=balance,
        )
