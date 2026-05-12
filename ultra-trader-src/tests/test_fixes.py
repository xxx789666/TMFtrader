"""
UltraTrader 修復驗證測試
驗證所有 16 項修復的正確性，確保上線前無遺漏

測試涵蓋：
1. SessionManager 多商品交易時段
2. 危機偵測門檻（防誤觸發）
3. K 棒時間戳（fromtimestamp vs utcfromtimestamp）
4. NaN/Inf 清理（Dashboard JSON 安全）
5. 黃金策略（GoldAdaptiveParams + 危機做多）
6. 跨日 K 棒查詢
7. _safe_round 工具函數
"""

import sys
import math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import unittest
from datetime import datetime, time as dtime, timedelta

from strategy.filters import SessionManager, SessionPhase, MarketRegime, MarketRegimeClassifier
from strategy.gold_trend import GoldAdaptiveParams, GoldTrendStrategy
from strategy.base import Signal, SignalDirection
from core.market_data import MarketSnapshot
from core.engine import _safe_round
from dashboard.app import _sanitize_for_json
from dashboard.websocket import _sanitize_floats


# ============================================================
# 1. SessionManager 多商品交易時段
# ============================================================

class TestSessionManagerTGF(unittest.TestCase):
    """測試 TGF（黃金期貨）交易時段"""

    def setUp(self):
        self.sm = SessionManager("TGF")

    # -- 日盤 08:45 ~ 16:15 --

    def test_tgf_day_open(self):
        """TGF 日盤開盤 08:45 → OPEN_VOLATILE"""
        t = datetime(2026, 3, 14, 8, 45)
        self.assertEqual(self.sm.get_phase(t), SessionPhase.OPEN_VOLATILE)

    def test_tgf_day_normal(self):
        """TGF 日盤 10:00 → NORMAL"""
        t = datetime(2026, 3, 14, 10, 0)
        self.assertEqual(self.sm.get_phase(t), SessionPhase.NORMAL)

    def test_tgf_day_still_normal_at_14(self):
        """TGF 14:00 仍為 NORMAL（TMF 已收盤，但 TGF 日盤到 16:15）"""
        t = datetime(2026, 3, 14, 14, 0)
        self.assertEqual(self.sm.get_phase(t), SessionPhase.NORMAL)

    def test_tgf_day_last30(self):
        """TGF 日盤 15:45 → LAST_30"""
        t = datetime(2026, 3, 14, 15, 50)
        self.assertEqual(self.sm.get_phase(t), SessionPhase.LAST_30)

    def test_tgf_day_closing(self):
        """TGF 日盤 16:10 → CLOSING"""
        t = datetime(2026, 3, 14, 16, 10)
        self.assertEqual(self.sm.get_phase(t), SessionPhase.CLOSING)

    def test_tgf_day_end(self):
        """TGF 日盤 16:15 → CLOSING（含）"""
        t = datetime(2026, 3, 14, 16, 15)
        self.assertEqual(self.sm.get_phase(t), SessionPhase.CLOSING)

    # -- 日盤結束到夜盤開始的空檔 --

    def test_tgf_between_sessions(self):
        """TGF 16:30 → CLOSED（日盤結束、夜盤未開）"""
        t = datetime(2026, 3, 14, 16, 30)
        self.assertEqual(self.sm.get_phase(t), SessionPhase.CLOSED)

    def test_tgf_before_night(self):
        """TGF 17:00 → CLOSED（夜盤 17:25 才開）"""
        t = datetime(2026, 3, 14, 17, 0)
        self.assertEqual(self.sm.get_phase(t), SessionPhase.CLOSED)

    # -- 夜盤 17:25 ~ 05:00 --

    def test_tgf_night_open(self):
        """TGF 夜盤 17:25 → OPEN_VOLATILE"""
        t = datetime(2026, 3, 14, 17, 25)
        self.assertEqual(self.sm.get_phase(t), SessionPhase.OPEN_VOLATILE)

    def test_tgf_night_normal(self):
        """TGF 夜盤 18:00 → NORMAL"""
        t = datetime(2026, 3, 14, 18, 0)
        self.assertEqual(self.sm.get_phase(t), SessionPhase.NORMAL)

    def test_tgf_night_midnight(self):
        """TGF 凌晨 00:30 → NORMAL（夜盤持續到 05:00）"""
        t = datetime(2026, 3, 15, 0, 30)
        self.assertEqual(self.sm.get_phase(t), SessionPhase.NORMAL)

    def test_tgf_night_last30(self):
        """TGF 夜盤 04:35 → LAST_30"""
        t = datetime(2026, 3, 15, 4, 35)
        self.assertEqual(self.sm.get_phase(t), SessionPhase.LAST_30)

    def test_tgf_night_closing(self):
        """TGF 夜盤 04:55 → CLOSING"""
        t = datetime(2026, 3, 15, 4, 55)
        self.assertEqual(self.sm.get_phase(t), SessionPhase.CLOSING)

    def test_tgf_after_night(self):
        """TGF 凌晨 05:30 → CLOSED"""
        t = datetime(2026, 3, 15, 5, 30)
        self.assertEqual(self.sm.get_phase(t), SessionPhase.CLOSED)

    def test_tgf_is_in_session(self):
        """TGF is_in_session 在各時段"""
        self.assertTrue(self.sm.is_in_session(datetime(2026, 3, 14, 10, 0)))   # 日盤
        self.assertTrue(self.sm.is_in_session(datetime(2026, 3, 14, 18, 0)))   # 夜盤
        self.assertTrue(self.sm.is_in_session(datetime(2026, 3, 15, 2, 0)))    # 凌晨
        self.assertFalse(self.sm.is_in_session(datetime(2026, 3, 14, 7, 0)))   # 開盤前
        self.assertFalse(self.sm.is_in_session(datetime(2026, 3, 14, 17, 0)))  # 空檔
        self.assertFalse(self.sm.is_in_session(datetime(2026, 3, 15, 6, 0)))   # 夜盤後


