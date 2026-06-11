"""ChipsExec — chips_combo 訊號的「引擎真 tick paper 執行載具」。

訊號計算(外資 flow + 大戶 ratio → combo,HTTP 籌碼資料)留在 scripts/chips_combo_daily.py
(cron 18:30 算、寫 data/chips_combo/next_signal.json);這支策略只負責「執行」:讀當天該不該進、
哪個方向,在日盤開盤窗用**引擎的真實 tick 成交價**進場(取代 CSV 版用日 K open/close 的無滑價結算)。

規則(對齊 chips_combo 凍結配置):
- next_signal.json = {trade_date, side('long'/'short'/'flat'), combo, ...}。
- 日盤開盤窗(預設首根 30m bar ~09:00)進 1 口;side=flat 不進。
- −2% 停損(由進場 Signal 的 stop_loss 交給引擎 tick 級硬停)。
- 收盤(force_close 13:44)強平、不過夜。無止盈、無 trail。
- 固定 1 口:靠 launcher 的 RISK_PROFILE=fixed1_paper(max_contracts=1)鎖死。

⚠️ 與 CSV 版的差異(會記錄、屬預期):進場是「開盤窗首根 30m bar ~09:00」的真實 tick 價,
   非 08:45 開盤價;這正是要驗的「真實可成交價 + 滑價序列」。TF 須 ≥30 讓 ATR 夠大、
   −2% 停損(~880點≈3.5-5×ATR)能過 risk_manager 的 8×ATR gate(TF 太小 ATR 小會被拒單)。
"""
import json
from datetime import time
from pathlib import Path
from typing import Optional

from strategy.base import BaseStrategy, Signal, SignalDirection
from core.market_data import KBar, MarketSnapshot
from core.position import Position, Side

ROOT = Path(__file__).resolve().parent.parent
SIGNAL_FILE = ROOT / "data" / "chips_combo" / "next_signal.json"


class ChipsExecStrategy(BaseStrategy):
    def __init__(self, stop_pct: float = 0.02, point_value: float = 10.0,
                 session_start: tuple = (8, 30), entry_window_end: tuple = (9, 30),
                 force_close: tuple = (13, 44), max_loss_twd: float = 0.0):
        self.stop_pct = stop_pct
        self.point_value = point_value
        self.session_start = time(*session_start)
        self.entry_window_end = time(*entry_window_end)
        self.force_close = time(*force_close)
        self.max_loss_twd = max_loss_twd      # 0=不啟用(−2% 已由引擎硬停)
        self.reset()

    @property
    def name(self) -> str:
        return "ChipsExec"

    def _read_signal(self) -> Optional[dict]:
        try:
            return json.loads(SIGNAL_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _read_signal_cached(self) -> Optional[dict]:
        """2s TTL 快取(tick 級進場每 tick 都會查;訊號檔前一晚 18:30 cron 寫好、盤中不變)。"""
        import time as _t
        now = _t.monotonic()
        if now - getattr(self, "_sig_cache_at", 0.0) > 2.0:
            self._sig_cache = self._read_signal()
            self._sig_cache_at = now
        return self._sig_cache

    def _entry_decision(self, sess, bt, price) -> Optional[Signal]:
        """進場判斷(on_kbar 與 check_entry_tick 共用;_traded 防重複進場)。"""
        if sess != self._cur_sess:               # 換日重置
            self._cur_sess = sess
            self._traded = False
        if self._traded or bt < self.session_start or bt >= self.entry_window_end:
            return None

        sig = self._read_signal_cached()
        if not sig or sig.get("trade_date") != sess.isoformat():
            return None                          # 今天沒有對應訊號(非交易日/尚未算)

        side = sig.get("side")
        if side == "long":
            direction = SignalDirection.BUY
            sl = price * (1 - self.stop_pct)
        elif side == "short":
            direction = SignalDirection.SELL
            sl = price * (1 + self.stop_pct)
        else:                                    # flat:今天不交易,標記避免整段重讀
            self._traded = True
            return None

        self._traded = True
        return Signal(direction=direction, strength=0.7, stop_loss=round(sl, 1),
                      take_profit=0.0, reason=f"chips {side} combo{sig.get('combo')}",
                      source=self.name)

    def check_entry_tick(self, snapshot: MarketSnapshot) -> Optional[Signal]:
        """tick 級進場(引擎無倉時每 tick 呼叫):08:45 開盤第一筆 tick 就能進,
        不必等首根 30m K 收盤(09:00)→ 更貼研究口徑(T+1 開盤價)。與 on_kbar 共用
        _entry_decision/_traded,tick 先進了 on_kbar 就不會重進。"""
        ts = snapshot.timestamp
        px = snapshot.price
        if ts is None or px <= 0:
            return None
        return self._entry_decision(ts.date(), ts.time(), px)

    def on_kbar(self, kbar: KBar, snapshot: MarketSnapshot, **kw) -> Optional[Signal]:
        self._bar_time = kbar.datetime
        # K 棒收盤路徑(備援;正常 tick 路徑已在 08:45 先進)
        return self._entry_decision(kbar.datetime.date(), kbar.datetime.time(), snapshot.price)

    def check_exit(self, position: Position, snapshot: MarketSnapshot) -> Optional[Signal]:
        price = snapshot.price
        is_long = position.side == Side.LONG

        def close(reason):
            return Signal(direction=SignalDirection.CLOSE, strength=1.0,
                          stop_loss=price, take_profit=price, reason=reason, source=self.name)

        if self.max_loss_twd > 0:
            loss_pts = (position.entry_price - price) if is_long else (price - position.entry_price)
            if loss_pts > 0 and loss_pts * position.quantity * self.point_value >= self.max_loss_twd:
                return close(f"金額止損 {loss_pts:.0f}pts")
        # 收盤強平:用引擎每 tick 餵的 snapshot.timestamp(不可用 _bar_time,進場後會凍結)。
        if snapshot.timestamp is not None and snapshot.timestamp.time() >= self.force_close:
            return close("chips 收盤平倉")
        return None

    def get_parameters(self) -> dict:
        return {"stop_pct": self.stop_pct, "entry_window_end": str(self.entry_window_end),
                "force_close": str(self.force_close)}

    def reset(self):
        self._cur_sess = None
        self._traded = False
        self._bar_time = None
        self._sig_cache = None
        self._sig_cache_at = 0.0
