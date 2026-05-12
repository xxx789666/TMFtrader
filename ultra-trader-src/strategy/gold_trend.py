"""
UltraTrader 黃金趨勢策略（頂尖升級版）
RegimeClassifier + 7 因子多因子進場 + 7 層出場保護
針對黃金避險特性調整：危機 = 做多（避險資金湧入）
"""

from typing import Optional

from loguru import logger

from core.market_data import KBar, MarketSnapshot
from core.position import Position, Side
from strategy.base import BaseStrategy, Signal, SignalDirection
from strategy.filters import MarketRegime, MarketRegimeClassifier, SessionManager, SessionPhase
from strategy.signals import MultiFactorSignalGenerator, AdaptiveParams


class GoldAdaptiveParams(AdaptiveParams):
    """
    黃金專用自適應參數

    與台指期差異：
    - ATR 較小（3~8 點 vs 50~200 點），停損倍數需較大
    - 黃金趨勢慣性強，trailing 給更多空間
    - 危機模式 = 做多（避險），與台指期相反
    """

    def __init__(self):
        super().__init__()
        # 黃金預設值（比台指期更寬）
        self.stop_loss_multiplier = 3.0
        self.min_signal_strength = 0.55
        self.trailing_trigger = 2.0
        self.trailing_distance = 1.5
        self.time_stop_bars = 60
        self.cooldown_bars = 3
        self.slippage_ticks = 1

    def update(self, atr_ratio: float, regime: MarketRegime):
        """黃金版自適應參數（EMA 平滑）"""
        alpha = 0.3

        if regime in (MarketRegime.CRISIS_DOWN, MarketRegime.CRISIS_REVERSAL):
            # 黃金危機 = 避險湧入，波動劇烈但趨勢明確
            target_sl = 5.0
            target_sig = 0.55
            target_trail = 3.5
            target_dist = 2.5
            target_time = 90    # 黃金危機趨勢持續更久
            target_cool = 5
            self.slippage_ticks = 3
        elif atr_ratio > 1.5:
            target_sl = 4.0
            target_sig = 0.65
            target_trail = 2.5
            target_dist = 2.0
            target_time = 60
            target_cool = 5
            self.slippage_ticks = 2
        elif atr_ratio < 0.7:
            target_sl = 2.0
            target_sig = 0.50
            target_trail = 1.5
            target_dist = 1.0
            target_time = 40
            target_cool = 3
            self.slippage_ticks = 1
        else:
            target_sl = 3.0
            target_sig = 0.55
            target_trail = 2.0
            target_dist = 1.5
            target_time = 60
            target_cool = 3
            self.slippage_ticks = 1

        # EMA 平滑
        self.stop_loss_multiplier = self.stop_loss_multiplier * (1 - alpha) + target_sl * alpha
        self.min_signal_strength = self.min_signal_strength * (1 - alpha) + target_sig * alpha
        self.trailing_trigger = self.trailing_trigger * (1 - alpha) + target_trail * alpha
        self.trailing_distance = self.trailing_distance * (1 - alpha) + target_dist * alpha
        self.time_stop_bars = round(self.time_stop_bars * (1 - alpha) + target_time * alpha)
        self.cooldown_bars = round(self.cooldown_bars * (1 - alpha) + target_cool * alpha)

        # 強趨勢降低門檻
        if regime in (MarketRegime.STRONG_TREND_UP, MarketRegime.STRONG_TREND_DOWN):
            self.min_signal_strength *= 0.85
            self.cooldown_bars = max(2, self.cooldown_bars)

        # 邊界檢查（黃金版：停損上限更高）
        self.stop_loss_multiplier = max(1.5, min(self.stop_loss_multiplier, 7.0))
        self.min_signal_strength = max(0.40, min(self.min_signal_strength, 0.85))
        self.trailing_trigger = max(1.0, min(self.trailing_trigger, 5.0))
        self.trailing_distance = max(0.8, min(self.trailing_distance, 4.0))
        self.time_stop_bars = max(20, min(self.time_stop_bars, 120))
        self.cooldown_bars = max(1, min(self.cooldown_bars, 15))