class TestSessionManagerTMF(unittest.TestCase):
    """測試 TMF（台指期）交易時段"""

    def setUp(self):
        self.sm = SessionManager("TMF")  # → default

    def test_tmf_day_end_at_1345(self):
        """TMF 日盤 13:45 → CLOSING"""
        t = datetime(2026, 3, 14, 13, 45)
        self.assertEqual(self.sm.get_phase(t), SessionPhase.CLOSING)

    def test_tmf_closed_at_14(self):
        """TMF 14:00 → CLOSED（日盤已結束）"""
        t = datetime(2026, 3, 14, 14, 0)
        self.assertEqual(self.sm.get_phase(t), SessionPhase.CLOSED)

    def test_tmf_night_opens_1500(self):
        """TMF 夜盤 15:00 → OPEN_VOLATILE"""
        t = datetime(2026, 3, 14, 15, 0)
        self.assertEqual(self.sm.get_phase(t), SessionPhase.OPEN_VOLATILE)

    def test_tmf_midnight(self):
        """TMF 凌晨 01:00 → NORMAL"""
        t = datetime(2026, 3, 15, 1, 0)
        self.assertEqual(self.sm.get_phase(t), SessionPhase.NORMAL)


class TestSessionManagerInstrumentMapping(unittest.TestCase):
    """測試 SessionManager 商品對應"""

    def test_tgf_uses_gold_sessions(self):
        """TGF 使用黃金時段"""
        sm = SessionManager("TGF")
        self.assertEqual(sm.DAY_END, dtime(16, 15))
        self.assertEqual(sm.NIGHT_START, dtime(17, 25))

    def test_tmf_uses_default_sessions(self):
        """TMF 使用預設時段（台指期）"""
        sm = SessionManager("TMF")
        self.assertEqual(sm.DAY_END, dtime(13, 45))
        self.assertEqual(sm.NIGHT_START, dtime(15, 0))

    def test_unknown_uses_default(self):
        """未知商品使用預設時段"""
        sm = SessionManager("XYZ")
        self.assertEqual(sm.DAY_END, dtime(13, 45))

    def test_case_insensitive(self):
        """商品代碼不分大小寫"""
        sm = SessionManager("tgf")
        self.assertEqual(sm.DAY_END, dtime(16, 15))

    def test_empty_string_uses_default(self):
        """空字串使用預設"""
        sm = SessionManager("")
        self.assertEqual(sm.DAY_END, dtime(13, 45))


