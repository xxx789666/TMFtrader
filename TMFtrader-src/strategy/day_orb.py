"""DayORBStrategy — 日盤開盤區間(ORB)策略,v7(低頻趨勢)的高頻互補。

日盤 08:45 開盤,頭 or_bars 根定開盤區間;突破上/下緣→順勢進場(夜盤 ORB 已證 edge,
日盤開盤同理)。OR 寬度/ADX 過濾 + 每日 1 筆。出場:ATR trail + 時間止損 + 金額硬停損
+ 13:25 盤末強平。支援 breakout/fade 兩模式。
"""
from datetime import time
from typing import Optional

from strategy.base import BaseStrategy, Signal, SignalDirection
from core.market_data import KBar, MarketSnapshot
from core.position import Position, Side


class DayORBStrategy(BaseStrategy):
    def __init__(self, mode: str = "breakout", or_bars: int = 6,
                 buf_atr: float = 0.10, min_or_atr: float = 0.5, max_or_atr: float = 6.0,
                 min_adx: float = 0.0, sl_atr: float = 1.5, tp_atr: float = 4.0,
                 trail_trigger_atr: float = 1.0, trail_dist_atr: float = 1.2,
                 max_hold_bars: int = 24, max_loss_twd: float = 4000.0, point_value: float = 10.0,
                 force_close: tuple = (13, 25),
                 session_start: tuple = (8, 30), session_end: tuple = (13, 45)):
        self.mode = mode; self.or_bars = or_bars; self.buf_atr = buf_atr
        self.min_or_atr = min_or_atr; self.max_or_atr = max_or_atr; self.min_adx = min_adx
        self.sl_atr = sl_atr; self.tp_atr = tp_atr
        self.trail_trigger_atr = trail_trigger_atr; self.trail_dist_atr = trail_dist_atr
        self.max_hold_bars = max_hold_bars; self.max_loss_twd = max_loss_twd
        self.point_value = point_value; self.force_close = time(*force_close)
        # 日盤時段窗口:08:45 開盤落在 clock-aligned 的 08:30 30m bucket,故起點設 08:30。
        # 盤外(夜盤/盤間)bar 不建 OR、不進場,避免 24h 餵 bar 時 OR 在半夜換日錨到夜盤。
        self.session_start = time(*session_start)
        self.session_end = time(*session_end)
        self.reset()

    @property
    def name(self) -> str:
        return f"DayORB-{self.mode}"

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

        # ── 日盤時段 gate ────────────────────────────────────────────
        # 只處理日盤 08:30–13:45 的 bar;盤外 bar 一律忽略。這讓 OR 永遠
        # 從日盤開盤起算,而非 24h 餵 bar 時在 00:00 換日錨到夜盤 bar。
        # 日盤不跨午夜,故 gate 後用 date() 當 session key 即正確。
        bt = kbar.datetime.time()
        if bt < self.session_start or bt >= self.session_end:
            return None

        sess = kbar.datetime.date()                  # 日盤不跨午夜 → 日曆日即交易日

        if sess != self._cur_sess:
            self._cur_sess = sess
            self._or_hi, self._or_lo, self._or_n = kbar.high, kbar.low, 1
            self._traded = self._or_ready = False
            return None
        if self._or_n < self.or_bars:
            self._or_hi = max(self._or_hi, kbar.high)
            self._or_lo = min(self._or_lo, kbar.low)
            self._or_n += 1
            if self._or_n >= self.or_bars:
                self._or_ready = True
            return None
        if self._traded or not self._or_ready:
            return None
        orw = self._or_hi - self._or_lo
        if orw < self.min_or_atr * atr or orw > self.max_or_atr * atr:
            return None
        if snapshot.adx < self.min_adx:
            return None

        price = snapshot.price
        up = price > self._or_hi + self.buf_atr * atr
        dn = price < self._or_lo - self.buf_atr * atr
        sig = None
        if self.mode == "breakout":
            if up:
                sig = self._mk(SignalDirection.BUY, price, atr, "DayORB-LONG")
            elif dn:
                sig = self._mk(SignalDirection.SELL, price, atr, "DayORB-SHORT")
        else:
            if up:
                sig = self._mk(SignalDirection.SELL, price, atr, "DayORBfade-SHORT")
            elif dn:
                sig = self._mk(SignalDirection.BUY, price, atr, "DayORBfade-LONG")
        if sig is not None:
            self._traded = True
            self._entry_atr = atr
            self._trail_best = price
            self._trail_armed = False
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
        # 盤末強平用引擎每 tick 餵的當下時間(snapshot.timestamp),不可用 self._bar_time。
        # 進場後 engine 只在無倉時呼叫 on_kbar(_process_kbar L1118),_bar_time 會凍結在
        # 進場那根 bar,時間型強平永不觸發 → 倉位裸抱過 13:45 收盤、漂到夜盤撮合吃跳空。
        # 日盤同一日曆日,>= 即可(無跨午夜)。
        if snapshot.timestamp is not None and snapshot.timestamp.time() >= self.force_close:
            # 同一類別供 day_orb(13:30 日盤)與 aft_orb(23:30 傍晚)共用 → reason 依 force_close 時間標,
            # 別一律寫「日盤」(aft_orb 23:30 收會誤標日盤)。
            return close("日盤盤末強平" if self.force_close.hour < 14 else "傍晚盤末強平")
        if position.bars_since_entry >= self.max_hold_bars:
            return close(f"時間出場 {position.bars_since_entry}根")
        # trail arming 用 latch:獲利「曾」達 trigger 即武裝(_trail_armed)、之後從最佳點反彈 dist
        # 即出。原本用「當下」獲利≥trigger 判、未 latch → 反彈到出場線時當下獲利已縮回 <trigger、
        # 條件失效 → 等價於要 ≥(trigger+dist)×ATR 才可能觸發、trail 幾乎不動(2026-06-05 發現)。
        # latch 後 lab realism:PF↑、DD↓2~3%、7/7 全正維持(compare_aft_trail_latch.py)。
        if is_long:
            self._trail_best = max(self._trail_best, price)
            if (price - position.entry_price) / atr >= self.trail_trigger_atr:
                self._trail_armed = True
            if self._trail_armed and price <= self._trail_best - self.trail_dist_atr * atr:
                return close(f"追蹤出場 {self._trail_best - self.trail_dist_atr * atr:.0f}")
        else:
            self._trail_best = min(self._trail_best, price)
            if (position.entry_price - price) / atr >= self.trail_trigger_atr:
                self._trail_armed = True
            if self._trail_armed and price >= self._trail_best + self.trail_dist_atr * atr:
                return close(f"追蹤出場 {self._trail_best + self.trail_dist_atr * atr:.0f}")
        return None

    def get_parameters(self) -> dict:
        return {"mode": self.mode, "or_bars": self.or_bars, "min_adx": self.min_adx}

    def reset(self):
        self._cur_sess = None
        self._or_hi = self._or_lo = 0.0
        self._or_n = 0
        self._or_ready = self._traded = False
        self._entry_atr = self._trail_best = 0.0
        self._trail_armed = False
        self._bar_time = None
