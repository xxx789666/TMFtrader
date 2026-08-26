"""NightBExec — chips_combo「夜盤變體 B」的引擎真 tick paper 執行載具(2026-08-26 升級)。

取代 chips_night_snapshot.py 的紙上 tape(18:36 snapshot 進、隔日 OHLC 結算、夜盤停損未模擬):
user 2026-08-26 拍板直接升真 tick — 夜盤時段的 −2% 停損自此為真(紙上版最大的洞)。

口徑(對齊紙上版,差異=真 tick 成交+全程停損):
- 訊號 = data/chips_combo/next_signal.json(chips_combo_daily.py cron 18:30 寫,trade_date=下一交易日)。
- 訊號夜 18:36–19:30 進場窗(18:36 對齊紙上版 snapshot 時點)真 tick 進 1 口;side=flat 不進。
- 持倉過夜(週五訊號=過週末),trade_date 當日 13:30 強平(2026-07-16「一律 13:30」定版)。
- −2% 停損由進場 Signal.stop_loss 交引擎 tick 級硬停 —— 夜盤+日盤全程有效。
- 固定 1 口:launcher RISK_PROFILE=fixed1_paper。標的 MXF 小台。TF≥30(過 8×ATR gate)。
- 重啟防重進:entered marker 檔(同晚停損出場後重啟不得再進;跨重啟持倉靠引擎持倉恢復)。
"""
import json
import os
from datetime import date as _date, time
from pathlib import Path
from typing import Optional

from loguru import logger
from strategy.base import BaseStrategy, Signal, SignalDirection
from core.market_data import KBar, MarketSnapshot
from core.position import Position, Side

ROOT = Path(__file__).resolve().parent.parent
SIGNAL_FILE = ROOT / "data" / "chips_combo" / "next_signal.json"
DDIR = ROOT / "data" / "night_b"
ALERT_MARK = DDIR / "alerts_sent.txt"
ENTER_MARK = DDIR / "entered.txt"      # 每行 "<trade_date>":該訊號已進過場(重啟/停損後不重進)
OWNER = os.getenv("STRATEGY_OWNER", "night_b_exec")


