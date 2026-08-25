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

from loguru import logger
from strategy.base import BaseStrategy, Signal, SignalDirection
from core.market_data import KBar, MarketSnapshot
from core.position import Position, Side

ROOT = Path(__file__).resolve().parent.parent
SIGNAL_FILE = ROOT / "data" / "wave_fade" / "next_signal.json"
# 今天已推播過的告警 token(每行一個 "<日期>|<種類>";盤中重啟不重推)
ALERT_MARK = ROOT / "data" / "wave_fade" / "alerts_sent.txt"


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

    def _alert_once(self, sess, kind, msg):
        """同一天同一種告警只推一次(TG)。

        兩層去重:①行程內 set ②marker 檔(盤中被 watchdog 重啟也不會重推)。
        marker 只保留當天的 token,不會無限長大。全程 try/except 吞掉 —— 通知失敗
        絕不影響交易主流程。
        """
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
            return                                      # 今天推過了(這次是重啟)
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

    def _alert_stale_once(self, sess, td, side, skipped: bool):
        """訊號 trade_date ≠ 今天 → 推 TG(可能是假日位移,也可能是 producer 沒跑/資料源慢)。"""
        act = "已跳過不交易" if skipped else "仍照常執行(≤2天容忍),請人工確認是否為資料源未更新"
        self._alert_once(sess, "stale",
                         f"⚠️ [wave_exec] 訊號疑似過期:trade_date={td} ≠ 今天 {sess}"
                         f"(side={side})→ {act}。檢查 08:05 的 wave_fade_daily.py"
                         f"(data/logs/wave_fade_daily.log)。")

    def _notify_flat_once(self, sess, sig):
        """side=flat(今天不進場)→ 推一則 TG。

        Why(2026-08-24):空手日引擎既不留 log 也不推播,於是「今天訊號叫我別進」和
        「引擎根本沒跑」在使用者端是**同一種沉默** —— 8/24 就因此被誤判成引擎掛了
        (實際是政策B 籌碼多×波浪多 同向跳)。有進場推、沒進場也推,沉默才回歸「異常」。
        """
        _d = {1: "多", -1: "空", 0: "不表態"}
        cd, wd, pol, cb = (sig.get("combo_dir"), sig.get("wave_dir"),
                           sig.get("policy", "?"), sig.get("combo"))
        cb_txt = f"{cb:+.2f}" if isinstance(cb, (int, float)) else str(cb)
        if cd == 0:
            why = f"籌碼未達門檻 combo {cb_txt}"
        elif cd is not None and cd == wd:
            why = f"政策{pol}:籌碼{_d.get(cd, cd)}(combo {cb_txt})×波浪{_d.get(wd, wd)} 同向跳"
        else:
            why = f"政策{pol}:籌碼{_d.get(cd, cd)}(combo {cb_txt})×波浪{_d.get(wd, wd)}"
        # 不標 [PAPER]/[LIVE]:引擎的進出場標籤是在 core/engine.py 兩條分支各自寫死的,
        # 不看 TRADING_MODE;而 core.notify 的 load_dotenv() 會把 .env 的 TRADING_MODE
        # 灌進 os.environ(2026-08-24 演練就因此印成 [LIVE])。寧可不標也不要標錯。
        self._alert_once(sess, "flat", f"⚪ wave_exec {sess} 空手({why})")

    def _entry_decision(self, sess, bt, price) -> Optional[Signal]:
        """進場判斷(on_kbar 與 check_entry_tick 共用;_traded 防重複進場)。"""
        if sess != self._cur_sess:               # 換日重置
            self._cur_sess = sess
            self._traded = False
            self._logged_sess = None

        def _log_once(msg):
            # 每 session 只印一次(此函式每 tick 被呼叫,避免洗版);只在進場窗內印
            if getattr(self, "_logged_sess", None) != sess and self.session_start <= bt < self.entry_window_end:
                self._logged_sess = sess
                logger.info(f"[wave_exec] {sess} 訊號讀取 → {msg}")

        if self._traded or bt < self.session_start or bt >= self.entry_window_end:
            return None

        sig = self._read_signal_cached()
        if not sig:
            _log_once("無 next_signal.json → 跳過")
            self._alert_once(sess, "nosig",
                             f"🔴 [wave_exec] {sess} 讀不到 next_signal.json → 今天不會進場。"
                             f"檢查 08:05 的 wave_fade_daily.py 有沒有跑成功"
                             f"(data/logs/wave_fade_daily.log)。")
            return None
        # 訊號日期配對:今天 == trade_date,或 trade_date 在 ≤2 天前(容忍假日位移)。
        # 絕不提前交易(sess < td 跳過)。
        _sd, _cb, _tdraw = sig.get("side"), sig.get("combo"), sig.get("trade_date", "")
        try:
            td = _date.fromisoformat(str(_tdraw))
        except ValueError:
            _log_once(f"trade_date 格式錯({_tdraw}) → 跳過")
            self._alert_once(sess, "badtd",
                             f"🔴 [wave_exec] {sess} next_signal.json 的 trade_date 格式錯"
                             f"({_tdraw!r}) → 今天不會進場。")
            return None
        if sess < td:
            _log_once(f"trade_date={td} side={_sd} combo={_cb} → 未到交易日、跳過")
            return None                          # 訊號是未來日期:絕不提前交易(非異常,不推)
        if (sess - td).days > 2:
            _log_once(f"trade_date={td} side={_sd} combo={_cb} → 訊號過期({(sess - td).days}天>2)、跳過")
            self._alert_stale_once(sess, td, _sd, skipped=True)
            return None
        if td < sess:
            # 容忍區(1-2 天)照常執行(假日位移是合法情境),但推 TG 供人工判斷 ——
            # 2026-07-01 chips 案例:資料源慢→訊號停在前一日→引擎靜默空手、錯過單,無人知曉。
            self._alert_stale_once(sess, td, _sd, skipped=False)

        side = _sd
        if side == "long":
            direction = SignalDirection.BUY
            sl = price * (1 - self.stop_pct)
        elif side == "short":
            direction = SignalDirection.SELL
            sl = price * (1 + self.stop_pct)
        else:                                    # flat:今天不交易(政策B 同向跳 / |combo|<0.5)
            _log_once(f"trade_date={td} side=flat combo={_cb} 波浪{sig.get('wave_dir')} "
                      f"政策{sig.get('policy')} → 空手不進場")
            self._notify_flat_once(sess, sig)
            self._traded = True
            return None

        wd = sig.get("wave_dir")
        _log_once(f"trade_date={td} side={side} combo={_cb} 波浪{wd} → 進場 {side} "
                  f"@ {price:.0f} 停損 {round(sl, 1)}")
        self._traded = True
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
        self._logged_sess = None
        self._alerted = set()
        self._bar_time = None
        self._sig_cache = None
        self._sig_cache_at = 0.0