# ============================================================
# 2. 危機偵測門檻修正
# ============================================================

class TestCrisisDetection(unittest.TestCase):
    """測試危機偵測（修正後門檻）"""

    def setUp(self):
        self.classifier = MarketRegimeClassifier()

    def test_no_crisis_normal_bearish(self):
        """VIX=27.3 + PC=1.59 + Foreign=-580B → 不是危機（修正前會誤觸發）"""
        self.classifier.update_intelligence(
            vix=27.3, pc_ratio=1.59, foreign_spot=-580.2
        )
        self.assertFalse(self.classifier._is_crisis())

    def test_no_crisis_moderate_bearish(self):
        """VIX=29 + PC=1.4 + Foreign=-200B → 普通利空，不是危機"""
        self.classifier.update_intelligence(
            vix=29, pc_ratio=1.4, foreign_spot=-200
        )
        self.assertFalse(self.classifier._is_crisis())

    def test_crisis_high_vix_and_pc_and_foreign(self):
        """VIX=32 + PC=1.8 + Foreign=-400B → 3 個信號觸發 → 危機"""
        self.classifier.update_intelligence(
            vix=32, pc_ratio=1.8, foreign_spot=-400
        )
        self.assertTrue(self.classifier._is_crisis())

    def test_crisis_extreme_vix_direct(self):
        """VIX=42 → 極度恐慌，直接判定危機"""
        self.classifier.update_intelligence(vix=42, pc_ratio=0.5, foreign_spot=100)
        self.assertTrue(self.classifier._is_crisis())

    def test_no_crisis_vix40_boundary(self):
        """VIX=40 → 不直接觸發（需要 >40）"""
        self.classifier.update_intelligence(vix=40, pc_ratio=1.0, foreign_spot=0)
        self.assertFalse(self.classifier._is_crisis())

    def test_no_crisis_vix30_only(self):
        """VIX=31 alone → 只有 1 個信號，需 3 個"""
        self.classifier.update_intelligence(vix=31, pc_ratio=1.0, foreign_spot=0)
        self.assertFalse(self.classifier._is_crisis())

    def test_crisis_vix_roc_plus_two_others(self):
        """VIX 急升 30%+ + VIX>30 + PC>1.5 → 3 個信號 → 危機"""
        # 先灌入歷史 VIX = 20
        for _ in range(3):
            self.classifier._vix_history.append(20)
        # 現在 VIX = 32（上升 60%）
        self.classifier.update_intelligence(vix=32, pc_ratio=1.6, foreign_spot=-100)
        # VIX ROC > 30% ✓, VIX > 30 ✓, PC > 1.5 ✓ → 3 個信號
        self.assertTrue(self.classifier._is_crisis())

    def test_no_crisis_two_signals_only(self):
        """VIX=31 + PC=1.6 → 只有 2 個信號，不夠 3 個"""
        self.classifier.update_intelligence(vix=31, pc_ratio=1.6, foreign_spot=-100)
        self.assertFalse(self.classifier._is_crisis())


# ============================================================
# 3. K 棒時間戳 — fromtimestamp vs utcfromtimestamp
# ============================================================

