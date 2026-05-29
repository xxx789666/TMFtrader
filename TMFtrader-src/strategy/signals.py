"""
TMFtrader 多因子訊號產生器
6 個因子加權評分，產生 0~1 的訊號強度
"""

from dataclasses import dataclass
from typing import Optional
import numpy as np

from core.market_data import MarketSnapshot
from strategy.base import Signal, SignalDirection
from strategy.filters import MarketRegime


@dataclass
class FactorScore:
    """單一因子的評分結果"""
    name: str
    score: float      # 0.0 ~ 1.0
    weight: float      # 權重
    detail: str = ""   # 說明


class AdaptiveParams:
    """自適應參數 — 根據近期波動率動態調整"""

    def __init__(self):
        self.stop_loss_multiplier = 2.5   # 優化後：2.0（原 2.5）
        self.min_signal_strength = 0.64   # 優化後：0.66（原 0.60），過濾弱訊號
        self.trailing_trigger = 1.25       # 優化後：1.0（原 2.0），更早啟動追蹤停利
        self.trailing_distance = 1.25      # 追蹤距離 1x ATR
        self.time_stop_bars = 60
        self.cooldown_bars = 3  # 停損後等 3 根 K 棒即可再進場
        # 滑價模型（根據波動率自動調整）
        self.slippage_ticks = 2  # 預設滑價點數

    def update(self, atr_ratio: float, regime: MarketRegime):
        """根據市場狀態調整參數（EMA 平滑，避免離散跳變）"""
        alpha = 0.3  # 平滑係數，越小越平滑

        # 計算目標值
        if regime in (MarketRegime.CRISIS_DOWN, MarketRegime.CRISIS_REVERSAL):
            target_sl = 2.5   # 優化後
            target_sig = 0.64   # 優化後
            target_trail = 1.25   # 優化後
            target_dist = 2.5
            target_time = 90
            target_cool = 5
            self.slippage_ticks = 5
        elif atr_ratio > 1.5:
            target_sl = 3.5
            target_sig = 0.65
            target_trail = 3.5
            target_dist = 2.0
            target_time = 60
            target_cool = 5
            self.slippage_ticks = 4
        elif atr_ratio < 0.7:
            target_sl = 2.0
            target_sig = 0.64
            target_trail = 1.0
            target_dist = 1.0
            target_time = 45
            target_cool = 3
            self.slippage_ticks = 1
        else:
            target_sl = 1.75   # 優化後
            target_sig = 0.66  # 優化後
            target_trail = 0.5  # 優化後
            target_dist = 1.0
            target_time = 60
            target_cool = 3
            self.slippage_ticks = 2

        # EMA 平滑過渡（不直接覆蓋，而是漸進趨近目標）
        self.stop_loss_multiplier = self.stop_loss_multiplier * (1 - alpha) + target_sl * alpha
        self.min_signal_strength = self.min_signal_strength * (1 - alpha) + target_sig * alpha
        self.trailing_trigger = self.trailing_trigger * (1 - alpha) + target_trail * alpha
        self.trailing_distance = self.trailing_distance * (1 - alpha) + target_dist * alpha
        self.time_stop_bars = round(self.time_stop_bars * (1 - alpha) + target_time * alpha)
        self.cooldown_bars = round(self.cooldown_bars * (1 - alpha) + target_cool * alpha)

        # 強趨勢降低門檻（趨勢明確時更積極進場）
        if regime in (MarketRegime.STRONG_TREND_UP, MarketRegime.STRONG_TREND_DOWN):
            self.min_signal_strength *= 0.85
            self.cooldown_bars = max(3, self.cooldown_bars)

    def apply_risk_profile(self, profile: str):
        """風險等級疊加修正（在 update 之後呼叫）"""
        if profile == "conservative":
            self.stop_loss_multiplier *= 0.8   # 更緊停損
            self.min_signal_strength *= 1.15   # 更嚴格進場門檻
            self.trailing_trigger *= 0.6       # 更早觸發移動停利（頂尖做法）
            self.trailing_distance *= 0.8      # 更緊追蹤距離
            self.cooldown_bars = max(self.cooldown_bars, 10)
        elif profile == "aggressive":
            self.stop_loss_multiplier *= 1.3   # 更寬停損
            self.min_signal_strength *= 0.85   # 更低門檻
            self.trailing_trigger *= 1.3       # 更晚觸發移動停利
            self.cooldown_bars = max(1, self.cooldown_bars // 2)
        elif profile == "crisis":
            self.stop_loss_multiplier *= 1.8   # 超寬停損
            self.min_signal_strength *= 0.70   # 大幅降低門檻
            self.trailing_trigger *= 2.0
            self.trailing_distance *= 1.5
            self.time_stop_bars = int(self.time_stop_bars * 1.5)
            self.cooldown_bars = max(self.cooldown_bars, 15)
        # balanced = 不修正，用 adaptive 的原始值

        # 參數邊界檢查（防止疊加後偏離合理範圍）
        self.stop_loss_multiplier = max(1.0, min(self.stop_loss_multiplier, 6.0))
        self.min_signal_strength = max(0.55, min(self.min_signal_strength, 0.90))
        self.trailing_trigger = max(0.5, min(self.trailing_trigger, 4.0))
        self.trailing_distance = max(0.5, min(self.trailing_distance, 4.0))
        self.time_stop_bars = max(10, min(self.time_stop_bars, 60))
        self.cooldown_bars = max(1, min(self.cooldown_bars, 20))

    def to_dict(self) -> dict:
        return {
            "stop_loss_multiplier": round(self.stop_loss_multiplier, 1),
            "min_signal_strength": round(self.min_signal_strength, 2),
            "trailing_trigger": round(self.trailing_trigger, 1),
            "trailing_distance": round(self.trailing_distance, 1),
            "time_stop_bars": self.time_stop_bars,
            "cooldown_bars": self.cooldown_bars,
        }


class MultiFactorSignalGenerator:
    """
    多因子訊號產生器（升級版 v2）

    7 個因子：
    1. 趨勢方向（25%）— 均線排列 + 價格相對位置
    2. RSI 動量（18%）— RSI 值 + RSI 趨勢
    3. 突破確認（12%）— 價格突破近期高/低點
    4. 成交量確認（12%）— 量能放大
    5. ADX 趨勢強度（8%）— ADX 值
    6. 波動率環境（10%）— ATR 比率在合理範圍
    7. 多時框確認（15%）— 5 分 / 15 分 K 趨勢一致性
    """

    DEFAULT_WEIGHTS = {
        "trend": 0.0395,
        "rsi": 0.4367,
        "breakout": 0.0087,
        "volume": 0.0276,
        "adx": 0.14,
        "volatility": 0.1724,
        "mtf": 0.016,
        "candle": 0.1592,  # K 線型態（新增）
    }

    def __init__(self, weights: dict = None):
        self.weights = weights or self.DEFAULT_WEIGHTS.copy()
        self.params = AdaptiveParams()
        self.risk_profile = "balanced"

    def generate(
        self,
        snapshot: MarketSnapshot,
        regime: MarketRegime,
        snapshot_5m: MarketSnapshot = None,
        snapshot_15m: MarketSnapshot = None,
    ) -> Optional[Signal]:
        """
        產生交易訊號（升級版 v2）
        - 多時框確認（5m/15m 趨勢一致性）
        - 滑價緩衝（根據波動率自動調整）
        - 分段停利（2x/3x/4x ATR 各出 1/3）
        """
        # 更新自適應參數 + 風險等級修正
        self.params.update(snapshot.atr_ratio, regime)
        self.params.apply_risk_profile(self.risk_profile)

        # 不在趨勢狀態 → 不產生動量訊號
        if regime in (MarketRegime.RANGING, MarketRegime.VOLATILE):
            return None

        # 判斷方向
        is_bullish = regime in (MarketRegime.STRONG_TREND_UP, MarketRegime.WEAK_TREND_UP)

        # 計算各因子分數（含多時框）
        factors = self._score_factors(snapshot, is_bullish, snapshot_5m, snapshot_15m)

        # 加權總分
        total_score = sum(f.score * f.weight for f in factors)
        total_score = min(max(total_score, 0.0), 1.0)

        # 未達門檻 → 不進場
        if total_score < self.params.min_signal_strength:
            return None

        # 計算停損停利（含滑價緩衝）
        direction = SignalDirection.BUY if is_bullish else SignalDirection.SELL
        atr = snapshot.atr if snapshot.atr > 0 else 50.0
        slippage = self.params.slippage_ticks
        sl_mult = self.params.stop_loss_multiplier

        # 分段停利倍數：拉大盈虧比，讓利潤跑
        tp1 = max(sl_mult * 1.5, 4.0)   # 第一段至少 4x ATR
        tp2 = tp1 + sl_mult * 1.5       # 第二段
        tp3 = tp2 + sl_mult * 1.5       # 第三段

        if is_bullish:
            stop_loss = snapshot.price - atr * sl_mult - slippage
            tp_levels = [
                (round(snapshot.price + atr * tp1), 0.33),
                (round(snapshot.price + atr * tp2), 0.33),
                (round(snapshot.price + atr * tp3), 0.34),
            ]
            take_profit = round(snapshot.price + atr * tp3)
        else:
            stop_loss = snapshot.price + atr * sl_mult + slippage
            tp_levels = [
                (round(snapshot.price - atr * tp1), 0.33),
                (round(snapshot.price - atr * tp2), 0.33),
                (round(snapshot.price - atr * tp3), 0.34),
            ]
            take_profit = round(snapshot.price - atr * tp3)

        # 組裝理由
        top_factors = sorted(factors, key=lambda f: f.score * f.weight, reverse=True)[:3]
        reason_parts = [f.detail for f in top_factors if f.score > 0.3]
        reason = " + ".join(reason_parts) if reason_parts else "多因子綜合訊號"

        return Signal(
            direction=direction,
            strength=total_score,
            stop_loss=round(stop_loss),
            take_profit=take_profit,
            reason=reason,
            source="AdaptiveMomentum",
            take_profit_levels=tp_levels,
            slippage_buffer=slippage,
        )

    def _score_factors(self, snap: MarketSnapshot, is_bullish: bool,
                       snap_5m: MarketSnapshot = None,
                       snap_15m: MarketSnapshot = None) -> list[FactorScore]:
        """計算 7 個因子的分數"""
        return [
            self._score_trend(snap, is_bullish),
            self._score_rsi(snap, is_bullish),
            self._score_breakout(snap, is_bullish),
            self._score_volume(snap),
            self._score_adx(snap),
            self._score_volatility(snap),
            self._score_mtf(snap, is_bullish, snap_5m, snap_15m),
            self._score_candle(snap, is_bullish),
        ]

    def _score_trend(self, snap: MarketSnapshot, is_bullish: bool) -> FactorScore:
        """因子 1：趨勢方向（均線排列 + 價格位置）"""
        score = 0.0
        details = []

        if is_bullish:
            # 做多：價格在均線之上
            if snap.price > snap.ema20:
                score += 0.4
                details.append("價格>EMA20")
            if snap.ema5 > snap.ema10:
                score += 0.2
            if snap.ema10 > snap.ema20:
                score += 0.2
            if snap.ema20 > snap.ema60:
                score += 0.2
                details.append("多頭排列")
        else:
            # 做空：價格在均線之下
            if snap.price < snap.ema20:
                score += 0.4
                details.append("價格<EMA20")
            if snap.ema5 < snap.ema10:
                score += 0.2
            if snap.ema10 < snap.ema20:
                score += 0.2
            if snap.ema20 < snap.ema60:
                score += 0.2
                details.append("空頭排列")

        return FactorScore(
            name="trend",
            score=min(score, 1.0),
            weight=self.weights["trend"],
            detail=" ".join(details) if details else "趨勢中性",
        )

    def _score_rsi(self, snap: MarketSnapshot, is_bullish: bool) -> FactorScore:
        """因子 2：RSI 動量（收緊版 — 左側交易偏好極端值）"""
        rsi = snap.rsi
        score = 0.0
        detail = f"RSI={rsi:.0f}"

        if is_bullish:
            # 做多：RSI < 35 最佳（超賣區抄底），35~50 次佳
            if rsi < 35:
                score = 0.8 + (35 - rsi) / 35 * 0.2  # 越低越高分
                detail += " 超賣抄底"
            elif 35 <= rsi < 50:
                score = 0.4
            elif 50 <= rsi < 65:
                score = 0.2  # 已不算便宜
            else:
                score = 0.0  # RSI > 65 做多風險太高
                detail += " 超買不追"
            if snap.rsi_ma5 > snap.rsi_ma10:
                score += 0.15
                detail += " 動量上升"
        else:
            # 做空：RSI > 70 最佳（超買區放空），50~70 次佳
            if rsi > 70:
                score = 0.8 + (rsi - 70) / 30 * 0.2
                detail += " 超買放空"
            elif 50 < rsi <= 70:
                score = 0.4
            elif 35 < rsi <= 50:
                score = 0.2
            else:
                score = 0.0  # RSI < 35 做空風險太高
                detail += " 超賣不追空"
            if snap.rsi_ma5 < snap.rsi_ma10:
                score += 0.15
                detail += " 動量下降"

        return FactorScore(
            name="rsi",
            score=min(score, 1.0),
            weight=self.weights["rsi"],
            detail=detail,
        )

    def _score_breakout(self, snap: MarketSnapshot, is_bullish: bool) -> FactorScore:
        """因子 3：突破確認"""
        score = 0.0
        atr = snap.atr if snap.atr > 0 else 1.0

        if is_bullish:
            # 做多：價格突破近期高點
            if snap.price > snap.recent_high:
                breakout_strength = (snap.price - snap.recent_high) / atr
                score = min(breakout_strength + 0.3, 1.0)
                detail = f"突破高點{snap.recent_high:.0f}"
            elif snap.price > snap.recent_high - atr * 0.3:
                score = 0.4
                detail = "接近高點"
            else:
                detail = "未突破"
        else:
            # 做空：價格跌破近期低點
            if snap.price < snap.recent_low:
                breakout_strength = (snap.recent_low - snap.price) / atr
                score = min(breakout_strength + 0.3, 1.0)
                detail = f"跌破低點{snap.recent_low:.0f}"
            elif snap.price < snap.recent_low + atr * 0.3:
                score = 0.4
                detail = "接近低點"
            else:
                detail = "未跌破"

        return FactorScore(
            name="breakout",
            score=min(score, 1.0),
            weight=self.weights["breakout"],
            detail=detail,
        )

    def _score_volume(self, snap: MarketSnapshot) -> FactorScore:
        """因子 4：成交量確認（提高門檻 — 量萎縮直接 0 分）"""
        ratio = snap.volume_ratio
        if ratio >= 2.0:
            score = 1.0
            detail = f"量能爆發({ratio:.1f}x)"
        elif ratio >= 1.5:
            score = 0.7 + (ratio - 1.5) / 1.0  # 1.5→0.7, 2.0→1.2→cap1.0
            detail = f"量能放大({ratio:.1f}x)"
        elif ratio >= 1.0:
            score = 0.3
            detail = f"量能普通({ratio:.1f}x)"
        else:
            score = 0.0
            detail = "量縮不進場"

        return FactorScore(
            name="volume",
            score=min(score, 1.0),
            weight=self.weights["volume"],
            detail=detail,
        )

    def _score_adx(self, snap: MarketSnapshot) -> FactorScore:
        """因子 5：ADX 趨勢強度"""
        adx = snap.adx
        if adx >= 40:
            score = 1.0
            detail = f"ADX={adx:.0f} 強趨勢"
        elif adx >= 25:
            score = 0.5 + (adx - 25) / 30
            detail = f"ADX={adx:.0f} 有趨勢"
        elif adx >= 20:
            score = 0.3
            detail = f"ADX={adx:.0f} 弱趨勢"
        else:
            score = 0.1
            detail = f"ADX={adx:.0f} 無趨勢"

        return FactorScore(
            name="adx",
            score=min(score, 1.0),
            weight=self.weights["adx"],
            detail=detail,
        )

    def _score_volatility(self, snap: MarketSnapshot) -> FactorScore:
        """因子 6：波動率環境"""
        ratio = snap.atr_ratio
        if 0.8 <= ratio <= 1.3:
            score = 1.0
            detail = "波動率適中"
        elif 0.5 <= ratio < 0.8:
            score = 0.6
            detail = "波動率偏低"
        elif 1.3 < ratio <= 1.8:
            score = 0.4
            detail = "波動率偏高"
        else:
            score = 0.1
            detail = "波動率異常"

        return FactorScore(
            name="volatility",
            score=min(score, 1.0),
            weight=self.weights["volatility"],
            detail=detail,
        )

    def _score_mtf(self, snap: MarketSnapshot, is_bullish: bool,
                   snap_5m: MarketSnapshot = None,
                   snap_15m: MarketSnapshot = None) -> FactorScore:
        """因子 7：多時框趨勢確認（5 分 K + 15 分 K）"""
        score = 0.0
        details = []

        has_5m = snap_5m and snap_5m.ema5 > 0 and snap_5m.ema20 > 0
        has_15m = snap_15m and snap_15m.ema5 > 0 and snap_15m.ema20 > 0

        if has_5m:
            if is_bullish and snap_5m.ema5 > snap_5m.ema20:
                score += 0.4
                details.append("5m多")
            elif not is_bullish and snap_5m.ema5 < snap_5m.ema20:
                score += 0.4
                details.append("5m空")
            else:
                details.append("5m逆")

        if has_15m:
            if is_bullish and snap_15m.ema5 > snap_15m.ema20:
                score += 0.6
                details.append("15m多")
            elif not is_bullish and snap_15m.ema5 < snap_15m.ema20:
                score += 0.6
                details.append("15m空")
            else:
                details.append("15m逆")

        # 沒有 MTF 數據時，給中性分（不懲罰）
        if not has_5m and not has_15m:
            score = 0.5
            details.append("MTF待機")

        return FactorScore(
            name="mtf",
            score=min(score, 1.0),
            weight=self.weights["mtf"],
            detail=" ".join(details) if details else "MTF中性",
        )

    def _score_candle(self, snap: MarketSnapshot, is_bullish: bool) -> FactorScore:
        """因子 8：K 線型態（左側交易關鍵確認）"""
        score = 0.0
        details = []

        if is_bullish:
            # 做多：長下影紅K、多頭吞噬
            if snap.candle_long_lower and snap.candle_is_bullish:
                score += 0.6
                details.append("長下影紅K")
            elif snap.candle_long_lower:
                score += 0.3
                details.append("長下影")
            if snap.candle_engulfing == 1:
                score += 0.4
                details.append("多頭吞噬")
            # 放量確認加分
            if snap.volume_spike and score > 0:
                score += 0.2
                details.append("放量確認")
        else:
            # 做空：長上影黑K、空頭吞噬
            if snap.candle_long_upper and not snap.candle_is_bullish:
                score += 0.6
                details.append("長上影黑K")
            elif snap.candle_long_upper:
                score += 0.3
                details.append("長上影")
            if snap.candle_engulfing == -1:
                score += 0.4
                details.append("空頭吞噬")
            if snap.volume_spike and score > 0:
                score += 0.2
                details.append("放量確認")

        return FactorScore(
            name="candle",
            score=min(score, 1.0),
            weight=self.weights["candle"],
            detail=" ".join(details) if details else "型態中性",
        )

    def get_last_factors(self, snapshot: MarketSnapshot, is_bullish: bool) -> list[dict]:
        """取得因子評分明細（供 Dashboard 顯示）"""
        factors = self._score_factors(snapshot, is_bullish)
        return [
            {
                "name": f.name,
                "score": round(f.score, 2),
                "weight": f.weight,
                "weighted": round(f.score * f.weight, 3),
                "detail": f.detail,
            }
            for f in factors
        ]
