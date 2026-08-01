"""WaveExec — chips_combo × 波浪 fade 濾網 訊號的「引擎真 tick paper 執行載具」。

決策(combo + 波浪方向 → 政策B 同向跳)留在 scripts/wave_fade_daily.py(cron ~07:00、寫
data/wave_fade/next_signal.json);這支只負責「執行」:讀當天該不該進、哪方向,在日盤開盤窗用
**引擎的真實 tick 成交價**進場(取代 lab OHLC 結算的無滑價版 → 量到真實滑價,handoff §7.3 核心)。

與 ChipsExec 同骨架(濾網已在 producer 套完、side 即政策B結果,本策略政策無關):
- next_signal.json = {trade_date, side('long'/'short'/'flat'), wave_dir, ...}。
- 日盤開盤窗(08:45 真開盤;08:30-08:45 試撮假 tick 不可成交)進 1 口;side=flat 不進。
- −2% 停損(由進場 Signal 的 stop_loss 交給引擎 tick 級硬停)。
- 收盤(force_close 13:30)強平、不過夜。無止盈、無 trail。
- 固定 1 口:靠 launcher RISK_PROFILE=fixed1_paper(max_contracts=1)鎖死。標的 MXF 小台(pv50)。
- TF 須 ≥30:讓 −2% 停損(≈880 點)≈3.5-5×ATR、能過 risk_manager 的 8×ATR gate。
"""
import json
from datetime import date as _date, time
from pathlib import Path
from typing import Optional

from strategy.base import BaseStrategy, Signal, SignalDirection
from core.market_data import KBar, MarketSnapshot
from core.position import Position, Side

ROOT = Path(__file__).resolve().parent.parent
SIGNAL_FILE = ROOT / "data" / "wave_fade" / "next_signal.json"


class WaveExecStrategy(BaseStrategy):
    def __init__(self, stop_pct: float = 0.02, point_value: float = 10.0,
                 session_start: tuple = (8, 45), entry_window_end: tuple = (9, 30),
                 force_close: tuple = (13, 30), max_loss_twd: float = 0.0):
        self.stop_pct = stop_pct
        self.point_value = point_value
        self.session_start = time(*session_start)
        self.entry_window_end = time(*entry_window_end)
        self.force_close = time(*force_close)
        self.max_loss_twd = max_loss_twd      # 0=不啟用(−2% 已由引擎硬停)
        self.reset()

    @property
    def name(self) -> str:
        return "WaveExec"

    def _read_signal(self) -> Optional[dict]:
        try:
            return json.loads(SIGNAL_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _read_signal_cached(self) -> Optional[dict]:
        """2s TTL 快取(tick 級進場每 tick 都會查;訊號檔當日 ~07:00 cron 寫好、盤中不變)。"""
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
        if not sig:
            return None
        # 訊號日期配對:今天 == trade_date,或 trade_date 在 ≤2 天前(容忍假日位移)。
        # 絕不提前交易(sess < td 跳過)。
        try:
            td = _date.fromisoformat(str(sig.get("trade_date", "")))
        except ValueError:
            return None
        if sess < td or (sess - td).days > 2:
            return None                          # 今天沒有對應訊號(非交易日/尚未算/訊號過期)

        side = sig.get("side")
        if side == "long":
            direction = SignalDirection.BUY
            sl = price * (1 - self.stop_pct)
        elif side == "short":
            direction = SignalDirection.SELL
            sl = price * (1 + self.stop_pct)
        else:                                    # flat:今天不交易(政策B 同向跳 / |combo|<0.5)
            self._traded = True
            return None

        self._traded = True
        wd = sig.get("wave_dir")
        return Signal(direction=direction, strength=0.7, stop_loss=round(sl, 1),
                      take_profit=0.0,
                      reason=f"wave {side} combo{sig.get('combo')} 波浪{wd} 政策{sig.get('policy')}",
                      source=self.name)

    def check_entry_tick(self, snapshot: MarketSnapshot) -> Optional[Signal]:
        """tick 級進場(引擎無倉時每 tick 呼叫):08:45 開盤第一筆 tick 就能進,
        不必等首根 30m K 收盤(09:00)→ 更貼研究口徑(T+1 開盤價)。"""
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
            return close("wave 收盤平倉")
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