class TestKBarTimestamp(unittest.TestCase):
    """測試 K 棒時間戳轉換正確性"""

    def test_fromtimestamp_local_time(self):
        """fromtimestamp 產生本地時間（台灣 UTC+8）"""
        # 2026-03-15 00:05:00 UTC+8 的 epoch
        taiwan_midnight = datetime(2026, 3, 15, 0, 5, 0)
        epoch = taiwan_midnight.timestamp()

        # fromtimestamp 應該回到原始的台灣本地時間
        result = datetime.fromtimestamp(epoch)
        self.assertEqual(result.hour, 0)
        self.assertEqual(result.minute, 5)

    def test_nanosecond_epoch_conversion(self):
        """奈秒 epoch → 正確轉換"""
        taiwan_time = datetime(2026, 3, 15, 0, 5, 0)
        epoch_ns = taiwan_time.timestamp() * 1e9

        # 模擬 broker.py 的邏輯
        epoch_sec = epoch_ns / 1e9 if epoch_ns > 1e12 else epoch_ns
        result = datetime.fromtimestamp(epoch_sec)
        self.assertEqual(result.hour, 0)
        self.assertEqual(result.minute, 5)

    def test_millisecond_epoch_conversion(self):
        """毫秒 epoch → 正確轉換（broker.py 只處理 >1e12 as nanoseconds）"""
        taiwan_time = datetime(2026, 3, 15, 1, 30, 0)
        epoch_sec = taiwan_time.timestamp()

        # broker.py 的邏輯：>1e12 → /1e9，否則直接用
        # 毫秒 epoch (~1.77e12) 大於 1e12，會被當作 nanoseconds 處理
        # 所以 broker 實際上不會收到毫秒格式 — Shioaji 回傳的是 nanoseconds 或 pandas Timestamp
        # 這裡測試秒級 epoch 的正確性
        result = datetime.fromtimestamp(epoch_sec)
        self.assertEqual(result.hour, 1)
        self.assertEqual(result.minute, 30)


# ============================================================
# 4. NaN/Inf JSON 安全清理
# ============================================================

class TestSafeRound(unittest.TestCase):
    """測試 _safe_round 工具函數"""

    def test_normal_float(self):
        self.assertEqual(_safe_round(3.14159, 2), 3.14)

    def test_nan_returns_default(self):
        self.assertEqual(_safe_round(float('nan')), 0)

    def test_inf_returns_default(self):
        self.assertEqual(_safe_round(float('inf')), 0)

    def test_neg_inf_returns_default(self):
        self.assertEqual(_safe_round(float('-inf')), 0)

    def test_none_returns_default(self):
        self.assertEqual(_safe_round(None), 0)

    def test_custom_default(self):
        self.assertEqual(_safe_round(float('nan'), default=50), 50)

    def test_zero(self):
        self.assertEqual(_safe_round(0.0), 0.0)

    def test_integer_like(self):
        self.assertEqual(_safe_round(42.0, 1), 42.0)


class TestSanitizeForJson(unittest.TestCase):
    """測試 Dashboard JSON 清理"""

    def test_nan_replaced(self):
        result = _sanitize_for_json({"adx": float('nan'), "rsi": 65.0})
        self.assertIsNone(result["adx"])
        self.assertEqual(result["rsi"], 65.0)

    def test_inf_replaced(self):
        result = _sanitize_for_json({"val": float('inf')})
        self.assertIsNone(result["val"])

    def test_neg_inf_replaced(self):
        result = _sanitize_for_json({"val": float('-inf')})
        self.assertIsNone(result["val"])

    def test_nested_dict(self):
        data = {"a": {"b": float('nan'), "c": 1.0}}
        result = _sanitize_for_json(data)
        self.assertIsNone(result["a"]["b"])
        self.assertEqual(result["a"]["c"], 1.0)

    def test_list_with_nan(self):
        data = [1.0, float('nan'), 3.0]
        result = _sanitize_for_json(data)
        self.assertEqual(result, [1.0, None, 3.0])

    def test_mixed_nested(self):
        """模擬真實 Dashboard 快照（TGF 無數據時）"""
        snapshot = {
            "instruments": {
                "TGF": {
                    "price": 0,
                    "snapshot": {"adx": float('nan'), "rsi": float('nan'), "atr": float('nan')},
                },
                "TMF": {
                    "price": 22100,
                    "snapshot": {"adx": 25.3, "rsi": 52.1, "atr": 45.0},
                },
            }
        }
        result = _sanitize_for_json(snapshot)
        self.assertIsNone(result["instruments"]["TGF"]["snapshot"]["adx"])
        self.assertEqual(result["instruments"]["TMF"]["snapshot"]["adx"], 25.3)

    def test_normal_data_unchanged(self):
        data = {"price": 22100, "qty": 1, "name": "test"}
        result = _sanitize_for_json(data)
        self.assertEqual(result, data)

    def test_string_and_int_passthrough(self):
        result = _sanitize_for_json({"s": "hello", "n": 42})
        self.assertEqual(result["s"], "hello")
        self.assertEqual(result["n"], 42)


