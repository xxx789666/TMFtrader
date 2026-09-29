"""AStructNightgate — A_struct 夜盤閘門法 的引擎真 tick paper 執行器。

回應 Day-1「下跌日逆勢做多」:lab 用「夜盤波浪結構閘門 + 當日 08:45 低錨」解掉。
偵測/閘門/破壞位/人工 Discord 審核在本機 lab 管線(Astruct_daily.py + Discord);本檔只做
「執行」——讀人工審核後的當日決定(橋接推來的 next_signal.json),08:45 用即時開盤判閘門,
過閘門才用 first_wave_anchored(浪0=08:45 低錨)即時掃 0-1-2,浪2 確認真 tick 進 1 口。

決定檔 data/astruct_nightgate/next_signal.json(橋接 Astruct_bridge.py ~08:35 推到 VPS):
  {trade_date, action('go'|'skip'), dir('空'|'多'), break_lvl, asof, source}

閘門(只做多,移植 Astruct_exec.py):
  dir 空(-1):開盤 >  break_lvl → 空頭破壞翻多、啟動偵測;開盤 ≤ → 不做。
  dir 多(+1):開盤 ≥  break_lvl → 多頭延續、啟動偵測;開盤 <  → 不做。
日內(移植 Astruct_nightgate.first_wave_anchored):浪0=08:45 首根低;w1=其後首個 H pivot
  (攻頂前低不破 w0);w2=w1 後首個 L pivot;w0<w2<w1、L1≥0.15%、ret2∈[20,80]%;
  浪2 確認當根收盤市價進,TP=浪2+1.618×L1、SL=浪2×(1−0.15%),13:45 強平、一天一筆。
TF=1(對齊 lab 1分K 偵測);固定 1 口靠 RISK_PROFILE=fixed1_paper;標的 MXF。
"""
import json
from datetime import time, date as _date
from pathlib import Path
from typing import Optional, List

from loguru import logger

from strategy.base import BaseStrategy, Signal, SignalDirection
from core.market_data import KBar, MarketSnapshot
from core.position import Position, Side

ROOT = Path(__file__).resolve().parent.parent
SIGNAL_FILE = ROOT / "data" / "astruct_nightgate" / "next_signal.json"

FIB = 1.618
RET2_LO, RET2_HI = 0.20, 0.80
MINPTS_FRAC = 0.0015
BUF = 0.0015
ZZ_PCT = 0.0030


