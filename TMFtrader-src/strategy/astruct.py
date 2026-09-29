"""AStruct — 日內早盤波浪 0-1-2 做多 + 日線 EMA250 牛熊濾網 的引擎真 tick paper 執行載具。

lab 交接(2026-06-22):`strategy_Astruct_v1.md`。**forward 候選、非確認 edge**
(standalone Sharpe~0.47 / MaxDD−22% / 年均~7%、2020+ 無 OOS;真正價值=投組分散 sleeve)。
單獨不重倉、微台/小台 1 口、paper 先行。

與其他 paper 執行載具(chips_exec/wave_exec)的**關鍵不同**:那些開盤前就決定、08:45 一槍進;
A_struct 是**盤中即時**——當天日盤 08:45–09:45 用 5m K 即時跑因果 zigzag,偵測第一組上升
浪0-1-2,浪2 確認當根(後續 bar high 超過浪2低點 0.30%)收盤市價做多 1 口。

規則(凍結,移植自回測 scripts/Astruct_ema200_filter.py;EMA200→EMA250 建議版):
- 牛熊前置濾:昨日加權指數日線收 > 昨日 EMA250(因果)才做;熊市整天 SKIP。
- 偵測窗 08:45–09:45,5m K;因果 zigzag 門檻 0.30%;第一組 L-H-L 浪0<浪2<浪1。
- 有效:L1=浪1−浪0 ≥ 浪0×0.15%;浪2折返 (浪1−浪2)/L1 ∈ [0.20,0.80]。
- 進場:浪2 確認當根收盤市價買 1 口;停利=浪2+1.618×L1、停損=浪2×(1−0.15%)(引擎 tick 級硬停/硬利)。
- 了結:先碰 SL/TP 出;都沒碰 → 13:45 收盤市價平、不過夜。
- 固定 1 口靠 launcher RISK_PROFILE=fixed1_paper;標的 MXF 小台(引擎自動對齊 pv50)。

⚠️ paper 階段先驗「即時偵測/浪2 進場/掛單時序」在真盤跑得對(驗工程,非驗 edge)。RECORD_DECISIONS
會錄決策帶,供盤後與 lab 同日 1m/5m 回放逐根對齊(尤其 5m bar 邊界標記是否一致)。
"""
import csv
from datetime import time, date as _date
from pathlib import Path
from typing import Optional, List

from loguru import logger

from strategy.base import BaseStrategy, Signal, SignalDirection
from core.market_data import KBar, MarketSnapshot
from core.position import Position, Side

ROOT = Path(__file__).resolve().parent.parent
TAIEX_CSV = ROOT / "data" / "taiex_daily.csv"