class TestWebSocketSanitize(unittest.TestCase):
    """測試 WebSocket NaN 清理"""

    def test_nan_in_broadcast(self):
        result = _sanitize_floats({"val": float('nan')})
        self.assertIsNone(result["val"])

    def test_inf_in_broadcast(self):
        result = _sanitize_floats({"val": float('inf')})
        self.assertIsNone(result["val"])

    def test_nested_list(self):
        result = _sanitize_floats([{"a": float('nan')}, {"b": 1.0}])
        self.assertIsNone(result[0]["a"])
        self.assertEqual(result[1]["b"], 1.0)


# ============================================================
# 5. GoldAdaptiveParams EMA 平滑
# ============================================================

class TestGoldAdaptiveParams(unittest.TestCase):
    """測試黃金專用自適應參數"""

    def setUp(self):
        self.params = GoldAdaptiveParams()

    def test_default_values(self):
        """預設值正確（比台指期更寬）"""
        self.assertEqual(self.params.stop_loss_multiplier, 3.0)
        self.assertEqual(self.params.trailing_trigger, 2.0)
        self.assertEqual(self.params.trailing_distance, 1.5)
        self.assertEqual(self.params.time_stop_bars, 60)

    def test_ema_smoothing(self):
        """EMA 平滑：參數不會瞬間跳變"""
        initial_sl = self.params.stop_loss_multiplier  # 3.0

        # 一次更新不會直接跳到目標值
        self.params.update(atr_ratio=2.0, regime=MarketRegime.VOLATILE)
        after_one = self.params.stop_loss_multiplier
        # alpha=0.3, target=4.0 → 3.0 * 0.7 + 4.0 * 0.3 = 3.3
        self.assertAlmostEqual(after_one, 3.3, places=1)
        self.assertNotEqual(after_one, 4.0)  # 不會直接跳到目標

    def test_crisis_wider_stops(self):
        """危機模式停損更寬（5.0x ATR）"""
        for _ in range(20):  # 多次更新收斂
            self.params.update(atr_ratio=1.0, regime=MarketRegime.CRISIS_DOWN)
        # 應收斂到接近 5.0
        self.assertGreater(self.params.stop_loss_multiplier, 4.5)

    def test_low_volatility_tighter(self):
        """低波動收窄停損"""
        for _ in range(20):
            self.params.update(atr_ratio=0.5, regime=MarketRegime.WEAK_TREND_UP)
        # 應收斂到接近 2.0
        self.assertLess(self.params.stop_loss_multiplier, 2.5)

    def test_boundary_check_sl_max(self):
        """停損不超過 7.0x"""
        self.params.stop_loss_multiplier = 10.0
        self.params.update(atr_ratio=2.0, regime=MarketRegime.CRISIS_DOWN)
        self.assertLessEqual(self.params.stop_loss_multiplier, 7.0)

    def test_boundary_check_sl_min(self):
        """停損不低於 1.5x"""
        self.params.stop_loss_multiplier = 0.5
        self.params.update(atr_ratio=0.3, regime=MarketRegime.RANGING)
        self.assertGreaterEqual(self.params.stop_loss_multiplier, 1.5)

    def test_strong_trend_lowers_threshold(self):
        """強趨勢降低進場門檻"""
        self.params.min_signal_strength = 0.55
        self.params.update(atr_ratio=1.0, regime=MarketRegime.STRONG_TREND_UP)
        # 強趨勢 *= 0.85
        self.assertLess(self.params.min_signal_strength, 0.55)

    def test_time_stop_within_bounds(self):
        """時間停損在 20~120 之間"""
        self.params.update(atr_ratio=1.0, regime=MarketRegime.CRISIS_DOWN)
        self.assertGreaterEqual(self.params.time_stop_bars, 20)
        self.assertLessEqual(self.params.time_stop_bars, 120)


