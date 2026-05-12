"""
UltraTrader 市場狀態分類器 + 盤別管理器
在做任何交易決策前，先判斷市場處於什麼狀態
"""

from collections import deque
from datetime import datetime, time as dtime
from enum import Enum
from typing import Optional

from core.market_data import MarketSnapshot


# ============================================================
# 盤別管理器 — 控制交易時段 + 跳空保護
# ============================================================

class SessionPhase(Enum):
    """交易時段階段"""
    PRE_OPEN = "pre_open"           # 開盤前
    OPEN_VOLATILE = "open_volatile" # 開盤前 15 分鐘（波動大，提高門檻）
    NORMAL = "normal"               # 正常交易
    LAST_30 = "last_30"             # 收盤前 30 分鐘（不開新倉）
    CLOSING = "closing"             # 收盤前 5 分鐘（強制平倉）
    CLOSED = "closed"               # 非交易時段


class SessionManager:
    """
    台灣期貨盤別管理（支援不同商品交易時段）

    TMF 台指期：日盤 08:45~13:45 / 夜盤 15:00~05:00
    TGF 黃金期：日盤 08:45~16:15 / 夜盤 17:25~05:00

    功能：
    1. 判斷當前盤別階段（開盤波動 / 正常 / 收盤前）
    2. 提供停損倍數修正（開盤放寬、收盤收緊）
    3. 計算距離收盤剩餘時間
    """

    # 各商品的交易時段設定
    SESSIONS = {
        "default": {
            "day_start": dtime(8, 45),
            "day_open_end": dtime(9, 0),
            "day_last30": dtime(13, 15),
            "day_closing": dtime(13, 40),
            "day_end": dtime(13, 45),
            "night_start": dtime(15, 0),
            "night_open_end": dtime(15, 15),
            "night_last30": dtime(4, 30),
            "night_closing": dtime(4, 55),
            "night_end": dtime(5, 0),
        },
        "TGF": {
            "day_start": dtime(8, 45),
            "day_open_end": dtime(9, 0),
            "day_last30": dtime(15, 45),
            "day_closing": dtime(16, 10),
            "day_end": dtime(16, 15),
            "night_start": dtime(17, 25),
            "night_open_end": dtime(17, 40),
            "night_last30": dtime(4, 30),
            "night_closing": dtime(4, 55),
            "night_end": dtime(5, 0),
        },
    }

    def __init__(self, instrument: str = ""):
        """instrument: 商品代碼（TGF, TMF 等），決定使用哪組交易時段"""
        key = instrument.upper() if instrument.upper() in self.SESSIONS else "default"
        s = self.SESSIONS[key]
        self.DAY_START = s["day_start"]
        self.DAY_OPEN_END = s["day_open_end"]
        self.DAY_LAST30 = s["day_last30"]
        self.DAY_CLOSING = s["day_closing"]
        self.DAY_END = s["day_end"]
        self.NIGHT_START = s["night_start"]
        self.NIGHT_OPEN_END = s["night_open_end"]
        self.NIGHT_LAST30 = s["night_last30"]
        self.NIGHT_CLOSING = s["night_closing"]
        self.NIGHT_END = s["night_end"]

    def get_phase(self, now: datetime = None) -> SessionPhase:
        """判斷當前交易時段階段"""
        t = (now or datetime.now()).time()

        # 日盤
        if self.DAY_START <= t < self.DAY_OPEN_END:
            return SessionPhase.OPEN_VOLATILE
        if self.DAY_OPEN_END <= t < self.DAY_LAST30:
            return SessionPhase.NORMAL
        if self.DAY_LAST30 <= t < self.DAY_CLOSING:
            return SessionPhase.LAST_30
        if self.DAY_CLOSING <= t <= self.DAY_END:
            return SessionPhase.CLOSING

        # 夜盤
        if self.NIGHT_START <= t < self.NIGHT_OPEN_END:
            return SessionPhase.OPEN_VOLATILE
        if self.NIGHT_OPEN_END <= t <= dtime(23, 59, 59, 999999):
            return SessionPhase.NORMAL
        if dtime(0, 0) <= t < self.NIGHT_LAST30:
            return SessionPhase.NORMAL
        if self.NIGHT_LAST30 <= t < self.NIGHT_CLOSING:
            return SessionPhase.LAST_30
        if self.NIGHT_CLOSING <= t <= self.NIGHT_END:
            return SessionPhase.CLOSING

        return SessionPhase.CLOSED

    def get_stop_multiplier(self, phase: SessionPhase = None) -> float:
        """根據盤別階段返回停損倍數修正"""
        phase = phase or self.get_phase()
        return {
            SessionPhase.OPEN_VOLATILE: 1.5,   # 開盤波動大，放寬 50%
            SessionPhase.NORMAL: 1.0,
            SessionPhase.LAST_30: 0.8,          # 收盤前收緊
            SessionPhase.CLOSING: 0.5,          # 即將收盤，極窄停損
            SessionPhase.CLOSED: 1.0,
            SessionPhase.PRE_OPEN: 1.0,
        }.get(phase, 1.0)

    def minutes_to_close(self, now: datetime = None) -> int:
        """計算距離當前時段收盤的剩餘分鐘數"""
        t = (now or datetime.now()).time()

        def _diff_minutes(t1, t2):
            return (t2.hour * 60 + t2.minute) - (t1.hour * 60 + t1.minute)

        # 日盤
        if self.DAY_START <= t <= self.DAY_END:
            return max(0, _diff_minutes(t, self.DAY_END))

        # 夜盤
        if t >= self.NIGHT_START:
            # 距離隔日 05:00 = (24:00 - now) + 5:00
            mins_to_midnight = (24 * 60) - (t.hour * 60 + t.minute)
            return mins_to_midnight + 5 * 60
        if t <= self.NIGHT_END:
            return max(0, _diff_minutes(t, self.NIGHT_END))

        return 0  # 非交易時段

    def is_in_session(self, now: datetime = None) -> bool:
        """是否在交易時段內"""
        return self.get_phase(now) not in (SessionPhase.CLOSED, SessionPhase.PRE_OPEN)