class AStructNightgateStrategy(BaseStrategy):
    def __init__(self, window_start: tuple = (8, 45), window_end: tuple = (9, 45),
                 force_close: tuple = (13, 45), point_value: float = 50.0):
        self.window_start = time(*window_start)
        self.window_end = time(*window_end)
        self.force_close = time(*force_close)
        self.point_value = point_value
        self.reset()

    @property
    def name(self) -> str:
        return "AStructNightgate"

    def _read_signal(self) -> Optional[dict]:
        try:
            return json.loads(SIGNAL_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _apply_signal(self, sig: dict) -> None:
        """把一筆有效決定套到 pending 狀態(閘門鎖定前每根可被更新的決定覆蓋)。"""
        if sig.get("action") == "go":
            try:
                self._break = float(sig.get("break_lvl"))
                self._dir = -1 if sig.get("dir") == "空" else 1
                self._go = True
            except (TypeError, ValueError):
                self._go = False
        else:                                          # skip
            self._go = False
            self._dir = None
            self._break = None

    # ── 因果 zigzag(忠實移植) ──────────────────────────────────────────────
    def _causal_pivots(self, H: List[float], L: List[float]):
        pct = ZZ_PCT
        piv = []
        trend = 0
        ph = H[0]; phi = 0; pl = L[0]; pli = 0
        for i in range(1, len(H)):
            if trend >= 0:
                if H[i] > ph:
                    ph = H[i]; phi = i
                if L[i] < ph * (1 - pct):
                    piv.append((phi, ph, 'H', i)); trend = -1; pl = L[i]; pli = i
            if trend <= 0:
                if L[i] < pl:
                    pl = L[i]; pli = i
                if H[i] > pl * (1 + pct):
                    piv.append((pli, pl, 'L', i)); trend = 1; ph = H[i]; phi = i
        return piv

    def _first_wave_anchored(self, H: List[float], L: List[float]) -> Optional[dict]:
        """當日錨:w0=08:45 首根低;w1=其後首個 H pivot(攻頂前低不破 w0);w2=w1 後首個 L pivot。
        忠實移植 Astruct_nightgate.first_wave_anchored。"""
        w0 = L[0]
        piv = self._causal_pivots(H, L)
        Hp = [p for p in piv if p[2] == 'H']
        if not Hp:
            return None
        w1p = Hp[0]; w1 = w1p[1]; w1i = w1p[0]
        if w1i >= 1 and min(L[1:w1i + 1]) < w0:        # 攻頂前破開盤低 → 錨不成立
            return None
        Lp = [p for p in piv if p[2] == 'L' and p[0] > w1i]
        if not Lp:
            return None
        w2p = Lp[0]; w2 = w2p[1]
        if not (w0 < w2 < w1):
            return None
        L1 = w1 - w0
        if L1 < w0 * MINPTS_FRAC:
            return None
        ret2 = (w1 - w2) / L1
        if not (RET2_LO <= ret2 <= RET2_HI):
            return None
        return dict(w0=w0, w1=w1, w2=w2, L1=L1, ret2=ret2)

    # ── 進場(on_kbar 增量;TF=1) ────────────────────────────────────────────
    def on_kbar(self, kbar: KBar, snapshot: MarketSnapshot, **kw) -> Optional[Signal]:
        self._bar_time = kbar.datetime
        d = kbar.datetime.date()
        t = kbar.datetime.time()

        if d != self._cur_sess:                          # 換交易日:重置
            self._cur_sess = d
            self.reset_day()

        # 時效防呆:每根重讀決定。人工決定 → 立刻鎖;否則拖到 08:45 閘門時刻才鎖、鎖前的
        # 自動未審可被閘門前抵達的人工 skip/override 覆蓋;晚於閘門才到 → 已鎖 → void。
        # fail-closed:閘門前都沒拿到有效決定 = 不做。(橋接 08:25-45 視窗內持續重推)
        if not self._resolved:
            sig = self._read_signal()
            if (sig and str(sig.get("trade_date")) == d.isoformat()
                    and sig.get("action") in ("go", "skip")):
                self._apply_signal(sig)               # 更新 pending,閘門前可被新決定覆蓋
                self._sig_source = str(sig.get("source", ""))
                self._have_pending = True
            human = self._have_pending and self._sig_source.startswith("人工")
            if human or t >= self.window_start:
                self._resolved = True
                if not self._have_pending:
                    self._go = False                  # 閘門前都沒有效決定 → fail-closed 不做
                lock_why = "人工" if human else ("閘門時刻" if self._have_pending else "閘門時刻/無決定")
                logger.info(f"[AStructNG] {d} 決定鎖定({lock_why}): dir={self._dir} "
                            f"break={self._break} go={self._go} (src={self._sig_source})")

        if not self._go or self._traded:
            return None
        if t < self.window_start or t > self.window_end:
            return None

        # 08:45 首根:鎖開盤價判閘門(只做多)
        if self._gate_ok is None:
            self._gate_open = float(kbar.open)
            if self._dir == -1:
                self._gate_ok = self._gate_open > self._break
                why = (f"開{self._gate_open:.0f}>破{self._break:.0f}→空頭破壞翻多" if self._gate_ok
                       else f"開{self._gate_open:.0f}≤破{self._break:.0f}→空頭延續不做")
            else:
                self._gate_ok = self._gate_open >= self._break
                why = (f"開{self._gate_open:.0f}≥破{self._break:.0f}→多頭延續做多" if self._gate_ok
                       else f"開{self._gate_open:.0f}<破{self._break:.0f}→多頭破壞不做")
            logger.info(f"[AStructNG] {d} 🚦閘門: {why}")
            if not self._gate_ok:
                self._traded = True                      # 閘門不過:整天不做
                return None

        # 過閘門 → 累積窗內 1m K、跑當日錨 0-1-2
        self._bars.append((float(kbar.high), float(kbar.low), float(kbar.close)))
        H = [b[0] for b in self._bars]
        L = [b[1] for b in self._bars]
        w = self._first_wave_anchored(H, L)
        if w is None:
            return None

        entry = float(kbar.close)
        tp = w['w2'] + FIB * w['L1']
        sl = w['w2'] * (1 - BUF)
        self._traded = True
        if tp <= entry:
            logger.info(f"[AStructNG] {d} {t} 偵到錨定0-1-2 但 tp({tp:.0f})<=entry({entry:.0f}) → 不做")
            return None

        logger.info(f"[AStructNG] {d} {t} 進場 LONG @ ~{entry:.0f} | 浪0={w['w0']:.0f} 浪1={w['w1']:.0f} "
                    f"浪2={w['w2']:.0f} L1={w['L1']:.0f} ret2={w['ret2']*100:.0f}% TP={tp:.0f} SL={sl:.0f}")
        return Signal(direction=SignalDirection.BUY, strength=0.7,
                      stop_loss=round(sl, 1), take_profit=round(tp, 1),
                      reason=f"AStructNG w0={w['w0']:.0f} w2={w['w2']:.0f} L1={w['L1']:.0f} ret2={w['ret2']*100:.0f}%",
                      source=self.name)

    def check_exit(self, position: Position, snapshot: MarketSnapshot) -> Optional[Signal]:
        price = snapshot.price
        ts = snapshot.timestamp
        if ts is not None and ts.time() >= self.force_close:
            return Signal(direction=SignalDirection.CLOSE, strength=1.0,
                          stop_loss=price, take_profit=price,
                          reason="AStructNG 收盤平倉(不過夜)", source=self.name)
        return None

    def get_parameters(self) -> dict:
        return {"zz_pct": ZZ_PCT, "fib": FIB, "ret2": [RET2_LO, RET2_HI], "buf": BUF,
                "window": [str(self.window_start), str(self.window_end)],
                "force_close": str(self.force_close), "anchor": "08:45-low + nightgate"}

    def reset_day(self):
        """換交易日重置(不動 _cur_sess)。"""
        self._traded = False
        self._bars = []
        self._bar_time = None
        self._resolved = False        # 當日決定是否已鎖定
        self._have_pending = False    # 是否已收到至少一筆有效決定(可被閘門前覆蓋)
        self._sig_source = ""         # 最近一筆決定來源(人工*/自動未審)
        self._dir = None
        self._break = None
        self._go = False
        self._gate_open = None
        self._gate_ok = None          # None=未判, True/False

    def reset(self):
        self._cur_sess = None
        self.reset_day()