class AStructStrategy(BaseStrategy):
    def __init__(self, ema_span: int = 250, zz_pct: float = 0.0030, fib: float = 1.618,
                 ret2_lo: float = 0.20, ret2_hi: float = 0.80,
                 minpts_frac: float = 0.0015, sl_buf: float = 0.0015,
                 window_start: tuple = (8, 45), window_end: tuple = (9, 45),
                 force_close: tuple = (13, 45), point_value: float = 50.0):
        self.ema_span = ema_span
        self.zz_pct = zz_pct
        self.fib = fib
        self.ret2_lo = ret2_lo
        self.ret2_hi = ret2_hi
        self.minpts_frac = minpts_frac
        self.sl_buf = sl_buf
        self.window_start = time(*window_start)
        self.window_end = time(*window_end)
        self.force_close = time(*force_close)
        self.point_value = point_value
        self.reset()

    @property
    def name(self) -> str:
        return "AStruct"

    # ── 牛熊濾網(昨日指數日線收 vs 昨日 EMA250,因果) ──────────────────────────
    def _compute_regime(self, today: _date) -> bool:
        """回 True=牛(今天可做)。讀不到資料 → fail-closed(不做);否則熊市裸做=最差版(MaxDD−40%)。"""
        try:
            rows = []
            with open(TAIEX_CSV, encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    d = (row.get("d") or row.get("date") or "").strip()
                    c = (row.get("Close") or row.get("close") or "").strip()
                    if d and c:
                        rows.append((d, float(c)))
            rows.sort(key=lambda x: x[0])
            iso = today.isoformat()
            prev = [(d, c) for d, c in rows if d < iso]   # 只取昨日及以前(因果)
            if not prev:
                logger.warning(f"[AStruct] {today} TAIEX 無昨日資料 → fail-closed SKIP")
                return False
            alpha = 2.0 / (self.ema_span + 1.0)            # ewm(adjust=False)
            ema = prev[0][1]
            for _, c in prev[1:]:
                ema = alpha * c + (1.0 - alpha) * ema
            last_d, last_close = prev[-1]
            bull = last_close > ema
            logger.info(f"[AStruct] {today} regime: 昨({last_d})收={last_close:.0f} "
                        f"vs EMA{self.ema_span}={ema:.0f} → {'牛市可做' if bull else '熊市SKIP'}")
            return bull
        except Exception as e:
            logger.warning(f"[AStruct] regime 讀取失敗 → fail-closed SKIP: {e}")
            return False

    # ── 因果 zigzag pivots(忠實移植回測 causal_pivots) ───────────────────────
    def _causal_pivots(self, H: List[float], L: List[float]):
        pct = self.zz_pct
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

    def _first_wave(self, H: List[float], L: List[float]) -> Optional[dict]:
        """第一組有效上升 L-H-L(浪0-1-2)。回 dict 或 None。忠實移植回測 first_wave。"""
        piv = self._causal_pivots(H, L)
        for a in range(len(piv) - 2):
            if piv[a][2] == 'L' and piv[a + 1][2] == 'H' and piv[a + 2][2] == 'L':
                w0, w1, w2 = piv[a][1], piv[a + 1][1], piv[a + 2][1]
                if not (w0 < w2 < w1):
                    continue
                L1 = w1 - w0
                if L1 < w0 * self.minpts_frac:
                    continue
                ret2 = (w1 - w2) / L1
                if not (self.ret2_lo <= ret2 <= self.ret2_hi):
                    continue
                return dict(w0=w0, w1=w1, w2=w2, L1=L1, ret2=ret2)
        return None

    # ── 進場(on_kbar 增量偵測) ──────────────────────────────────────────────
    def on_kbar(self, kbar: KBar, snapshot: MarketSnapshot, **kw) -> Optional[Signal]:
        self._bar_time = kbar.datetime
        d = kbar.datetime.date()
        t = kbar.datetime.time()

        if d != self._cur_sess:                      # 換交易日:重置 + 算當日牛熊
            self._cur_sess = d
            self._traded = False
            self._bars = []
            self._regime_bull = self._compute_regime(d)

        if not self._regime_bull or self._traded:
            return None
        if t < self.window_start or t > self.window_end:
            return None

        self._bars.append((float(kbar.high), float(kbar.low), float(kbar.close)))
        H = [b[0] for b in self._bars]
        L = [b[1] for b in self._bars]
        w = self._first_wave(H, L)                   # 第一根偵測到=浪2 確認當根
        if w is None:
            return None

        entry = float(kbar.close)                     # 確認當根收盤市價
        tp = w['w2'] + self.fib * w['L1']
        sl = w['w2'] * (1 - self.sl_buf)
        self._traded = True                           # 一天一筆(不論進不進,標記避免反覆)
        if tp <= entry:                               # 回測:tp<=entry 跳過該筆
            logger.info(f"[AStruct] {d} {t} 偵測到浪0-1-2 但 tp({tp:.0f})<=entry({entry:.0f}) → 不做")
            return None

        logger.info(f"[AStruct] {d} {t} 進場 LONG @ ~{entry:.0f} | 浪2={w['w2']:.0f} L1={w['L1']:.0f} "
                    f"ret2={w['ret2']*100:.0f}% TP={tp:.0f} SL={sl:.0f}")
        return Signal(direction=SignalDirection.BUY, strength=0.7,
                      stop_loss=round(sl, 1), take_profit=round(tp, 1),
                      reason=f"Astruct w2={w['w2']:.0f} L1={w['L1']:.0f} ret2={w['ret2']*100:.0f}%",
                      source=self.name)

    # ── 了結(SL/TP 由引擎 tick 級硬停/硬利;這裡只管 13:45 收盤強平) ──────────
    def check_exit(self, position: Position, snapshot: MarketSnapshot) -> Optional[Signal]:
        price = snapshot.price
        ts = snapshot.timestamp
        if ts is not None and ts.time() >= self.force_close:
            return Signal(direction=SignalDirection.CLOSE, strength=1.0,
                          stop_loss=price, take_profit=price,
                          reason="Astruct 收盤平倉(不過夜)", source=self.name)
        return None

    def get_parameters(self) -> dict:
        return {"ema_span": self.ema_span, "zz_pct": self.zz_pct, "fib": self.fib,
                "ret2": [self.ret2_lo, self.ret2_hi], "sl_buf": self.sl_buf,
                "window": [str(self.window_start), str(self.window_end)],
                "force_close": str(self.force_close)}

    def reset(self):
        self._cur_sess = None
        self._traded = False
        self._bars = []
        self._regime_bull = False
        self._bar_time = None