class MarketRegime(Enum):
    STRONG_TREND_UP = "強勢上漲"       # → 積極做多
    WEAK_TREND_UP = "溫和上漲"         # → 保守做多
    RANGING = "盤整"                   # → 均值回歸或不交易
    WEAK_TREND_DOWN = "溫和下跌"       # → 保守做空
    STRONG_TREND_DOWN = "強勢下跌"     # → 積極做空
    VOLATILE = "劇烈波動"              # → 不交易，等待
    CRISIS_DOWN = "危機崩跌"           # → 跟勢做空，等待左側反轉
    CRISIS_REVERSAL = "危機反轉"       # → 左側做多（恐慌到頂）


# 各狀態的 emoji 和顏色
REGIME_META = {
    MarketRegime.STRONG_TREND_UP:   {"emoji": "🟢", "color": "#10B981"},
    MarketRegime.WEAK_TREND_UP:     {"emoji": "🟡", "color": "#F59E0B"},
    MarketRegime.RANGING:           {"emoji": "⚪", "color": "#6B7280"},
    MarketRegime.WEAK_TREND_DOWN:   {"emoji": "🟠", "color": "#F97316"},
    MarketRegime.STRONG_TREND_DOWN: {"emoji": "🔴", "color": "#EF4444"},
    MarketRegime.VOLATILE:          {"emoji": "⚡", "color": "#8B5CF6"},
    MarketRegime.CRISIS_DOWN:       {"emoji": "💀", "color": "#DC2626"},
    MarketRegime.CRISIS_REVERSAL:   {"emoji": "🔄", "color": "#06B6D4"},
}