# ============================================================
# 6. 黃金策略 — 危機做多（與台指期相反）
# ============================================================

class TestGoldCrisisLogic(unittest.TestCase):
    """測試黃金危機模式邏輯"""

    def setUp(self):
        self.strategy = GoldTrendStrategy()

    def test_crisis_generates_buy(self):
        """黃金危機 → 做多（不是做空！）"""
        snap = MarketSnapshot(
            price=3200.0, atr=5.0,
            ema5=3198, ema10=3195, ema20=3190, ema60=3170,
            rsi=65, adx=30, plus_di=25, minus_di=15,
        )
        # price - ema60 = 30, 8*ATR = 40 → 未超漲
        signal = self.strategy._crisis_long_signal(snap)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.direction, SignalDirection.BUY)

    def test_crisis_long_stop_loss_below(self):
        """危機做多停損在下方"""
        snap = MarketSnapshot(
            price=3200.0, atr=5.0,
            ema5=3198, ema20=3190, ema60=3170,
            rsi=65, adx=30, plus_di=25, minus_di=15,
        )
        signal = self.strategy._crisis_long_signal(snap)
        self.assertIsNotNone(signal)
        self.assertLess(signal.stop_loss, snap.price)
        # 停損 = price - 5*ATR - 1 = 3200 - 25 - 1 = 3174
        self.assertAlmostEqual(signal.stop_loss, 3174.0, places=0)

    def test_crisis_long_rejected_when_overbought(self):
        """RSI > 82 → 不追多"""
        snap = MarketSnapshot(
            price=3200.0, atr=5.0,
            ema5=3195, ema20=3185, ema60=3100,
            rsi=85, adx=30, plus_di=25, minus_di=15,
        )
        signal = self.strategy._crisis_long_signal(snap)
        self.assertIsNone(signal)

    def test_crisis_long_rejected_when_overextended(self):
        """價格遠超 EMA60 (>8x ATR) → 不追多"""
        snap = MarketSnapshot(
            price=3300.0, atr=5.0,
            ema5=3295, ema20=3280, ema60=3200,  # price - ema60 = 100 > 8*5=40
            rsi=65, adx=30, plus_di=25, minus_di=15,
        )
        signal = self.strategy._crisis_long_signal(snap)
        self.assertIsNone(signal)

    def test_crisis_reversal_generates_sell(self):
        """危機反轉 → 做空（避險結束，金價回落）"""
        snap = MarketSnapshot(
            price=3200.0, atr=5.0,
            ema5=3198, ema20=3200,  # EMA5 ≈ EMA20（走弱）
            rsi=72, adx=30, plus_di=20, minus_di=22,
        )
        signal = self.strategy._crisis_reversal_signal(snap)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.direction, SignalDirection.SELL)

    def test_crisis_reversal_rejected_low_rsi(self):
        """RSI <= 60 → 不做空（無超買回落跡象）"""
        snap = MarketSnapshot(
            price=3200.0, atr=5.0,
            ema5=3198, ema20=3200,
            rsi=55,
        )
        signal = self.strategy._crisis_reversal_signal(snap)
        self.assertIsNone(signal)

    def test_gold_price_rounding(self):
        """黃金價格精度為小數 1 位"""
        snap = MarketSnapshot(
            price=3200.0, atr=5.0,
            ema5=3198, ema20=3190, ema60=3170,
            rsi=65, adx=30, plus_di=25, minus_di=15,
        )
        signal = self.strategy._crisis_long_signal(snap)
        # 停損和停利應該是 1 位小數
        self.assertEqual(signal.stop_loss, round(signal.stop_loss, 1))
        self.assertEqual(signal.take_profit, round(signal.take_profit, 1))

    def test_gold_uses_tgf_session(self):
        """黃金策略使用 TGF 交易時段"""
        self.assertEqual(self.strategy._session.DAY_END, dtime(16, 15))
        self.assertEqual(self.strategy._session.NIGHT_START, dtime(17, 25))


