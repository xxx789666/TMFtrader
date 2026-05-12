"""
BreakoutTrendStrategy — 台指期 ATR 壓縮突破 + 趨勢回調策略

邏輯說明：
  模式 A — ATR 壓縮突破
    當 ATR 低於均值（整理壓縮），接著 ATR 放大 + DI 方向明確 → 突破進場
    捕捉：開盤整理後的爆發、重要消息後的單邊行情

  模式 B — 強趨勢回調
    EMA5 > EMA20 > EMA60（强上升趨勢）或反之（强下降趨勢）
    等 EMA5 回到 EMA20 附近（回調）再進場
    捕捉：趨勢中繼的低風險加碼點

進場過濾：
  - 基本 ADX >= 15（趨勢強度）
  - 11:00 後 ADX >= 28（午後高噪訊時段加嚴）
  - Mode B 永遠要求 ADX > 22（強趨勢才回調進場）

出場：
  - ATR 追蹤止損（獲利超過 trail_trigger_atr 後啟動）
  - 固定時間止損：超過 max_bars 根強制出場
  - 盤末止損：收盤前 15 分鐘平倉
"""

from datetime import datetime, time
from typing import Optional

from loguru import logger as _logger

from strategy.base import BaseStrategy, Signal, SignalDirection
from core.market_data import KBar, MarketSnapshot
from core.position import Position, Side