class MarketRegimeClassifier:
    """
    多因子市場狀態分類器

    判斷方法（4 個因子 + Intelligence 危機偵測）：
    1. ADX（趨勢強度）
    2. 均線排列（EMA5/10/20/60）
    3. 波動率比（ATR ratio）
    4. 方向性指標（DI+/DI-）
    5. Intelligence 數據（VIX / P/C Ratio / 外資 → 危機模式偵測）

    內建遲滯（hysteresis）：需連續 2 根 K 棒確認才切換狀態
    """

    def __init__(self):
        self._current_regime = MarketRegime.RANGING
        self._pending_regime: Optional[MarketRegime] = None
        self._pending_count = 0
        self._hysteresis = 1  # 需要連續 N 根 K 棒確認（快速切換到盤整）

        # Intelligence 數據（由引擎注入）
        self._vix: float = 0
        self._pc_ratio: float = 0
        self._left_side_score: float = 0
        self._left_side_signal: str = "neutral"
        self._foreign_spot: float = 0  # 億元

        # 快速危機偵測：追蹤近期 VIX 和 ATR 變化率
        self._vix_history: deque = deque(maxlen=10)
        self._atr_history: deque = deque(maxlen=20)

    def update_intelligence(self, vix: float = 0, pc_ratio: float = 0,
                           left_side_score: float = 0, left_side_signal: str = "neutral",
                           foreign_spot: float = 0):
        """更新 Intelligence 數據"""
        self._vix = vix
        self._pc_ratio = pc_ratio
        self._left_side_score = left_side_score
        self._left_side_signal = left_side_signal
        self._foreign_spot = foreign_spot
        # 記錄 VIX 歷史，用於計算變化率
        if vix > 0:
            self._vix_history.append(vix)

    def _update_atr_history(self, atr_ratio: float):
        """記錄 ATR ratio 歷史"""
        if atr_ratio > 0:
            self._atr_history.append(atr_ratio)

    def _is_crisis(self) -> bool:
        """
        判斷是否處於危機狀態（升級版 v2 — 修正誤觸發）

        危機 = 市場真正恐慌，不是普通的利空日
        門檻調高，避免正常波動被誤判為危機

        5 個因子，需要至少 3 個觸發（原 2 個太敏感）：
        1. VIX > 30（原 28 太低，27~29 是常態高波動）
        2. VIX 變化率 > 30%（原 25% 太敏感）
        3. ATR ratio 急升 2x+（原 1.8x 太敏感）
        4. P/C Ratio > 1.5（原 1.3 太低，1.3~1.5 在台灣很常見）
        5. 外資現貨大幅賣超 > 300 億（原 150 億太低）

        VIX > 40 直接判定（原 35，改為只有真正的黑天鵝才直接觸發）
        """
        crisis_signals = 0

        # ---- VIX 變化率偵測 ----
        if len(self._vix_history) >= 3:
            oldest = self._vix_history[0]
            if oldest > 0:
                vix_roc = (self._vix - oldest) / oldest
                if vix_roc > 0.30:  # VIX 急升 30%+
                    crisis_signals += 1

        # ---- ATR ratio 急升偵測 ----
        if len(self._atr_history) >= 5:
            recent_avg = sum(list(self._atr_history)[-3:]) / 3
            older_avg = sum(list(self._atr_history)[:3]) / 3
            if older_avg > 0 and recent_avg / older_avg > 2.0:
                crisis_signals += 1  # 波動率翻倍

        # VIX > 30 = 恐慌
        if self._vix > 30:
            crisis_signals += 1
        # VIX > 40 = 極度恐慌（黑天鵝），直接判定
        if self._vix > 40:
            return True

        # P/C Ratio > 1.5 = 散戶大量買 Put（台灣 1.3~1.5 屬常態偏空）
        if self._pc_ratio > 1.5:
            crisis_signals += 1

        # 外資現貨大幅賣超 > 300 億
        if self._foreign_spot < -300:
            crisis_signals += 1

        # 5 個因子中至少 3 個觸發才算危機（防止誤觸發）
        return crisis_signals >= 3

    def _is_crisis_reversal(self) -> bool:
        """判斷危機是否到達反轉點（恐慌到頂）"""
        reversal_signals = 0

        # 左側評分 > 0.5 = 強烈左側做多訊號
        if self._left_side_score > 0.5:
            reversal_signals += 1

        # P/C Ratio > 2.0 = 歷史級恐慌（底部訊號）
        if self._pc_ratio > 2.0:
            reversal_signals += 1

        # VIX > 35 但開始下降（需要歷史數據，暫用絕對值）
        if self._vix > 35:
            reversal_signals += 1

        # 左側訊號為 strong_buy
        if self._left_side_signal == "strong_buy":
            reversal_signals += 1

        return reversal_signals >= 2

    def classify(self, snapshot: MarketSnapshot) -> MarketRegime:
        """分類當前市場狀態"""
        # 追蹤 ATR ratio 用於快速危機偵測
        self._update_atr_history(snapshot.atr_ratio)

        # 最高優先：危機模式偵測（基於 Intelligence 數據）
        if self._is_crisis():
            if self._is_crisis_reversal():
                raw = MarketRegime.CRISIS_REVERSAL
                # 危機反轉不需要遲滯，立即切換
                self._current_regime = raw
                self._pending_regime = None
                self._pending_count = 0
                return raw
            else:
                raw = MarketRegime.CRISIS_DOWN
                # 危機崩跌也不需要遲滯
                self._current_regime = raw
                self._pending_regime = None
                self._pending_count = 0
                return raw

        # 因子 1：波動率過濾
        if snapshot.atr_ratio > 1.8:
            raw = MarketRegime.VOLATILE
            return self._apply_hysteresis(raw)

        # 因子 2：ADX 趨勢強度
        has_trend = snapshot.adx > 26
        strong_trend = snapshot.adx > 38

        # 因子 3：均線排列方向
        bullish_ema = (snapshot.ema5 > snapshot.ema20 and snapshot.ema20 > snapshot.ema60)
        bearish_ema = (snapshot.ema5 < snapshot.ema20 and snapshot.ema20 < snapshot.ema60)

        # 因子 4：DI 方向
        bullish_di = snapshot.plus_di > snapshot.minus_di
        bearish_di = snapshot.minus_di > snapshot.plus_di

        # 綜合判斷
        if has_trend:
            if bullish_ema or bullish_di:
                raw = MarketRegime.STRONG_TREND_UP if strong_trend else MarketRegime.WEAK_TREND_UP
            elif bearish_ema or bearish_di:
                raw = MarketRegime.STRONG_TREND_DOWN if strong_trend else MarketRegime.WEAK_TREND_DOWN
            else:
                raw = MarketRegime.RANGING
        else:
            # ADX 低 → 盤整
            raw = MarketRegime.RANGING

        return self._apply_hysteresis(raw)

    def _apply_hysteresis(self, raw: MarketRegime) -> MarketRegime:
        """遲滯處理：避免狀態頻繁切換"""
        if raw == self._current_regime:
            self._pending_regime = None
            self._pending_count = 0
            return self._current_regime

        if raw == self._pending_regime:
            self._pending_count += 1
            if self._pending_count >= self._hysteresis:
                self._current_regime = raw
                self._pending_regime = None
                self._pending_count = 0
        else:
            self._pending_regime = raw
            self._pending_count = 1

        return self._current_regime

    def get_regime(self) -> MarketRegime:
        """取得當前狀態"""
        return self._current_regime

    def get_regime_info(self) -> dict:
        """取得狀態資訊（供 Dashboard 顯示）"""
        regime = self._current_regime
        meta = REGIME_META[regime]
        return {
            "regime": regime.value,
            "regime_key": regime.name,
            "emoji": meta["emoji"],
            "color": meta["color"],
        }

    def reset(self):
        """重置狀態"""
        self._current_regime = MarketRegime.RANGING
        self._pending_regime = None
        self._pending_count = 0