# ============================================================
# 7. 跨日 K 棒查詢邏輯
# ============================================================

class TestCrossDayKBarQuery(unittest.TestCase):
    """測試跨日 K 棒查詢日期邏輯"""

    def _get_start_date(self, now):
        """模擬 broker.py 的跨日查詢邏輯"""
        today = now.strftime("%Y-%m-%d")
        if now.hour < 5:
            start_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        else:
            start_date = today
        return start_date, today

    def test_midnight_queries_yesterday(self):
        """00:30 → 查詢從昨天開始"""
        now = datetime(2026, 3, 15, 0, 30)
        start, end = self._get_start_date(now)
        self.assertEqual(start, "2026-03-14")
        self.assertEqual(end, "2026-03-15")

    def test_2am_queries_yesterday(self):
        """02:00 → 查詢從昨天開始"""
        now = datetime(2026, 3, 15, 2, 0)
        start, end = self._get_start_date(now)
        self.assertEqual(start, "2026-03-14")

    def test_4am_queries_yesterday(self):
        """04:00 → 查詢從昨天開始"""
        now = datetime(2026, 3, 15, 4, 0)
        start, end = self._get_start_date(now)
        self.assertEqual(start, "2026-03-14")

    def test_5am_queries_today(self):
        """05:00 → 查詢今天（夜盤已結束）"""
        now = datetime(2026, 3, 15, 5, 0)
        start, end = self._get_start_date(now)
        self.assertEqual(start, "2026-03-15")

    def test_9am_queries_today(self):
        """09:00 → 查詢今天"""
        now = datetime(2026, 3, 15, 9, 0)
        start, end = self._get_start_date(now)
        self.assertEqual(start, "2026-03-15")

    def test_night_session_queries_today(self):
        """20:00 → 查詢今天"""
        now = datetime(2026, 3, 14, 20, 0)
        start, end = self._get_start_date(now)
        self.assertEqual(start, "2026-03-14")


# ============================================================
# 8. MarketRegimeClassifier 危機整合
# ============================================================

class TestRegimeWithCrisis(unittest.TestCase):
    """測試 RegimeClassifier 在不同市場數據下的行為"""

    def test_normal_bearish_not_crisis(self):
        """正常偏空日不觸發危機（修正前會觸發）"""
        classifier = MarketRegimeClassifier()
        classifier.update_intelligence(vix=27.3, pc_ratio=1.59, foreign_spot=-580.2)

        snap = MarketSnapshot(
            adx=12, plus_di=18, minus_di=20,
            ema5=22000, ema20=22050, ema60=22100,
            atr_ratio=1.0,
        )
        regime = classifier.classify(snap)
        # 不應該是 CRISIS_DOWN
        self.assertNotEqual(regime, MarketRegime.CRISIS_DOWN)

    def test_real_crisis_triggers(self):
        """真正的危機觸發 CRISIS_DOWN"""
        classifier = MarketRegimeClassifier()
        classifier.update_intelligence(vix=35, pc_ratio=1.8, foreign_spot=-500)

        snap = MarketSnapshot(
            adx=40, plus_di=10, minus_di=35,
            ema5=21500, ema20=21800, ema60=22000,
            atr_ratio=1.5,
        )
        regime = classifier.classify(snap)
        self.assertEqual(regime, MarketRegime.CRISIS_DOWN)

    def test_crisis_bypasses_hysteresis(self):
        """危機模式不需要遲滯，立即觸發"""
        classifier = MarketRegimeClassifier()
        classifier.update_intelligence(vix=42)  # VIX > 40 直接觸發

        snap = MarketSnapshot(adx=20, atr_ratio=1.0,
                              ema5=22000, ema20=22000, ema60=22000)
        regime = classifier.classify(snap)
        # 第一根就應該是 CRISIS_DOWN（不需要等第二根）
        self.assertEqual(regime, MarketRegime.CRISIS_DOWN)