class NightBExecStrategy(BaseStrategy):
    def __init__(self, stop_pct: float = 0.02, point_value: float = 10.0,
                 entry_start: tuple = (18, 36), entry_end: tuple = (19, 30),
                 settle_close: tuple = (13, 30), max_loss_twd: float = 0.0):
        self.stop_pct = stop_pct
        self.point_value = point_value
        self.entry_start = time(*entry_start)
        self.entry_end = time(*entry_end)
        self.settle_close = time(*settle_close)
        self.max_loss_twd = max_loss_twd      # 0=不啟用(−2% 已由引擎硬停)
        self.reset()

    @property
    def name(self) -> str:
        return "NightBExec"

    # ── 訊號讀取(2s TTL 快取,同 chips_exec) ──
    def _read_signal_cached(self) -> Optional[dict]:
        import time as _t
        now = _t.monotonic()
        if now - getattr(self, "_sig_cache_at", 0.0) > 2.0:
            try:
                self._sig_cache = json.loads(SIGNAL_FILE.read_text(encoding="utf-8"))
            except Exception:
                self._sig_cache = None
            self._sig_cache_at = now
        return self._sig_cache

    # ── 告警(同 wave/chips:①行程內 set ②marker 檔,一天一種一次,失敗全吞) ──
    def _alert_once(self, sess, kind, msg):
        tok = f"{sess}|{kind}"
        seen = getattr(self, "_alerted", None)
        if seen is None:
            seen = self._alerted = set()
        if tok in seen:
            return
        seen.add(tok)
        try:
            done = ALERT_MARK.read_text(encoding="utf-8").split() if ALERT_MARK.exists() else []
        except Exception:
            done = []
        if tok in done:
            return
        try:
            from core.notify import tg
            tg(msg)
        except Exception:
            pass
        try:
            ALERT_MARK.parent.mkdir(parents=True, exist_ok=True)
            ALERT_MARK.write_text(
                "\n".join([d for d in done if d.startswith(f"{sess}|")] + [tok]),
                encoding="utf-8")
        except Exception:
            pass

    # ── entered marker(防「同晚停損出場→重啟→視為無倉→窗內重進」;只留最近 30 行) ──
    def _entered(self, td) -> bool:
        try:
            return str(td) in ENTER_MARK.read_text(encoding="utf-8").split()
        except Exception:
            return False

    def _mark_entered(self, td):
        try:
            ENTER_MARK.parent.mkdir(parents=True, exist_ok=True)
            lines = []
            if ENTER_MARK.exists():
                lines = ENTER_MARK.read_text(encoding="utf-8").split()
            lines = (lines + [str(td)])[-30:]
            ENTER_MARK.write_text("\n".join(lines), encoding="utf-8")
        except Exception:
            pass

    def _last_entered_td(self):
        """重啟後 check_exit 需要 trade_date(行程內 _pos_td 已失)→ 從 marker 撈最後一筆。"""
        try:
            lines = ENTER_MARK.read_text(encoding="utf-8").split()
            return _date.fromisoformat(lines[-1]) if lines else None
        except Exception:
            return None

    # ── 進場 ──
    def _entry_decision(self, now_d, bt, price) -> Optional[Signal]:
        if now_d != self._cur_sess:              # 換日重置
            self._cur_sess = now_d
            self._traded = False
            self._logged_sess = None

        def _log_once(msg):
            if getattr(self, "_logged_sess", None) != now_d and self.entry_start <= bt < self.entry_end:
                self._logged_sess = now_d
                logger.info(f"[{OWNER}] {now_d} 訊號讀取 → {msg}")

        if self._traded or bt < self.entry_start or bt >= self.entry_end:
            return None

        sig = self._read_signal_cached()
        if not sig:
            _log_once("無 next_signal.json → 跳過")
            self._alert_once(now_d, "nosig",
                             f"🔴 [{OWNER}] {now_d} 讀不到 next_signal.json → 今晚不會進場。"
                             f"檢查 18:30 的 chips_combo_daily.py(data/logs/chips_combo.log)。")
            return None
        _sd, _cb, _tdraw = sig.get("side"), sig.get("combo"), sig.get("trade_date", "")
        try:
            td = _date.fromisoformat(str(_tdraw))
        except ValueError:
            _log_once(f"trade_date 格式錯({_tdraw}) → 跳過")
            self._alert_once(now_d, "badtd",
                             f"🔴 [{OWNER}] {now_d} next_signal.json 的 trade_date 格式錯"
                             f"({_tdraw!r}) → 今晚不會進場。")
            return None
        # 只做「訊號夜」:trade_date 必須在未來(今晚 18:30 剛寫的訊號)。td<=今天=舊訊號,絕不補進。
        # 週五夜 td=週一 diff=3;夾國定假日再+1-2 → 容忍 ≤4 天,超過視為異常過期。
        dd = (td - now_d).days
        if dd <= 0:
            _log_once(f"trade_date={td} 非今晚訊號(舊)→ 跳過")
            self._alert_once(now_d, "stale",
                             f"⚠️ [{OWNER}] {now_d} 訊號過期(trade_date={td})→ 今晚不進。"
                             f"18:30 的 chips_combo_daily 可能沒跑成功。")
            return None
        if dd > 4:
            _log_once(f"trade_date={td} 距今 {dd} 天(>4)→ 異常、跳過")
            self._alert_once(now_d, "fartd",
                             f"⚠️ [{OWNER}] {now_d} 訊號 trade_date={td} 距今 {dd} 天(>4)異常 → 今晚不進。")
            return None
        if self._entered(td):
            self._traded = True                  # 這個訊號已進過(重啟/停損後禁止重進)
            return None

        if _sd == "long":
            direction = SignalDirection.BUY
            sl = price * (1 - self.stop_pct)
        elif _sd == "short":
            direction = SignalDirection.SELL
            sl = price * (1 + self.stop_pct)
        else:                                    # flat:今晚不交易
            cb_txt = f"{_cb:+.2f}" if isinstance(_cb, (int, float)) else str(_cb)
            _log_once(f"trade_date={td} side=flat combo={_cb} → 空手不進場")
            self._alert_once(now_d, "flat", f"⚪ {OWNER} {now_d} 夜盤空手(combo {cb_txt} 未達門檻 ±0.5)")
            self._traded = True
            return None

        _log_once(f"trade_date={td} side={_sd} combo={_cb} → 夜盤進場 {_sd} "
                  f"@ {price:.0f} 停損 {round(sl, 1)}(持倉至 {td} 13:30)")
        self._traded = True
        self._pos_td = td
        self._mark_entered(td)
        return Signal(direction=direction, strength=0.7, stop_loss=round(sl, 1),
                      take_profit=0.0,
                      reason=f"night_b {_sd} combo{_cb}(夜{now_d}進、{td} 13:30 平)",
                      source=self.name)

    def check_entry_tick(self, snapshot: MarketSnapshot) -> Optional[Signal]:
        """tick 級進場:18:36 後第一筆 tick 就能進(對齊紙上版 snapshot 時點)。"""
        ts = snapshot.timestamp
        px = snapshot.price
        if ts is None or px <= 0:
            return None
        return self._entry_decision(ts.date(), ts.time(), px)

    def on_kbar(self, kbar: KBar, snapshot: MarketSnapshot, **kw) -> Optional[Signal]:
        self._bar_time = kbar.datetime
        return self._entry_decision(kbar.datetime.date(), kbar.datetime.time(), snapshot.price)

    # ── 出場:trade_date 當日(或之後首個交易日)13:30 強平;−2% 停損由引擎硬停 ──
    def check_exit(self, position: Position, snapshot: MarketSnapshot) -> Optional[Signal]:
        price = snapshot.price
        ts = snapshot.timestamp
        if ts is None:
            return None
        is_long = position.side == Side.LONG

        def close(reason):
            return Signal(direction=SignalDirection.CLOSE, strength=1.0,
                          stop_loss=price, take_profit=price, reason=reason, source=self.name)

        if self.max_loss_twd > 0:
            loss_pts = (position.entry_price - price) if is_long else (price - position.entry_price)
            if loss_pts > 0 and loss_pts * position.quantity * self.point_value >= self.max_loss_twd:
                return close(f"金額止損 {loss_pts:.0f}pts")
        td = getattr(self, "_pos_td", None) or self._last_entered_td()
        if td is None:
            # 不該發生(有倉必有 marker):fail-safe 在日盤 13:30-15:00 窗強平,避免無限持倉
            if self.settle_close <= ts.time() < time(15, 0):
                return close("night_b 收盤平倉(trade_date 遺失 fail-safe)")
            return None
        # 夜盤跨午夜:進場晚 18:36(date=T)→ 午夜後 date=T+1 但仍在夜盤時段(<05:00)不平;
        # trade_date 當日日盤 13:30 起強平(15:00 後的 tick 也 >=13:30,屬備援不影響)。
        if ts.date() >= td and ts.time() >= self.settle_close and ts.time() < time(15, 0):
            return close("night_b 收盤平倉")
        return None

    def get_parameters(self) -> dict:
        return {"stop_pct": self.stop_pct, "entry_window": f"{self.entry_start}-{self.entry_end}",
                "settle_close": str(self.settle_close)}

    def reset(self):
        self._cur_sess = None
        self._traded = False
        self._logged_sess = None
        self._alerted = set()
        self._bar_time = None
        self._pos_td = None
        self._sig_cache = None
        self._sig_cache_at = 0.0