class BreakoutTrendStrategy(BaseStrategy):
    """
    ATR 壓縮突破 + 趨勢回調 雙模式策略
    """

    @property
    def name(self) -> str:
        return "BreakoutTrend"

    def __init__(
        self,
        # 進場參數
        squeeze_ratio: float = 0.90,     # ATR < avg × 此比例 → 判定為壓縮（5min 放寬 0.78→0.90）
        expand_ratio: float = 1.08,      # ATR > avg × 此比例 → 壓縮結束、擴張
        squeeze_grace_bars: int = 1,     # 壓縮結束後的寬限根數（1=等同原始行為；>1=允許更慢的展開）
        min_adx: float = 20.0,           # 最低 ADX（趨勢強度門檻）
        min_di_gap: float = 5.0,         # +DI 與 -DI 的最小差距（方向明確性）
        min_vol_ratio: float = 1.0,      # 最低成交量比（5min 放寬 1.15→1.0）
        pullback_ema_gap: float = 0.3,   # 回調模式：EMA5 距 EMA20 ≤ gap × ATR 才算回調
        afternoon_min_adx: float = 32.0, # 11:00後進場需更強趨勢
        # 出場參數（5min K線最佳化結果）
        sl_atr: float = 2.5,             # 停損 ATR 倍數
        tp_atr: float = 10.0,            # 固定停利 ATR 倍數（幾乎不觸及，由 trail 出場）
        trail_trigger_atr: float = 1.0,  # 超過此盈利（ATR 倍數）啟動追蹤止損（5min 1.75→1.0）
        trail_dist_atr: float = 1.25,    # 追蹤止損距離（ATR 倍數）（5min 1.75→1.25）
        max_bars: int = 80,              # 時間止損（根）5min×80根=400min≈6.7小時
        breakeven_trigger_atr: float = 999.0,  # 獲利超過此倍數 ATR 後，將止損移至保本（999=停用）
        early_cut_bars: int = 25,        # 早切止損啟動根數：持倉超過此根數且虧損則提前出場
        early_cut_loss_atr: float = 1.5, # 早切止損虧損門檻（ATR 倍數）
        max_loss_twd: float = 4000.0,     # 金額硬止損（TWD）：0=停用，>0=單筆虧損超過此金額立即出場（優先於其他出場）（最佳化：4,000）
        point_value: float = 10.0,       # 每點價值（TMF=10元/點），用於計算金額止損
        # 分批出場（Scale-out）
        scale_out_trigger_atr: float = 0.0,  # 獲利達到此倍 ATR 時，先平部分口數（0=停用）
        scale_out_qty: int = 1,              # 分批出場口數（建議 1，讓剩餘 2 口繼續追蹤）
        trend_filter: bool = True,       # 趨勢過濾：True=以 EMA200 判斷宏觀多空偏向
        ema200_margin_atr: float = 0.0,  # EMA200 緩衝帶（ATR 倍數）：0=嚴格，>0=price 在 EMA200 ± margin 內視為中性
        use_momentum_score: bool = True, # True=加入 RSI+盤中動能評分，修正 EMA200 的滯後性
        momentum_rsi_bull: float = 52.0, # RSI_MA5 > 此值 → 動能偏多（+1 分）（最佳化：52）
        momentum_rsi_bear: float = 48.0, # RSI_MA5 < 此值 → 動能偏空（-1 分）（最佳化：48）
        momentum_session_atr: float = 0.5, # 盤中漲幅 > N×ATR → 今日偏多（+1 分）；< -N×ATR → 偏空（-1 分）（最佳化：0.5）
        # 時段
        session_start: tuple = (8, 45),  # 台指正常盤開盤
        session_end: tuple = (13, 15),   # 收盤前
        force_close_time: tuple = (13, 25),  # 強制平倉時間
    ):
        self.squeeze_ratio = squeeze_ratio
        self.expand_ratio = expand_ratio
        self.squeeze_grace_bars = squeeze_grace_bars
        self.min_adx = min_adx
        self.min_di_gap = min_di_gap
        self.min_vol_ratio = min_vol_ratio
        self.pullback_ema_gap = pullback_ema_gap
        self.afternoon_min_adx = afternoon_min_adx
        self.sl_atr = sl_atr
        self.tp_atr = tp_atr
        self.trail_trigger_atr = trail_trigger_atr
        self.trail_dist_atr = trail_dist_atr
        self.max_bars = max_bars
        self.breakeven_trigger_atr = breakeven_trigger_atr
        self.early_cut_bars = early_cut_bars
        self.early_cut_loss_atr = early_cut_loss_atr
        self.max_loss_twd = max_loss_twd
        self.point_value = point_value
        self.scale_out_trigger_atr = scale_out_trigger_atr
        self.scale_out_qty = scale_out_qty
        self.trend_filter = trend_filter
        self.ema200_margin_atr = ema200_margin_atr
        self.use_momentum_score = use_momentum_score
        self.momentum_rsi_bull = momentum_rsi_bull
        self.momentum_rsi_bear = momentum_rsi_bear
        self.momentum_session_atr = momentum_session_atr
        self.session_start = time(*session_start)
        self.session_end = time(*session_end)
        self.force_close_time = time(*force_close_time)

        # 策略狀態
        self._prev_ema5: float = 0.0
        self._prev_ema20: float = 0.0
        self._was_squeeze: bool = False
        self._squeeze_bars: int = 0      # 連續壓縮根數
        self._squeeze_grace: int = 0     # 壓縮結束後的寬限計數
        self._in_grace: bool = False     # 目前是否在寬限期
        self._entry_atr: float = 0.0
        self._trail_best: float = 0.0   # long: 最高價 / short: 最低價
        self._breakeven_active: bool = False  # 保本止損是否已啟動
        self._scaled_out: bool = False   # 分批出場是否已執行
        self._current_bar_time: Optional[datetime] = None
        self._cooldown_bars: int = 0    # 進場後冷卻（避免過度交易）
        self._session_open: float = 0.0  # 今日盤開價（08:45 第一根）
        self._current_date: str = ""     # 今日日期字串（換日偵測）

    # ─────────────────────────────────────────────
    # 進場
    # ─────────────────────────────────────────────
    def on_kbar(self, kbar: KBar, snapshot: MarketSnapshot, **kwargs) -> Optional[Signal]:
        self._current_bar_time = kbar.datetime
        price = snapshot.price
        atr = snapshot.atr if snapshot.atr > 0 else 1.0
        atr_avg = snapshot.atr_ma20 if snapshot.atr_ma20 > 0 else atr
        bar_time = kbar.datetime.time()

        # ── 換日：重置今日盤開
        bar_date = kbar.datetime.strftime('%Y-%m-%d')
        if bar_date != self._current_date:
            self._current_date = bar_date
            self._session_open = 0.0
        if self._session_open == 0.0 and bar_time >= self.session_start:
            self._session_open = price

        # ── 暖機期
        if snapshot.bar_count < 80:
            self._prev_ema5 = snapshot.ema5
            self._prev_ema20 = snapshot.ema20
            return None

        # ── 冷卻期（剛出場後不立即再進場）
        if self._cooldown_bars > 0:
            self._cooldown_bars -= 1
            self._prev_ema5 = snapshot.ema5
            self._prev_ema20 = snapshot.ema20
            return None

        # ── 時段過濾（日盤 08:45–13:45 + 夜盤 15:00–05:00）
        in_day   = time(8, 45) <= bar_time <= time(13, 45)
        in_night = bar_time >= time(15, 0) or bar_time <= time(5, 0)

        # ── [Scan] 每根 K 棒掃描紀錄
        vol_r = snapshot.volume_ratio if hasattr(snapshot, 'volume_ratio') else 0
        atr_r_log = (snapshot.atr / snapshot.atr_ma20) if (hasattr(snapshot, 'atr_ma20') and snapshot.atr_ma20 > 0) else 0
        _logger.info(
            f"[Scan] {kbar.datetime.strftime('%H:%M:%S')} price={int(snapshot.price)} "
            f"adx={snapshot.adx:.1f} ema5={snapshot.ema5:.0f} ema20={snapshot.ema20:.0f} ema200={snapshot.ema200:.0f} "
            f"rsi={snapshot.rsi:.1f} "
            f"+DI={snapshot.plus_di:.1f} -DI={snapshot.minus_di:.1f} "
            f"volR={vol_r:.2f} atrR={atr_r_log:.2f} "
            f"in_day={in_day} in_night={in_night}"
        )

        if not (in_day or in_night):
            self._prev_ema5 = snapshot.ema5
            self._prev_ema20 = snapshot.ema20
            return None

        # ── 有效 ADX 門檻（日盤午後加嚴，夜盤用基本門檻）
        effective_min_adx = self.min_adx
        if in_day and bar_time >= time(11, 0):
            effective_min_adx = max(self.min_adx, self.afternoon_min_adx)
        # Mode B：強趨勢模式至少需要 22
        b_min_adx = max(22.0, effective_min_adx)

        signal = None
        atr_ratio = atr / atr_avg if atr_avg > 0 else 1.0


        # ── 壓縮偵測
        # squeeze_grace_bars=1 等同原始行為（第1根壓縮即設 flag；有效壓縮≥3根才保留寬限）
        if atr_ratio < self.squeeze_ratio:
            self._was_squeeze = True       # 第 1 根壓縮即標記（原始行為）
            self._squeeze_bars += 1
            self._in_grace = False         # 重回壓縮 → 清除寬限狀態
            self._squeeze_grace = 0
        else:
            if self._in_grace:
                # 已在寬限期 — 繼續計數
                self._squeeze_grace += 1
                if self._squeeze_grace > self.squeeze_grace_bars:
                    self._was_squeeze = False
                    self._in_grace = False
                    self._squeeze_grace = 0
            elif self._squeeze_bars >= 3:
                # 有效壓縮（≥3根）剛結束 — 進入寬限期
                self._in_grace = True
                self._squeeze_grace = 1
                if self._squeeze_grace > self.squeeze_grace_bars:
                    self._was_squeeze = False
                    self._in_grace = False
                    self._squeeze_grace = 0
            else:
                # 壓縮根數不足 → 取消
                self._was_squeeze = False
                self._squeeze_grace = 0
            self._squeeze_bars = 0

        # ── 趨勢允許方向（EMA200 + 動能評分）
        trend_allow_long, trend_allow_short = self._compute_trend_allow(price, atr, snapshot)

        # ── 成交量門檻（夜盤量小，放寬至 0.3）
        effective_vol_ratio = self.min_vol_ratio if in_day else 0.3

        # ── 模式 A：ATR 壓縮突破
        if (
            self._was_squeeze
            and atr_ratio >= self.expand_ratio
            and snapshot.adx >= effective_min_adx
        ):
            long_ok = (
                snapshot.plus_di - snapshot.minus_di >= self.min_di_gap
                and snapshot.volume_ratio >= effective_vol_ratio
                and price > snapshot.ema20
                and trend_allow_long
            )
            short_ok = (
                snapshot.minus_di - snapshot.plus_di >= self.min_di_gap
                and snapshot.volume_ratio >= effective_vol_ratio
                and price < snapshot.ema20
                and trend_allow_short
            )

            if long_ok:
                sl = price - self.sl_atr * atr
                tp = price + self.tp_atr * atr
                signal = Signal(
                    direction=SignalDirection.BUY,
                    strength=0.85,
                    stop_loss=sl,
                    take_profit=tp,
                    reason=(f"A-Squeeze LONG atr_ratio={atr_ratio:.2f} "
                            f"adx={snapshot.adx:.0f} +DI={snapshot.plus_di:.0f}"),
                    source=self.name,
                )
                self._was_squeeze = False
                self._squeeze_bars = 0

            elif short_ok:
                sl = price + self.sl_atr * atr
                tp = price - self.tp_atr * atr
                signal = Signal(
                    direction=SignalDirection.SELL,
                    strength=0.85,
                    stop_loss=sl,
                    take_profit=tp,
                    reason=(f"A-Squeeze SHORT atr_ratio={atr_ratio:.2f} "
                            f"adx={snapshot.adx:.0f} -DI={snapshot.minus_di:.0f}"),
                    source=self.name,
                )
                self._was_squeeze = False
                self._squeeze_bars = 0

        # ── 模式 B：強趨勢回調進場（至少需要 ADX>22 的強趨勢）
        if signal is None:
            strong_up = (
                snapshot.ema5 > snapshot.ema20 > snapshot.ema60
                and snapshot.adx > b_min_adx
                and snapshot.plus_di > snapshot.minus_di + 8
            )
            strong_down = (
                snapshot.ema5 < snapshot.ema20 < snapshot.ema60
                and snapshot.adx > b_min_adx
                and snapshot.minus_di > snapshot.plus_di + 8
            )

            # EMA5 回調並剛跨過 EMA20（交叉確認）
            gap_limit = self.pullback_ema_gap * atr
            near_ema20_long = abs(snapshot.ema5 - snapshot.ema20) <= gap_limit
            near_ema20_short = abs(snapshot.ema5 - snapshot.ema20) <= gap_limit

            ema5_cross_up = (
                self._prev_ema5 <= self._prev_ema20
                and snapshot.ema5 > snapshot.ema20
            )
            ema5_cross_down = (
                self._prev_ema5 >= self._prev_ema20
                and snapshot.ema5 < snapshot.ema20
            )

            if strong_up and ema5_cross_up and near_ema20_long and trend_allow_long:
                sl = snapshot.ema20 - self.sl_atr * atr
                tp = price + self.tp_atr * atr
                signal = Signal(
                    direction=SignalDirection.BUY,
                    strength=0.78,
                    stop_loss=sl,
                    take_profit=tp,
                    reason=(f"B-Pullback LONG ema5={snapshot.ema5:.0f} "
                            f"ema20={snapshot.ema20:.0f} adx={snapshot.adx:.0f}"),
                    source=self.name,
                )

            elif strong_down and ema5_cross_down and near_ema20_short and trend_allow_short:
                sl = snapshot.ema20 + self.sl_atr * atr
                tp = price - self.tp_atr * atr
                signal = Signal(
                    direction=SignalDirection.SELL,
                    strength=0.78,
                    stop_loss=sl,
                    take_profit=tp,
                    reason=(f"B-Pullback SHORT ema5={snapshot.ema5:.0f} "
                            f"ema20={snapshot.ema20:.0f} adx={snapshot.adx:.0f}"),
                    source=self.name,
                )

        if signal is not None:
            self._entry_atr = atr
            self._trail_best = price
            self._breakeven_active = False
            self._scaled_out = False
            self._cooldown_bars = 5

        self._prev_ema5 = snapshot.ema5
        self._prev_ema20 = snapshot.ema20
        return signal

    # ─────────────────────────────────────────────
    # 趨勢方向過濾（EMA200 + 動能評分）
    # ─────────────────────────────────────────────
    def _compute_trend_allow(self, price: float, atr: float, snapshot: MarketSnapshot):
        """
        計算今根 K 棒允許的進場方向。
        回傳 (allow_long, allow_short)。

        EMA200 給予 2 倍權重（±2 分），RSI_MA5 和盤中漲幅各 ±1 分。
        合計 score 範圍 [-4, +4]：
          score >= +2 → 偏多，封鎖做空
          score <= -2 → 偏空，封鎖做多
          else       → 中性，雙向皆可
        """
        if not self.trend_filter:
            return True, True

        ema200 = snapshot.ema200
        if ema200 <= 0:
            return True, True

        margin = self.ema200_margin_atr * atr
        if price > ema200 + margin:
            ema200_vote = 1
        elif price < ema200 - margin:
            ema200_vote = -1
        else:
            ema200_vote = 0

        if not self.use_momentum_score:
            # 純 EMA200 模式（原本行為）
            return ema200_vote >= 0, ema200_vote <= 0

        # RSI_MA5 動能
        rsi_vote = 0
        if snapshot.rsi_ma5 > self.momentum_rsi_bull:
            rsi_vote = 1
        elif snapshot.rsi_ma5 < self.momentum_rsi_bear:
            rsi_vote = -1

        # 盤中方向（今日漲幅 vs ATR）
        session_vote = 0
        if self._session_open > 0 and atr > 0:
            session_move = (price - self._session_open) / atr
            if session_move > self.momentum_session_atr:
                session_vote = 1
            elif session_move < -self.momentum_session_atr:
                session_vote = -1

        # 合成評分：EMA200 加倍，RSI 和盤中各 1 票
        score = ema200_vote * 2 + rsi_vote + session_vote
        return score > -2, score < 2

    # ─────────────────────────────────────────────
    # 出場
    # ─────────────────────────────────────────────
    def check_exit(self, position: Position, snapshot: MarketSnapshot) -> Optional[Signal]:
        price = snapshot.price
        atr = self._entry_atr if self._entry_atr > 0 else max(snapshot.atr, 1.0)
        is_long = (position.side == Side.LONG)

        def close_signal(reason: str) -> Signal:
            return Signal(
                direction=SignalDirection.CLOSE,
                strength=1.0,
                stop_loss=price,
                take_profit=price,
                reason=reason,
                source=self.name,
            )

        # ── 金額硬止損（最高優先，任何根數都檢查）
        if self.max_loss_twd > 0:
            loss_pts = (position.entry_price - price) if is_long else (price - position.entry_price)
            if loss_pts > 0:
                loss_twd = loss_pts * position.quantity * self.point_value
                if loss_twd >= self.max_loss_twd:
                    return close_signal(f"金額止損 -{loss_twd:,.0f}TWD {loss_pts:.0f}pts")

        # ── 強制盤末平倉（日盤 13:25 / 夜盤 04:55）
        if self._current_bar_time is not None:
            bar_time = self._current_bar_time.time()
            night_force_close = time(4, 55)
            in_day_session = time(8, 45) <= bar_time <= time(13, 45)
            if in_day_session and bar_time >= self.force_close_time:
                return close_signal("日盤盤末強制平倉")
            elif not in_day_session and bar_time >= night_force_close and bar_time <= time(5, 10):
                return close_signal("夜盤盤末強制平倉")

        # ── 時間出場
        if position.bars_since_entry >= self.max_bars:
            return close_signal(f"時間出場 {position.bars_since_entry}根")

        # ── 早切止損（持倉 N 根後仍虧損超過門檻，提前出場）
        if self.early_cut_bars > 0 and position.bars_since_entry >= self.early_cut_bars:
            entry_tmp = position.entry_price
            loss_pts = (entry_tmp - price) if is_long else (price - entry_tmp)
            if loss_pts >= self.early_cut_loss_atr * atr:
                return close_signal(f"早切止損 {position.bars_since_entry}根 -{loss_pts:.0f}pts")

        # ── 分批出場（Scale-out）：獲利達標時先平部分口數，讓剩餘口數繼續追蹤
        entry = position.entry_price
        profit_pts = (price - entry) if is_long else (entry - price)
        profit_atr = profit_pts / atr

        if (
            self.scale_out_trigger_atr > 0
            and not self._scaled_out
            and position.quantity > self.scale_out_qty  # 確保剩餘口數 > 0
            and profit_atr >= self.scale_out_trigger_atr
        ):
            self._scaled_out = True
            sig = close_signal(
                f"分批出場 {self.scale_out_qty}口 profit={profit_atr:.2f}ATR"
            )
            sig.close_quantity = self.scale_out_qty
            return sig

        # ── 追蹤止損 + 保本止損

        # 保本止損：獲利曾超過 breakeven_trigger_atr，啟動保本
        if profit_atr >= self.breakeven_trigger_atr:
            self._breakeven_active = True

        if is_long:
            self._trail_best = max(self._trail_best, price)
            if profit_atr >= self.trail_trigger_atr:
                trail_stop = self._trail_best - self.trail_dist_atr * atr
                if price <= trail_stop:
                    return close_signal(f"追蹤出場 {trail_stop:.0f}")
            elif self._breakeven_active and price <= entry:
                return close_signal(f"保本出場 {entry:.0f}")
        else:
            self._trail_best = min(self._trail_best, price)
            if profit_atr >= self.trail_trigger_atr:
                trail_stop = self._trail_best + self.trail_dist_atr * atr
                if price >= trail_stop:
                    return close_signal(f"追蹤出場 {trail_stop:.0f}")
            elif self._breakeven_active and price >= entry:
                return close_signal(f"保本出場 {entry:.0f}")

        return None

    def get_parameters(self) -> dict:
        return {
            "squeeze_ratio": self.squeeze_ratio,
            "expand_ratio": self.expand_ratio,
            "min_adx": self.min_adx,
            "sl_atr": self.sl_atr,
            "tp_atr": self.tp_atr,
            "trail_trigger_atr": self.trail_trigger_atr,
            "trail_dist_atr": self.trail_dist_atr,
            "max_bars": self.max_bars,
            "early_cut_bars": self.early_cut_bars,
            "early_cut_loss_atr": self.early_cut_loss_atr,
        }

    def reset(self):
        self._prev_ema5 = 0.0
        self._prev_ema20 = 0.0
        self._was_squeeze = False
        self._squeeze_bars = 0
        self._squeeze_grace = 0
        self._in_grace = False
        self._entry_atr = 0.0
        self._trail_best = 0.0
        self._breakeven_active = False
        self._scaled_out = False
        self._current_bar_time = None
        self._cooldown_bars = 0
        self._session_open = 0.0
        self._current_date = ""