class GoldTrendStrategy(BaseStrategy):
    """
    黃金趨勢策略 v2（頂尖升級版）

    升級內容：
    - MarketRegimeClassifier：8 態市場分類 + 遲滯防抖
    - 7 因子多因子進場：趨勢/RSI/突破/量能/ADX/波動率/多時框
    - GoldAdaptiveParams：黃金專用 EMA 平滑自適應參數
    - 7 層出場保護：盤別/停損/保本/Chandelier/回吐/分段停利/時間
    - 危機模式特殊處理：黃金是避險資產，危機 = 做多（與台指期相反）

    參數（針對黃金波動特性）：
    - 黃金 ATR 通常 3~8 點（TWD/公克）
    - 每點 10 元，10 點 ATR 停損 = 100 元/口
    """

    @property
    def name(self) -> str:
        return "黃金趨勢策略"

    def __init__(self):
        self.regime_classifier = MarketRegimeClassifier()
        self.signal_generator = MultiFactorSignalGenerator()
        # 替換為黃金專用自適應參數
        self.signal_generator.params = GoldAdaptiveParams()
        self._cooldown_remaining = 0
        self._last_regime = MarketRegime.RANGING
        self._last_signal_strength = 0.0
        self._session = SessionManager("TGF")

    def on_kbar(self, kbar: KBar, snapshot: MarketSnapshot,
                snapshot_5m: MarketSnapshot = None,
                snapshot_15m: MarketSnapshot = None) -> Optional[Signal]:
        """K 棒收盤時的決策邏輯（頂尖版）"""

        # 0. 盤別檢查
        phase = self._session.get_phase()
        if phase in (SessionPhase.LAST_30, SessionPhase.CLOSING, SessionPhase.CLOSED):
            return None

        # 1. 分類市場狀態
        regime = self.regime_classifier.classify(snapshot)
        if regime != self._last_regime:
            logger.info(f"[Gold Regime] {self._last_regime.value} -> {regime.value}")
            self._last_regime = regime

        # 2. 冷卻期
        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1
            return None

        # 3. 劇烈波動 → 不交易（但危機模式例外）
        if regime == MarketRegime.VOLATILE:
            return None

        # 4. 盤整市 → 動量策略不進場
        if regime == MarketRegime.RANGING:
            return None

        # 5. 開盤波動期 → 提高門檻
        open_boost = 0.0
        if phase == SessionPhase.OPEN_VOLATILE:
            open_boost = 0.10

        # 6. 危機模式（黃金特殊：危機 = 避險 = 做多）
        if regime == MarketRegime.CRISIS_DOWN:
            # 台指期危機做空，但黃金危機做多（避險資金湧入）
            signal = self._crisis_long_signal(snapshot)
            if signal:
                if signal.strength < (0.55 + open_boost):
                    return None
                self._last_signal_strength = signal.strength
                logger.info(f"[Gold CRISIS LONG] strength: {signal.strength:.2f} | {signal.reason}")
            return signal

        # 7. 危機反轉（黃金：避險結束 → 金價回落 → 做空）
        if regime == MarketRegime.CRISIS_REVERSAL:
            signal = self._crisis_reversal_signal(snapshot)
            if signal:
                self._last_signal_strength = signal.strength
                logger.info(f"[Gold CRISIS REVERSAL] strength: {signal.strength:.2f} | {signal.reason}")
            return signal

        # 8. 正常趨勢 → 多因子訊號（含多時框確認）
        signal = self.signal_generator.generate(snapshot, regime, snapshot_5m, snapshot_15m)

        if signal:
            if signal.strength < (self.signal_generator.params.min_signal_strength + open_boost):
                return None
            # 黃金價格精度：小數點 1 位
            signal.stop_loss = round(signal.stop_loss, 1)
            signal.take_profit = round(signal.take_profit, 1)
            if signal.take_profit_levels:
                signal.take_profit_levels = [
                    (round(p, 1), f) for p, f in signal.take_profit_levels
                ]
            signal.source = "GoldTrend"
            self._last_signal_strength = signal.strength
            logger.info(
                f"[Gold Signal] {signal.direction.value} | "
                f"strength: {signal.strength:.2f} | {signal.reason}"
            )

        return signal

    def _crisis_long_signal(self, snapshot: MarketSnapshot) -> Optional[Signal]:
        """
        危機避險做多策略（黃金特有）

        歷史模式：
        - 戰爭/地緣政治 → 避險資金湧入黃金 → 金價飆漲
        - 2020 COVID：黃金 $1,500 → $2,075（+38%）
        - 2022 俄烏戰爭：黃金急漲 $100+
        - 寬停損 + 移動停利，讓避險趨勢奔跑
        """
        atr = snapshot.atr if snapshot.atr > 0 else 5.0

        # 跳空保護：價格已經比 EMA60 高超過 8x ATR → 漲太多了，不追多
        if snapshot.ema60 > 0 and snapshot.price > snapshot.ema60 + atr * 8:
            return None

        # RSI 超買保護
        if snapshot.rsi > 82:
            return None

        # 多頭排列確認
        bullish_ema = False
        indicators_ready = snapshot.ema5 > 0 and snapshot.ema20 > 0
        if indicators_ready:
            bullish_ema = snapshot.ema5 > snapshot.ema20
            bullish_rsi = snapshot.rsi > 55
            if not (bullish_ema or bullish_rsi):
                return None

        strength = 0.6
        if snapshot.adx > 25:
            strength += 0.15
        if snapshot.rsi > 55 and snapshot.rsi < 80:
            strength += 0.1
        if bullish_ema:
            strength += 0.1
        if snapshot.plus_di > snapshot.minus_di:
            strength += 0.05
        strength = min(strength, 1.0)

        # 寬停損（5x ATR）— 危機波動大
        stop_loss = snapshot.price - atr * 5.0 - 1
        take_profit = snapshot.price + atr * 10.0

        tp_levels = [
            (round(snapshot.price + atr * 3, 1), 0.25),
            (round(snapshot.price + atr * 5, 1), 0.25),
            (round(snapshot.price + atr * 7, 1), 0.25),
            (round(snapshot.price + atr * 10, 1), 0.25),
        ]

        return Signal(
            direction=SignalDirection.BUY,
            strength=strength,
            stop_loss=round(stop_loss, 1),
            take_profit=round(take_profit, 1),
            reason=f"危機避險做多 | RSI={snapshot.rsi:.0f} ADX={snapshot.adx:.0f}",
            source="GoldCrisisLong",
            take_profit_levels=tp_levels,
            slippage_buffer=2,
        )

    def _crisis_reversal_signal(self, snapshot: MarketSnapshot) -> Optional[Signal]:
        """
        危機反轉做空策略（黃金特有）

        歷史模式：
        - 危機結束 → 避險資金撤出黃金 → 金價回落
        - 戰爭結束/停火消息 → 黃金急跌
        - 需要確認多頭動能衰退才進場
        """
        atr = snapshot.atr if snapshot.atr > 0 else 5.0

        # 必須有超買回落跡象（RSI > 60 代表之前漲太多）
        if snapshot.rsi <= 60:
            return None

        # 需要 EMA 開始走弱（EMA5 跌破 EMA20 或接近）
        if snapshot.ema5 > 0 and snapshot.ema20 > 0:
            gap_pct = (snapshot.ema5 - snapshot.ema20) / snapshot.ema20 if snapshot.ema20 > 0 else 0
            if gap_pct > 0.03:  # EMA5 仍遠高於 EMA20，尚未走弱
                return None

        # 空頭 DI 確認
        if snapshot.plus_di > snapshot.minus_di * 1.3:
            return None  # 多頭仍強勢

        strength = 0.65
        if snapshot.rsi > 70:
            strength += 0.1
        if snapshot.ema5 < snapshot.ema20:
            strength += 0.15
        if snapshot.minus_di > snapshot.plus_di:
            strength += 0.1
        strength = min(strength, 1.0)

        # 較寬停損（4x ATR）
        stop_loss = snapshot.price + atr * 4.0 + 1
        take_profit = snapshot.price - atr * 6.0

        return Signal(
            direction=SignalDirection.SELL,
            strength=strength,
            stop_loss=round(stop_loss, 1),
            take_profit=round(take_profit, 1),
            reason=f"危機反轉做空 | 避險退潮 | RSI={snapshot.rsi:.0f}",
            source="GoldCrisisReversal",
        )

    def check_exit(self, position: Position, snapshot: MarketSnapshot) -> Optional[Signal]:
        """
        出場檢查（頂尖版 — 七層保護）

        第 0 層：盤別強制平倉
        第 1 層：固定停損 + 連續自適應收緊
        第 1.5 層：保本停損（獲利 > 1x ATR → 停損移到進場價）
        第 2 層：Chandelier Exit（追蹤最高/最低點）
        第 2.5 層：利潤回吐保護（最多回吐 50%）
        第 3 層：分段停利
        第 4 層：時間停損
        """
        if position.is_flat:
            return None

        price = snapshot.price
        atr = snapshot.atr if snapshot.atr > 0 else 5.0
        params = self.signal_generator.params

        # ---- 第 0 層：盤別強制平倉 ----
        phase = self._session.get_phase()
        if phase == SessionPhase.CLOSING:
            return Signal(
                direction=SignalDirection.CLOSE, strength=1.0,
                stop_loss=0, take_profit=0,
                reason=f"盤別收盤平倉 @ {price:.1f}",
                source=self.name,
            )

        # ---- 第 1 層：固定停損 + 連續自適應收緊 ----
        if position.stop_loss > 0:
            if position.side == Side.LONG:
                adaptive_stop = position.entry_price - atr * params.stop_loss_multiplier
                if adaptive_stop > position.stop_loss:
                    position.stop_loss = round(adaptive_stop, 1)

                if price <= position.stop_loss:
                    self._cooldown_remaining = params.cooldown_bars
                    return Signal(
                        direction=SignalDirection.CLOSE, strength=1.0,
                        stop_loss=0, take_profit=0,
                        reason=f"停損觸發 @ {price:.1f}（停損價 {position.stop_loss:.1f}）",
                        source=self.name,
                    )

            elif position.side == Side.SHORT:
                adaptive_stop = position.entry_price + atr * params.stop_loss_multiplier
                if adaptive_stop < position.stop_loss:
                    position.stop_loss = round(adaptive_stop, 1)

                if price >= position.stop_loss:
                    self._cooldown_remaining = params.cooldown_bars
                    return Signal(
                        direction=SignalDirection.CLOSE, strength=1.0,
                        stop_loss=0, take_profit=0,
                        reason=f"停損觸發 @ {price:.1f}（停損價 {position.stop_loss:.1f}）",
                        source=self.name,
                    )

        # ---- 第 1.5 層：保本停損 ----
        if position.side == Side.LONG:
            profit_pts = price - position.entry_price
        else:
            profit_pts = position.entry_price - price

        if profit_pts > position.max_unrealized_profit:
            position.max_unrealized_profit = profit_pts

        if profit_pts > atr * 1.5 and not position.breakeven_activated:
            position.breakeven_activated = True
            be_buffer = round(atr * 0.5, 1)
            if position.side == Side.LONG:
                be_stop = position.entry_price + be_buffer
                if be_stop > position.stop_loss:
                    position.stop_loss = round(be_stop, 1)
                    logger.info(f"[Gold 保本] LONG 停損移至 {position.stop_loss:.1f}（成本+{be_buffer}，獲利 {profit_pts:.1f} > {atr:.1f}）")
            else:
                be_stop = position.entry_price - be_buffer
                if be_stop < position.stop_loss:
                    position.stop_loss = round(be_stop, 1)
                    logger.info(f"[Gold 保本] SHORT 停損移至 {position.stop_loss:.1f}（成本-{be_buffer}，獲利 {profit_pts:.1f} > {atr:.1f}）")

        # ---- 第 2 層：Chandelier Exit ----
        chandelier_mult = params.trailing_distance

        if position.side == Side.LONG:
            profit = price - position.entry_price
            if profit > atr * params.trailing_trigger:
                if not position.trailing_activated:
                    position.trailing_activated = True
                    logger.info(f"[Gold Chandelier] LONG activated (profit {profit:.1f})")

                chandelier_stop = position.highest_since_entry - atr * chandelier_mult
                if chandelier_stop > position.trailing_stop:
                    position.trailing_stop = round(chandelier_stop, 1)

                if price <= position.trailing_stop:
                    return Signal(
                        direction=SignalDirection.CLOSE, strength=1.0,
                        stop_loss=0, take_profit=0,
                        reason=f"Chandelier停利 @ {price:.1f}（追蹤 {position.trailing_stop:.1f}）",
                        source=self.name,
                    )

        elif position.side == Side.SHORT:
            profit = position.entry_price - price
            if profit > atr * params.trailing_trigger:
                if not position.trailing_activated:
                    position.trailing_activated = True
                    logger.info(f"[Gold Chandelier] SHORT activated (profit {profit:.1f})")

                chandelier_stop = position.lowest_since_entry + atr * chandelier_mult
                if chandelier_stop < position.trailing_stop or position.trailing_stop == 0:
                    position.trailing_stop = round(chandelier_stop, 1)

                if price >= position.trailing_stop:
                    return Signal(
                        direction=SignalDirection.CLOSE, strength=1.0,
                        stop_loss=0, take_profit=0,
                        reason=f"Chandelier停利 @ {price:.1f}（追蹤 {position.trailing_stop:.1f}）",
                        source=self.name,
                    )

        # ---- 第 2.5 層：利潤回吐保護（最多回吐 65%）----
        if position.max_unrealized_profit > atr * 2.0:
            giveback = position.max_unrealized_profit - profit_pts
            max_giveback = position.max_unrealized_profit * 0.65
            if giveback > max_giveback:
                return Signal(
                    direction=SignalDirection.CLOSE, strength=1.0,
                    stop_loss=0, take_profit=0,
                    reason=f"利潤回吐保護 @ {price:.1f}（最高獲利 {position.max_unrealized_profit:.1f}，回吐 {giveback:.1f} > 65%）",
                    source=self.name,
                )

        # ---- 第 3 層：分段停利 ----
        if position.take_profit_levels:
            for tp_price, fraction in position.take_profit_levels:
                hit = (position.side == Side.LONG and price >= tp_price) or \
                      (position.side == Side.SHORT and price <= tp_price)
                if hit:
                    position.take_profit_levels = [
                        (p, f) for p, f in position.take_profit_levels if p != tp_price
                    ]
                    return Signal(
                        direction=SignalDirection.CLOSE, strength=fraction,
                        stop_loss=0, take_profit=0,
                        reason=f"分段停利 @ {price:.1f}（目標 {tp_price:.1f}，出 {fraction:.0%}）",
                        source=self.name,
                    )

        # ---- 第 4 層：時間停損 ----
        if position.bars_since_entry > params.time_stop_bars:
            if position.side == Side.LONG:
                profit = price - position.entry_price
            else:
                profit = position.entry_price - price

            if profit < atr * 0.5:
                return Signal(
                    direction=SignalDirection.CLOSE, strength=0.8,
                    stop_loss=0, take_profit=0,
                    reason=f"時間停損: {position.bars_since_entry}根K棒 獲利不足",
                    source=self.name,
                )

        # ---- 最終停利（兜底）----
        if position.take_profit > 0:
            if position.side == Side.LONG and price >= position.take_profit:
                return Signal(
                    direction=SignalDirection.CLOSE, strength=1.0,
                    stop_loss=0, take_profit=0,
                    reason=f"停利觸發 @ {price:.1f}",
                    source=self.name,
                )
            if position.side == Side.SHORT and price <= position.take_profit:
                return Signal(
                    direction=SignalDirection.CLOSE, strength=1.0,
                    stop_loss=0, take_profit=0,
                    reason=f"停利觸發 @ {price:.1f}",
                    source=self.name,
                )

        return None

    def get_parameters(self) -> dict:
        return {
            "strategy": self.name,
            "regime": self._last_regime.value,
            "signal_strength": round(self._last_signal_strength, 2),
            "cooldown_remaining": self._cooldown_remaining,
            "adaptive_params": self.signal_generator.params.to_dict(),
        }

    def reset(self):
        self.regime_classifier.reset()
        self._cooldown_remaining = 0
        self._last_regime = MarketRegime.RANGING
        self._last_signal_strength = 0.0
