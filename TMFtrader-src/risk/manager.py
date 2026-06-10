"""
TMFtrader 四層風控管理器
整合部位大小、每日限制、帳戶安全、系統熔斷
"""

import threading
from dataclasses import dataclass
from datetime import datetime, time
from typing import Optional

from loguru import logger

from core.market_data import MarketSnapshot
from core.position import PositionManager
from core.broker import AccountInfo
from strategy.base import Signal, SignalDirection
from risk.position_sizing import PositionSizer, RISK_PRESETS
from risk.circuit_breaker import CircuitBreaker
from risk.persistence import save_risk_state, load_risk_state
from strategy.filters import SessionManager, SessionPhase


@dataclass
class RiskDecision:
    """風控判定結果"""
    approved: bool
    quantity: int = 0
    adjusted_stop: float = 0.0
    rejection_reason: str = ""


class RiskManager:
    """
    四層風控管理器

    第 1 層：單筆風控 — 部位大小 + 停損合理性
    第 2 層：日內風控 — 每日虧損/交易次數上限
    第 3 層：帳戶風控 — 保證金水位 + 最大回撤
    第 4 層：系統風控 — 熔斷 + 交易時段
    """

    # 預設保證金（向後相容）
    MARGIN_PER_CONTRACT = 20600
    MAINTENANCE_MARGIN = 15800

    def __init__(self, profile: str = "balanced", persist: bool = True):
        self._lock = threading.Lock()
        self.position_sizer = PositionSizer(profile)
        self.circuit_breaker = CircuitBreaker(
            max_daily_loss=self.position_sizer.preset.max_daily_loss,
            max_consecutive_loss=self.position_sizer.preset.max_consecutive_loss,
            cooldown_minutes=self.position_sizer.preset.cooldown_minutes,
        )
        self._profile = profile
        self._peak_equity = 0.0
        self._sessions: dict[str, SessionManager] = {}
        self._persist_counter = 0  # 每 N 筆交易存一次
        self._backtest_mode = False  # 回測模式跳過盤別檢查
        self._persist = persist  # backtest/test 設 False，避免污染 production risk_state.json

        # 從磁碟恢復風控狀態（僅 live mode）
        saved = load_risk_state() if self._persist else None
        if saved:
            today = datetime.now().strftime("%Y-%m-%d")
            if saved.get("today") == today:
                self._peak_equity = saved.get("peak_equity", 0.0)
                self.circuit_breaker._daily_loss = saved.get("daily_loss", 0.0)
                self.circuit_breaker._consecutive_losses = saved.get("consecutive_losses", 0)
                self.circuit_breaker._today = today
                logger.info(f"[Risk] 恢復風控狀態: peak={self._peak_equity:,.0f} daily_loss={self.circuit_breaker._daily_loss:,.0f}")
            else:
                # 不同天，只恢復 peak_equity
                self._peak_equity = saved.get("peak_equity", 0.0)
                logger.info(f"[Risk] 新交易日，僅恢復 peak_equity={self._peak_equity:,.0f}")

    def set_profile(self, profile: str):
        """切換風險等級"""
        if profile not in RISK_PRESETS:
            return

        self._profile = profile
        self.position_sizer.set_profile(profile)
        preset = self.position_sizer.preset
        self.circuit_breaker.update_settings(
            max_daily_loss=preset.max_daily_loss,
            max_consecutive_loss=preset.max_consecutive_loss,
            cooldown_minutes=preset.cooldown_minutes,
        )
        logger.info(f"🎚️ 風險等級切換: {preset.label}")

    def evaluate(
        self,
        signal: Signal,
        position_manager: PositionManager,
        account: AccountInfo,
        snapshot: MarketSnapshot,
        instrument: str = "",
    ) -> RiskDecision:
        """
        評估訊號是否通過風控

        四層依序檢查，任一層不通過就拒絕
        """

        # ---- 第 4 層：系統風控（最先檢查）----
        # 熔斷檢查
        if not self.circuit_breaker.can_trade:
            return RiskDecision(
                approved=False,
                rejection_reason=f"熔斷中: {self.circuit_breaker.to_dict()['halt_reason']}",
            )

        # 盤別檢查（回測模式跳過）
        if not self._backtest_mode:
            if instrument not in self._sessions:
                self._sessions[instrument] = SessionManager(instrument)
            phase = self._sessions[instrument].get_phase()
            if phase in (SessionPhase.CLOSED, SessionPhase.PRE_OPEN):
                return RiskDecision(
                    approved=False,
                    rejection_reason="非交易時段",
                )
            # 收盤前 30 分鐘不開新倉（平倉除外）
            if phase == SessionPhase.LAST_30 and signal.direction != SignalDirection.CLOSE:
                return RiskDecision(
                    approved=False,
                    rejection_reason="收盤前 30 分鐘不開新倉",
                )
            # 收盤前 5 分鐘只允許平倉
            if phase == SessionPhase.CLOSING and signal.direction != SignalDirection.CLOSE:
                return RiskDecision(
                    approved=False,
                    rejection_reason="即將收盤，僅允許平倉",
                )

        # ---- 第 3 層：帳戶風控 ----
        equity = account.equity if account.equity > 0 else account.balance
        with self._lock:
            self._peak_equity = max(self._peak_equity, equity)

        # 根據商品取得保證金
        margin_per_contract = self.MARGIN_PER_CONTRACT
        if instrument and hasattr(position_manager, 'configs') and instrument in position_manager.configs:
            config = position_manager.configs[instrument]
            margin_per_contract = config.margin if hasattr(config, 'margin') else self.MARGIN_PER_CONTRACT

        # 帳戶餘額過低
        min_balance = margin_per_contract * 1.5
        if equity < min_balance:
            return RiskDecision(
                approved=False,
                rejection_reason=f"帳戶權益不足: {equity:,.0f} < {min_balance:,.0f}",
            )

        # 最大回撤檢查（回測模式跳過全局回撤，改用每日熔斷控制虧損）
        if self._peak_equity > 0 and not self._backtest_mode:
            drawdown_pct = (self._peak_equity - equity) / self._peak_equity
            max_dd = self.position_sizer.preset.max_drawdown_pct
            if drawdown_pct > max_dd:
                return RiskDecision(
                    approved=False,
                    rejection_reason=f"回撤超限: {drawdown_pct:.1%} > {max_dd:.1%}",
                )

        # ---- 第 2 層：日內風控 ----
        preset = self.position_sizer.preset

        # 每日交易次數
        daily_count = position_manager.get_daily_trade_count()
        if daily_count >= preset.max_daily_trades:
            return RiskDecision(
                approved=False,
                rejection_reason=f"每日交易次數已達上限: {daily_count}/{preset.max_daily_trades}",
            )

        # 每日虧損檢查（由 circuit_breaker 處理，這裡做預警）
        daily_pnl = position_manager.get_daily_pnl()
        if daily_pnl < -(preset.max_daily_loss * 0.8):
            logger.warning(f"⚠️ 每日虧損接近上限: {daily_pnl:,.0f} / -{preset.max_daily_loss:,.0f}")

        # ---- 第 1 層：單筆風控 ----
        # 該商品已有持倉不開新倉
        pos = position_manager.positions.get(instrument, position_manager.position) if instrument else position_manager.position
        if not pos.is_flat and signal.direction != SignalDirection.CLOSE:
            return RiskDecision(
                approved=False,
                rejection_reason=f"已有 {instrument} 持倉" if instrument else "已有持倉",
            )

        # 平倉訊號直接通過
        if signal.direction == SignalDirection.CLOSE:
            return RiskDecision(
                approved=True,
                quantity=pos.quantity,
            )

        # 計算停損距離
        stop_distance = abs(snapshot.price - signal.stop_loss)
        if stop_distance <= 0:
            if snapshot.atr > 0:
                stop_distance = snapshot.atr * 2
            else:
                return RiskDecision(
                    approved=False,
                    rejection_reason="停損距離和 ATR 均為 0，無法計算風險",
                )

        # 停損距離合理性檢查（危機模式停損倍數可達 6.5x，上限需配合）
        # max_sl_mult 由 risk preset 設定(預設 8;chips/maxpain 固定 −2% 停損用 fixed1_paper=30 放寬)
        atr = snapshot.atr if snapshot.atr > 0 else 50
        max_sl_mult = getattr(self.position_sizer.preset, "max_sl_mult", 8.0)
        if stop_distance > atr * max_sl_mult:
            return RiskDecision(
                approved=False,
                rejection_reason=f"停損距離過大: {stop_distance:.0f} 點 > {atr * max_sl_mult:.0f} 點",
            )

        if stop_distance < atr * 0.3:
            # 停損太小，調整到至少 1 ATR
            signal.stop_loss = (
                snapshot.price - atr if signal.is_buy else snapshot.price + atr
            )
            stop_distance = atr
            logger.info(f"📐 停損距離太小，自動調整到 {stop_distance:.0f} 點")

        # 計算部位大小（動態讀取 point_value）
        config = position_manager.configs.get(instrument) if instrument else None
        point_value = config.point_value if config and hasattr(config, 'point_value') else 10.0
        quantity = self.position_sizer.calculate(equity, stop_distance, point_value=point_value)

        if quantity <= 0:
            return RiskDecision(
                approved=False,
                rejection_reason=f"風險過大無法下單: 停損 {stop_distance:.0f} 點 × {point_value} 元/點 = {stop_distance * point_value:,.0f} 元 > 帳戶風險額度",
            )

        # 保證金檢查（考慮已佔用的保證金）
        total_margin_used = position_manager.get_total_margin_used() if hasattr(position_manager, 'get_total_margin_used') else 0
        available = equity - total_margin_used
        required_margin = quantity * margin_per_contract
        if required_margin > available and available > 0:
            quantity = max(1, int(available / margin_per_contract))

        # 相關性風控：如果其他商品已有同方向持倉，減半部位
        corr_mult = self._check_correlation(instrument, signal, position_manager)
        if corr_mult < 1.0:
            quantity = max(1, int(quantity * corr_mult))
            logger.info(f"[Risk] 相關性降倉: {instrument} → {quantity} 口（{corr_mult:.0%}）")

        return RiskDecision(
            approved=True,
            quantity=quantity,
            adjusted_stop=signal.stop_loss,
        )

    def on_trade_closed(self, pnl: float, expected_max_loss: float = 0):
        """交易關閉時通知風控 + 持久化狀態"""
        self.circuit_breaker.on_trade(pnl, expected_max_loss)
        self._persist_counter += 1
        # 每筆交易都持久化（交易不頻繁，IO 開銷可接受）
        self._save_state()

    def _save_state(self):
        """持久化風控狀態到磁碟"""
        if not self._persist:
            return
        try:
            save_risk_state(
                peak_equity=self._peak_equity,
                daily_loss=self.circuit_breaker._daily_loss,
                consecutive_losses=self.circuit_breaker._consecutive_losses,
                circuit_state=self.circuit_breaker.state.value,
                halt_reason=self.circuit_breaker._halt_reason,
                today=self.circuit_breaker._today or datetime.now().strftime("%Y-%m-%d"),
            )
        except Exception as e:
            logger.warning(f"[Risk] 持久化失敗: {e}")

    def _check_correlation(self, instrument: str, signal: Signal,
                          position_manager: PositionManager) -> float:
        """
        相關性風控：如果其他商品已有同方向持倉，降低新倉部位

        原理：TMF 和 TGF 在恐慌行情中高度相關（同漲同跌），
        如果都做多或都做空 = 2 倍曝險，需要減半。
        """
        for inst, pos in position_manager.positions.items():
            if inst == instrument or pos.is_flat:
                continue
            # 同方向 = 相關風險
            same_direction = (
                (signal.is_buy and pos.side.value == "long") or
                (signal.is_sell and pos.side.value == "short")
            )
            if same_direction:
                return 0.5  # 減半部位
        return 1.0

    def _in_trading_session(self) -> bool:
        """檢查是否有任何商品在交易時段（向後相容）"""
        if not self._sessions:
            # 沒有任何商品註冊過，用預設時段
            return SessionManager().is_in_session()
        return any(s.is_in_session() for s in self._sessions.values())

    def to_dict(self) -> dict:
        """序列化（供 Dashboard 顯示）"""
        return {
            "profile": self._profile,
            "preset": self.position_sizer.get_preset_info(),
            "circuit_breaker": self.circuit_breaker.to_dict(),
            "peak_equity": self._peak_equity,
            "in_trading_session": self._in_trading_session(),
        }
