"""
UltraTrader 自適應動量策略（主策略）
多因子進場 + 三層出場系統
"""

from datetime import datetime
from typing import Optional

from loguru import logger

from core.market_data import KBar, MarketSnapshot
from core.position import Position, Side
from strategy.base import BaseStrategy, Signal, SignalDirection
from strategy.filters import MarketRegime, MarketRegimeClassifier, SessionManager, SessionPhase
from strategy.signals import MultiFactorSignalGenerator, AdaptiveParams


class AdaptiveMomentumStrategy(BaseStrategy):
    """
    自適應動量策略 v2（世界頂尖水準升級版）

    升級內容：
    - 盤別感知：開盤 15 分提高門檻、收盤前 30 分不開新倉、收盤前 5 分強制平倉
    - 7 因子進場：新增多時框確認（5m/15m）
    - Chandelier Exit：替代簡易移動停利
    - 連續自適應停損：ATR 變化時停損跟著縮緊
    - 分段停利：2x/3x/4x ATR 各出 1/3
    - 滑價緩衝：所有停損停利含滑價修正
    """

    @property
    def name(self) -> str:
        return "自適應動量策略"

    def __init__(self):
        self.regime_classifier = MarketRegimeClassifier()
        self.signal_generator = MultiFactorSignalGenerator()
        self._cooldown_remaining = 0
        self._last_regime = MarketRegime.RANGING
        self._last_signal_strength = 0.0
        self._session = SessionManager()

    def on_kbar(self, kbar: KBar, snapshot: MarketSnapshot,
                snapshot_5m: MarketSnapshot = None,
                snapshot_15m: MarketSnapshot = None) -> Optional[Signal]:
        """K 棒收盤時的決策邏輯（升級版）"""

        # 0. 盤別檢查 — 收盤前 30 分不開新倉
        phase = self._session.get_phase()
        if phase in (SessionPhase.LAST_30, SessionPhase.CLOSING, SessionPhase.CLOSED):
            return None

        # 1. 分類市場狀態
        regime = self.regime_classifier.classify(snapshot)
        if regime != self._last_regime:
            logger.info(f"[Regime] {self._last_regime.value} -> {regime.value}")
            self._last_regime = regime

        # 2. 冷卻期中 → 不交易
        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1
            return None

        # 3. 劇烈波動 → 不交易（但危機模式例外）
        if regime == MarketRegime.VOLATILE:
            return None

        # 4. 盤整市 → 動量策略不進場
        if regime == MarketRegime.RANGING:
            return None

        # 5. 開盤波動期 → 提高訊號門檻
        open_boost = 0.0
        if phase == SessionPhase.OPEN_VOLATILE:
            open_boost = 0.10  # 開盤前 15 分鐘要求更強信號

        # 6. 危機崩跌 → 跟勢做空
        if regime == MarketRegime.CRISIS_DOWN:
            signal = self._crisis_short_signal(snapshot)
            if signal:
                if signal.strength < (0.60 + open_boost):
                    return None
                self._last_signal_strength = signal.strength
                logger.info(f"[CRISIS SHORT] strength: {signal.strength:.2f} | {signal.reason}")
            return signal

        # 7. 危機反轉 → 左側做多
        if regime == MarketRegime.CRISIS_REVERSAL:
            signal = self._crisis_reversal_signal(snapshot)
            if signal:
                self._last_signal_strength = signal.strength
                logger.info(f"[CRISIS REVERSAL] strength: {signal.strength:.2f} | {signal.reason}")
            return signal

        # 8. 200MA 方向濾鏡 — 逆勢單直接擋掉
        if snapshot.ema200 > 0:
            if regime in (MarketRegime.STRONG_TREND_UP, MarketRegime.WEAK_TREND_UP):
                if snapshot.price < snapshot.ema200:
                    return None  # 200MA 下方不做多
            elif regime in (MarketRegime.STRONG_TREND_DOWN, MarketRegime.WEAK_TREND_DOWN):
                if snapshot.price > snapshot.ema200:
                    return None  # 200MA 上方不做空

        # 9. ADX 太弱 → 不進場（趨勢不明確）
        if snapshot.adx < 25:
            return None

        # 10. 正常趨勢 → 多因子訊號（含多時框確認）
        signal = self.signal_generator.generate(snapshot, regime, snapshot_5m, snapshot_15m)

        if signal:
            # 開盤波動期提高門檻
            if signal.strength < (self.signal_generator.params.min_signal_strength + open_boost):
                return None
            self._last_signal_strength = signal.strength
            logger.info(
                f"[Signal] {signal.direction.value} | "
                f"strength: {signal.strength:.2f} | {signal.reason}"
            )

        return signal

    def _crisis_short_signal(self, snapshot: MarketSnapshot) -> Optional[Signal]:
        """
        危機崩跌做空策略
        歷史模式：崩盤初期（1-2 週），跟隨趨勢做空獲利
        寬停損 + 移動停利，讓利潤奔跑

        跳空保護：如果開盤已經大跌（價格遠低於 EMA60），
        代表崩盤可能已反映在開盤價，反彈風險高 → 不追空
        """
        atr = snapshot.atr if snapshot.atr > 0 else 50.0

        # 跳空保護：價格已經比 EMA60 低超過 8x ATR → 崩太多了，不追空
        if snapshot.ema60 > 0 and snapshot.price < snapshot.ema60 - atr * 8:
            return None

        # RSI 超賣保護：RSI < 20 代表已經超賣到極限，反彈機率高
        if snapshot.rsi < 20:
            return None

        # 空頭排列確認（指標暖機中時跳過此檢查）
        bearish_ema = False
        indicators_ready = snapshot.ema5 > 0 and snapshot.ema20 > 0
        if indicators_ready:
            bearish_ema = snapshot.ema5 < snapshot.ema20
            bearish_rsi = snapshot.rsi < 45
            if not (bearish_ema or bearish_rsi):
                return None

        # 危機模式降低訊號門檻 — 趨勢明確時積極進場
        strength = 0.6
        if snapshot.adx > 25:
            strength += 0.15
        if snapshot.rsi < 35:
            strength += 0.1
        if bearish_ema:
            strength += 0.1

        strength = min(strength, 1.0)

        # 超寬停損（7x ATR）— 崩盤波動極大，不被反彈洗出去
        stop_loss = snapshot.price + atr * 7.0
        take_profit = snapshot.price - atr * 10.0  # 高報酬比

        return Signal(
            direction=SignalDirection.SELL,
            strength=strength,
            stop_loss=round(stop_loss),
            take_profit=round(take_profit),
            reason=f"危機崩跌做空 | RSI={snapshot.rsi:.0f} ADX={snapshot.adx:.0f}",
            source="CrisisShort",
        )

    def _crisis_reversal_signal(self, snapshot: MarketSnapshot) -> Optional[Signal]:
        """
        危機反轉做多策略（左側交易 — 歷史級機會）
        歷史模式：
        - 1990 波灣戰爭：底部後 1 年 +29%
        - 2003 伊拉克戰爭：「Buy the invasion」，底部後 S&P +26.7%
        - P/C > 2.0 + VIX 穩定 = 歷史級底部訊號
        """
        atr = snapshot.atr if snapshot.atr > 0 else 50.0

        # 必須有超賣跡象才進場（RSI < 40），避免盲目抄底
        if snapshot.rsi >= 40:
            return None

        # 需要 EMA 止跌跡象（EMA5 不再創新低 or 接近 EMA20）
        if snapshot.ema5 > 0 and snapshot.ema20 > 0:
            gap_pct = (snapshot.ema20 - snapshot.ema5) / snapshot.ema20 if snapshot.ema20 > 0 else 0
            if gap_pct > 0.03:  # EMA5 仍遠低於 EMA20 超過 3%，尚未止跌
                return None

        # 反轉訊號：RSI 超賣反彈
        strength = 0.7  # 基礎分高（危機反轉是高確信度交易）

        if snapshot.rsi < 30:
            strength += 0.15  # RSI 極度超賣
        if snapshot.rsi > 25 and snapshot.rsi < 40:
            strength += 0.1  # RSI 開始從底部回升

        strength = min(strength, 1.0)

        # 更寬的停損（4x ATR）— 給反轉足夠空間
        stop_loss = snapshot.price - atr * 4.0
        take_profit = snapshot.price + atr * 8.0  # 極高報酬比（歷史級行情）

        return Signal(
            direction=SignalDirection.BUY,
            strength=strength,
            stop_loss=round(stop_loss),
            take_profit=round(take_profit),
            reason=f"危機反轉做多 | 左側訊號觸發 | RSI={snapshot.rsi:.0f}",
            source="CrisisReversal",
        )

    def check_exit(self, position: Position, snapshot: MarketSnapshot) -> Optional[Signal]:
        """
        出場檢查（升級版 v3 — 七層保護）

        第 0 層：盤別強制平倉（收盤前 5 分鐘）
        第 1 層：固定停損 + 連續自適應收緊
        第 1.5 層：保本停損（獲利 > 1x ATR → 停損移到進場價）
        第 2 層：Chandelier Exit（追蹤最高點回撤）
        第 2.5 層：利潤回吐保護（最多回吐 50% 最大未實現獲利）
        第 3 層：分段停利（動態 TP levels）
        第 4 層：時間停損（持倉過久 + 獲利不足）
        """
        if position.is_flat:
            return None

        price = snapshot.price
        atr = snapshot.atr if snapshot.atr > 0 else 50.0
        params = self.signal_generator.params

        # ---- 第 0 層：盤別強制平倉 ----
        phase = self._session.get_phase()
        if phase == SessionPhase.CLOSING:
            return Signal(
                direction=SignalDirection.CLOSE, strength=1.0,
                stop_loss=0, take_profit=0,
                reason=f"盤別收盤平倉 @ {price:.0f}",
                source=self.name,
            )

        # ---- 第 1 層：固定停損 + 連續自適應收緊 ----
        if position.stop_loss > 0:
            # 自適應停損：如果 ATR 縮小，把停損往有利方向收緊
            if position.side == Side.LONG:
                adaptive_stop = position.entry_price - atr * params.stop_loss_multiplier
                # 只能收緊（往上移），不能放寬
                if adaptive_stop > position.stop_loss:
                    position.stop_loss = round(adaptive_stop)

                if price <= position.stop_loss:
                    self._cooldown_remaining = params.cooldown_bars
                    return Signal(
                        direction=SignalDirection.CLOSE, strength=1.0,
                        stop_loss=0, take_profit=0,
                        reason=f"停損觸發 @ {price:.0f}（停損價 {position.stop_loss:.0f}）",
                        source=self.name,
                    )

            elif position.side == Side.SHORT:
                adaptive_stop = position.entry_price + atr * params.stop_loss_multiplier
                if adaptive_stop < position.stop_loss:
                    position.stop_loss = round(adaptive_stop)

                if price >= position.stop_loss:
                    self._cooldown_remaining = params.cooldown_bars
                    return Signal(
                        direction=SignalDirection.CLOSE, strength=1.0,
                        stop_loss=0, take_profit=0,
                        reason=f"停損觸發 @ {price:.0f}（停損價 {position.stop_loss:.0f}）",
                        source=self.name,
                    )

        # ---- 第 1.5 層：保本停損（獲利 > 1.5x ATR → 停損移到進場價）----
        if position.side == Side.LONG:
            profit_pts = price - position.entry_price
        else:
            profit_pts = position.entry_price - price

        # 更新最大未實現獲利
        if profit_pts > position.max_unrealized_profit:
            position.max_unrealized_profit = profit_pts

        if profit_pts > atr * 2.0 and not position.breakeven_activated:
            position.breakeven_activated = True
            # 保本停損 = 進場價 + 0.5 ATR（給呼吸空間，不會被小回檔洗掉）
            be_buffer = round(atr * 0.5)
            if position.side == Side.LONG:
                be_stop = position.entry_price + be_buffer
                if be_stop > position.stop_loss:
                    position.stop_loss = round(be_stop)
                    logger.info(f"[保本] LONG 停損移至 {position.stop_loss:.0f}（成本+{be_buffer}，獲利 {profit_pts:.0f} > {atr:.0f}）")
            else:
                # SHORT 保本：停損移到進場價上方（收緊，不是放寬）
                be_stop = position.entry_price - be_buffer
                if be_stop < position.stop_loss:
                    position.stop_loss = round(be_stop)
                    logger.info(f"[保本] SHORT 停損移至 {position.stop_loss:.0f}（成本-{be_buffer}，獲利 {profit_pts:.0f} > {atr:.0f}）")

        # ---- 第 2 層：Chandelier Exit（用當前 ATR，不是進場時的）----
        chandelier_mult = params.trailing_distance

        if position.side == Side.LONG:
            profit = price - position.entry_price

            if profit > atr * params.trailing_trigger:
                if not position.trailing_activated:
                    position.trailing_activated = True
                    logger.info(f"[Chandelier] LONG activated (profit {profit:.0f})")

                # Chandelier Exit = highest_high - N × current_ATR
                chandelier_stop = position.highest_since_entry - atr * chandelier_mult
                if chandelier_stop > position.trailing_stop:
                    position.trailing_stop = round(chandelier_stop)

                if price <= position.trailing_stop:
                    return Signal(
                        direction=SignalDirection.CLOSE, strength=1.0,
                        stop_loss=0, take_profit=0,
                        reason=f"Chandelier停利 @ {price:.0f}（追蹤 {position.trailing_stop:.0f}）",
                        source=self.name,
                    )

        elif position.side == Side.SHORT:
            profit = position.entry_price - price

            if profit > atr * params.trailing_trigger:
                if not position.trailing_activated:
                    position.trailing_activated = True
                    logger.info(f"[Chandelier] SHORT activated (profit {profit:.0f})")

                chandelier_stop = position.lowest_since_entry + atr * chandelier_mult
                if chandelier_stop < position.trailing_stop or position.trailing_stop == 0:
                    position.trailing_stop = round(chandelier_stop)

                if price >= position.trailing_stop:
                    return Signal(
                        direction=SignalDirection.CLOSE, strength=1.0,
                        stop_loss=0, take_profit=0,
                        reason=f"Chandelier停利 @ {price:.0f}（追蹤 {position.trailing_stop:.0f}）",
                        source=self.name,
                    )

        # ---- 第 2.5 層：利潤回吐保護（最多回吐 35% 最大未實現獲利）----
        if position.max_unrealized_profit > atr * 2.0:
            giveback = position.max_unrealized_profit - profit_pts
            max_giveback = position.max_unrealized_profit * 0.35
            if giveback > max_giveback:
                return Signal(
                    direction=SignalDirection.CLOSE, strength=1.0,
                    stop_loss=0, take_profit=0,
                    reason=f"利潤回吐保護 @ {price:.0f}（最高獲利 {position.max_unrealized_profit:.0f}，回吐 {giveback:.0f} > 65%）",
                    source=self.name,
                )

        # ---- 第 3 層：分段停利 ----
        if position.take_profit_levels:
            for tp_price, fraction in position.take_profit_levels:
                hit = (position.side == Side.LONG and price >= tp_price) or \
                      (position.side == Side.SHORT and price <= tp_price)
                if hit:
                    # 移除已觸發的 level
                    position.take_profit_levels = [
                        (p, f) for p, f in position.take_profit_levels
                        if p != tp_price
                    ]
                    return Signal(
                        direction=SignalDirection.CLOSE,
                        strength=fraction,  # fraction 表示出多少比例
                        stop_loss=0, take_profit=0,
                        reason=f"分段停利 @ {price:.0f}（目標 {tp_price:.0f}，出 {fraction:.0%}）",
                        source=self.name,
                    )

        # ---- 第 4 層：時間停損（只在虧損時觸發，獲利中不強制出場）----
        if position.bars_since_entry > params.time_stop_bars:
            if position.side == Side.LONG:
                profit = price - position.entry_price
            else:
                profit = position.entry_price - price

            # 持倉超時且虧損中 → 出場（獲利中讓它繼續跑）
            if profit < 0:
                return Signal(
                    direction=SignalDirection.CLOSE, strength=0.8,
                    stop_loss=0, take_profit=0,
                    reason=f"時間停損: {position.bars_since_entry}根K棒 仍在虧損",
                    source=self.name,
                )

        # ---- 最終停利（兜底）----
        if position.take_profit > 0:
            if position.side == Side.LONG and price >= position.take_profit:
                return Signal(
                    direction=SignalDirection.CLOSE, strength=1.0,
                    stop_loss=0, take_profit=0,
                    reason=f"停利觸發 @ {price:.0f}",
                    source=self.name,
                )
            if position.side == Side.SHORT and price <= position.take_profit:
                return Signal(
                    direction=SignalDirection.CLOSE, strength=1.0,
                    stop_loss=0, take_profit=0,
                    reason=f"停利觸發 @ {price:.0f}",
                    source=self.name,
                )

        return None

    def get_parameters(self) -> dict:
        """取得當前參數"""
        return {
            "strategy": self.name,
            "regime": self._last_regime.value,
            "signal_strength": round(self._last_signal_strength, 2),
            "cooldown_remaining": self._cooldown_remaining,
            "adaptive_params": self.signal_generator.params.to_dict(),
        }

    def reset(self):
        """重置策略狀態"""
        self.regime_classifier.reset()
        self._cooldown_remaining = 0
        self._last_regime = MarketRegime.RANGING
        self._last_signal_strength = 0.0
