"""NightORBStrategy — 夜盤開盤區間(ORB)策略,支援突破/反轉兩模式。

夜盤(15:00 開盤)頭 `or_bars` 根定義開盤區間(OR high/low);之後:
- mode="breakout":價突破 OR 上緣→多、下緣→空(順勢)。
- mode="fade":價突破 OR 緣→反向(均值回歸)。
過濾:OR 寬度需落在 [min_or_atr, max_or_atr]×ATR(太窄=假突破多、太寬=已噴);ADX 門檻。
每夜最多 1 筆(控頻率)。出場:ATR trail + 時間止損 + 金額硬停損 + 夜盤盤末強平。
盤末強平窗 [04:55,05:10] 比對 snapshot.timestamp(引擎每 tick 餵的當下時間),收盤前平掉
既有倉,避免裸抱過 05:00 收盤被隔日撮合跳空掃損;與 TIMEFRAME 無關。

session 感知:夜盤交易日 = 15:00 後算當日、午夜後算前一日(跨午夜歸同一夜)。
"""
from datetime import time, timedelta
from typing import Optional

from loguru import logger

from strategy.base import BaseStrategy, Signal, SignalDirection
from core.market_data import KBar, MarketSnapshot
from core.position import Position, Side


class NightORBStrategy(BaseStrategy):
    def __init__(self, mode: str = "breakout", or_bars: int = 6,
                 buf_atr: float = 0.10, min_or_atr: float = 0.5, max_or_atr: float = 6.0,
                 min_adx: float = 0.0, sl_atr: float = 1.5, tp_atr: float = 4.0,
                 trail_trigger_atr: float = 1.0, trail_dist_atr: float = 1.2,
                 max_hold_bars: int = 24, max_loss_twd: float = 4000.0, point_value: float = 10.0,
                 force_close_after: tuple = (4, 55), force_close_until: tuple = (5, 10)):
        self.mode = mode
        self.or_bars = or_bars
        self.buf_atr = buf_atr
        self.min_or_atr = min_or_atr
        self.max_or_atr = max_or_atr
        self.min_adx = min_adx
        self.sl_atr = sl_atr
        self.tp_atr = tp_atr
        self.trail_trigger_atr = trail_trigger_atr
        self.trail_dist_atr = trail_dist_atr
        self.max_hold_bars = max_hold_bars
        self.max_loss_twd = max_loss_twd
        self.point_value = point_value
        # 夜盤盤末強平窗 [04:55, 05:10]:避免裸抱過 05:00 收盤 → 隔日 08:45 撮合吃跳空。
        # 夜盤跨午夜,故用「有界窗」而非 >=（傍晚 20:00 數值上也 > 04:55）。窗在 check_exit
        # 裡比對的是 snapshot.timestamp（引擎每 tick 餵的當下時間),不是 K 棒時間 → 與 TF 無關。
        self.force_close_after = time(*force_close_after)
        self.force_close_until = time(*force_close_until)
        self.reset()

    @property
    def name(self) -> str:
        return f"NightORB-{self.mode}"

    @staticmethod
    def _sess_of(dt):
        return dt.date() if dt.time() >= time(15, 0) else (dt - timedelta(days=1)).date()

    def _mk(self, direction, price, atr, reason):
        long = direction == SignalDirection.BUY
        sl = price - self.sl_atr * atr if long else price + self.sl_atr * atr
        tp = price + self.tp_atr * atr if long else price - self.tp_atr * atr
        s = Signal(direction=direction, strength=0.8, stop_loss=sl, take_profit=tp,
                   reason=reason, source=self.name)
        s.trail_dist_pts = round(self.trail_dist_atr * atr)
        return s

    def on_kbar(self, kbar: KBar, snapshot: MarketSnapshot, **kw) -> Optional[Signal]:
        self._bar_time = kbar.datetime
        atr = snapshot.atr if snapshot.atr > 0 else 1.0
        sess = self._sess_of(kbar.datetime)

        if sess != self._cur_sess:                       # 進入新的一夜:重置 OR
            self._cur_sess = sess
            self._or_hi, self._or_lo, self._or_n = kbar.high, kbar.low, 1
            self._traded = self._or_ready = self._skip_logged = False
            return None

        if self._or_n < self.or_bars:                    # 累積開盤區間
            self._or_hi = max(self._or_hi, kbar.high)
            self._or_lo = min(self._or_lo, kbar.low)
            self._or_n += 1
            if self._or_n >= self.or_bars:               # OR 剛建好:記寬度判決(每夜一次)
                self._or_ready = True
                w = self._or_hi - self._or_lo
                lo_b, hi_b = self.min_or_atr * atr, self.max_or_atr * atr
                verdict = "可交易" if lo_b <= w <= hi_b else ("太寬不交易" if w > hi_b else "太窄不交易")
                logger.info(
                    f"[NightORB] OR ready @ {kbar.datetime.strftime('%H:%M')}: "
                    f"OR=[{self._or_lo:.0f},{self._or_hi:.0f}] 寬={w:.0f} ATR={atr:.0f} "
                    f"合格範圍=[{self.min_or_atr},{self.max_or_atr}]xATR=[{lo_b:.0f},{hi_b:.0f}] -> {verdict}")
            return None

        if self._traded or not self._or_ready:
            return None
        # 夜盤交易時段 gate:只在 15:00–05:00 進場。_sess_of 把 00:00–15:00 歸前一夜(供跨午夜
        # 群組),但 OR ready 後若無此 gate,會在日盤(05:00–15:00)拿舊夜 OR 評估突破而誤進場
        # (2026-06-05 事故:12:00 誤進 ORB-SHORT)。出場/盤末強平(check_exit)不受此限。
        nt = kbar.datetime.time()
        if not (nt >= time(15, 0) or nt <= time(5, 0)):
            return None
        orw = self._or_hi - self._or_lo
        if orw < self.min_or_atr * atr or orw > self.max_or_atr * atr:
            return None   # 寬度不符:已在 OR-ready 記過判決、此處不重複(避免每根 spam)
        price = snapshot.price
        up = price > self._or_hi + self.buf_atr * atr
        dn = price < self._or_lo - self.buf_atr * atr
        if snapshot.adx < self.min_adx:
            # 只在「有突破但被 ADX 擋」時記一次(每夜)
            if (up or dn) and not self._skip_logged:
                self._skip_logged = True
                logger.info(f"[NightORB-skip] 突破但 ADX={snapshot.adx:.1f}<{self.min_adx}"
                            f" | OR=[{self._or_lo:.0f},{self._or_hi:.0f}] price={price:.0f}")
            return None
        sig = None
        if self.mode == "breakout":
            if up:
                sig = self._mk(SignalDirection.BUY, price, atr, "ORB-LONG")
            elif dn:
                sig = self._mk(SignalDirection.SELL, price, atr, "ORB-SHORT")
        else:  # fade
            if up:
                sig = self._mk(SignalDirection.SELL, price, atr, "ORBfade-SHORT")
            elif dn:
                sig = self._mk(SignalDirection.BUY, price, atr, "ORBfade-LONG")
        if sig is not None:
            self._traded = True
            self._entry_atr = atr
            self._trail_best = price
        return sig

    def check_exit(self, position: Position, snapshot: MarketSnapshot) -> Optional[Signal]:
        price = snapshot.price
        atr = self._entry_atr if self._entry_atr > 0 else max(snapshot.atr, 1.0)
        is_long = position.side == Side.LONG

        def close(reason):
            return Signal(direction=SignalDirection.CLOSE, strength=1.0,
                          stop_loss=price, take_profit=price, reason=reason, source=self.name)

        if self.max_loss_twd > 0:
            loss_pts = (position.entry_price - price) if is_long else (price - position.entry_price)
            if loss_pts > 0 and loss_pts * position.quantity * self.point_value >= self.max_loss_twd:
                return close(f"金額止損 {loss_pts:.0f}pts")

        # 夜盤盤末強平:用引擎每 tick 餵的當下時間(snapshot.timestamp),不可用 self._bar_time
        # ——進場後 engine 只在無倉時呼叫 on_kbar,_bar_time 會凍結在進場 bar,時間型強平永不觸發。
        if snapshot.timestamp is not None:
            now_t = snapshot.timestamp.time()
            if self.force_close_after <= now_t <= self.force_close_until:
                return close("夜盤盤末強平")

        if position.bars_since_entry >= self.max_hold_bars:
            return close(f"時間出場 {position.bars_since_entry}根")

        if is_long:
            self._trail_best = max(self._trail_best, price)
            if (price - position.entry_price) / atr >= self.trail_trigger_atr:
                stop = self._trail_best - self.trail_dist_atr * atr
                if price <= stop:
                    return close(f"追蹤出場 {stop:.0f}")
        else:
            self._trail_best = min(self._trail_best, price)
            if (position.entry_price - price) / atr >= self.trail_trigger_atr:
                stop = self._trail_best + self.trail_dist_atr * atr
                if price >= stop:
                    return close(f"追蹤出場 {stop:.0f}")
        return None

    def get_parameters(self) -> dict:
        return {"mode": self.mode, "or_bars": self.or_bars, "buf_atr": self.buf_atr,
                "min_or_atr": self.min_or_atr, "max_or_atr": self.max_or_atr,
                "sl_atr": self.sl_atr, "max_hold_bars": self.max_hold_bars}

    def reset(self):
        self._cur_sess = None
        self._or_hi = self._or_lo = 0.0
        self._or_n = 0
        self._or_ready = self._traded = self._skip_logged = False
        self._entry_atr = self._trail_best = 0.0
        self._bar_time = None


class NightGapORBStrategy(NightORBStrategy):
    """v4:依夜盤開盤跳空大小條件化 ORB 方向(文獻線①)。
    gap = 夜盤 15:00 開盤 − 前日 13:45 收盤;|gap|/ATR ≥ gap_thr → 大跳空走順勢突破,
    否則小跳空走 fade(均值回歸)。gap_pts 為 {session_date: gap點數} 字典(外部注入)。
    """

    def __init__(self, gap_thr: float = 0.5, gap_pts: dict = None, **kw):
        self.gap_thr = gap_thr
        self._gap_pts = gap_pts or {}
        super().__init__(**kw)

    def on_kbar(self, kbar, snapshot, **kw):
        sess = self._sess_of(kbar.datetime)
        if sess != self._cur_sess:                   # 新的一夜:依跳空決定本夜模式
            atr = snapshot.atr if snapshot.atr > 0 else 1.0
            g = abs(self._gap_pts.get(sess, 0.0)) / atr
            self.mode = "breakout" if g >= self.gap_thr else "fade"
        return super().on_kbar(kbar, snapshot, **kw)