# ============================================================
# 9. 黃金策略出場機制
# ============================================================

class TestGoldExitLayers(unittest.TestCase):
    """測試黃金 7 層出場保護"""

    def setUp(self):
        self.strategy = GoldTrendStrategy()

    def test_session_close_exit(self):
        """盤別收盤 → 強制平倉"""
        from core.position import Position, Side

        pos = Position(
            side=Side.LONG, entry_price=3200.0, quantity=1,
            stop_loss=3170.0, take_profit=3260.0,
            entry_time=datetime.now(),
        )

        snap = MarketSnapshot(price=3210.0, atr=5.0)

        # Mock session to return CLOSING
        original_get_phase = self.strategy._session.get_phase
        self.strategy._session.get_phase = lambda now=None: SessionPhase.CLOSING

        signal = self.strategy.check_exit(pos, snap)
        self.assertIsNotNone(signal)
        self.assertEqual(signal.direction, SignalDirection.CLOSE)
        self.assertIn("收盤", signal.reason)

        self.strategy._session.get_phase = original_get_phase

    def test_stop_loss_long(self):
        """做多停損觸發"""
        from core.position import Position, Side

        pos = Position(
            side=Side.LONG, entry_price=3200.0, quantity=1,
            stop_loss=3185.0, take_profit=3260.0,
            entry_time=datetime.now(),
        )

        snap = MarketSnapshot(price=3184.0, atr=5.0)
        signal = self.strategy.check_exit(pos, snap)
        self.assertIsNotNone(signal)
        self.assertIn("停損", signal.reason)

    def test_time_stop(self):
        """時間停損：超過 N 根 K 棒且獲利不足"""
        from core.position import Position, Side

        pos = Position(
            side=Side.LONG, entry_price=3200.0, quantity=1,
            stop_loss=3170.0, take_profit=3260.0,
            entry_time=datetime.now(),
        )
        pos.bars_since_entry = 100  # 超過 time_stop_bars (60)

        snap = MarketSnapshot(price=3201.0, atr=5.0)  # 獲利只有 1 點 < 0.5*ATR=2.5
        signal = self.strategy.check_exit(pos, snap)
        self.assertIsNotNone(signal)
        self.assertIn("時間停損", signal.reason)

    def test_no_exit_when_flat(self):
        """空倉不觸發出場"""
        from core.position import Position
        pos = Position()
        snap = MarketSnapshot(price=3200.0, atr=5.0)
        signal = self.strategy.check_exit(pos, snap)
        self.assertIsNone(signal)

    def test_profit_giveback_protection(self):
        """利潤回吐超過 65% → 平倉（門檻 2x ATR）"""
        from core.position import Position, Side

        pos = Position(
            side=Side.LONG, entry_price=3200.0, quantity=1,
            stop_loss=3170.0, take_profit=3260.0,
            entry_time=datetime.now(),
        )
        # 最大獲利必須 > 2x ATR = 10（設 15）
        pos.max_unrealized_profit = 15.0

        snap = MarketSnapshot(price=3203.0, atr=5.0)
        # 當前獲利 = 3，回吐 = 15 - 3 = 12，> 65% of 15 = 9.75
        signal = self.strategy.check_exit(pos, snap)
        self.assertIsNotNone(signal)
        self.assertIn("回吐", signal.reason)


if __name__ == "__main__":
    unittest.main(verbosity=2)
