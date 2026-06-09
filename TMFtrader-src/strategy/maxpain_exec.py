"""MaxPainExec — maxpain_v2 訊號的「引擎真 tick paper 執行載具」(完整 2 口含加碼)。

訊號計算(TXO 各履約 OI → Max Pain、dist)留在 scripts/maxpain_daily.py(cron、HTTP 官網資料),
寫 data/maxpain_v2/next_signal.json。這支只負責「執行」:用引擎真實 tick 成交價跑完整策略——
日盤開盤窗進第1口、盤中漲 +1% 加第2口(靠引擎 check_scale/scale-in 機制)、−2% 全停(引擎硬停)、
抱到該週選結算日 13:30 強平。多日持倉跨每日 cron 重啟:狀態(ed/S1/scaled)走引擎 _strategy_state
持久化(跟 position 存、平倉自動清、重啟 _restore_strategy_state 還原)。

規則(對齊 maxpain_v2 凍結配置):
- next_signal.json state=="signal_fired"(dist>0、訊號日 t 收盤算、隔日開盤進)→ t+1 開盤窗進第1口。
- 第1口 S1,停損 S1×0.98(交給引擎 tick 級硬停;加碼後 stop 不變→兩口一起 −2% 出、第2口實虧 −3%)。
- 盤中漲到 S1×1.01 → 加第2口(check_scale)。每組最多加一次。
- 抱到結算日(ed)13:30 強平(check_exit、用 snapshot.timestamp)。
- 固定口:第1口靠 RISK_PROFILE=fixed1_paper(cap 1);加碼那口走引擎 scale-in 路徑(不過 sizer)。

⚠️ TF 須 ≥30(讓 −2% 停損 ≈3.5-5×ATR 過 risk_manager 8×ATR gate)。標的 MXF 小台(pv50)。
"""
import json
from datetime import date, time
from pathlib import Path
from typing import Optional

from strategy.base import BaseStrategy, Signal, SignalDirection
from core.market_data import KBar, MarketSnapshot
from core.position import Position, Side

ROOT = Path(__file__).resolve().parent.parent
SIGNAL_FILE = ROOT / "data" / "maxpain_v2" / "next_signal.json"


class MaxPainExecStrategy(BaseStrategy):
    def __init__(self, stop_pct: float = 0.02, scale_pct: float = 0.01, point_value: float = 50.0,
                 session_start: tuple = (8, 30), entry_window_end: tuple = (9, 30),
                 settle_close: tuple = (13, 30), trail_pct: float = 0.0125, arm_pct: float = 0.01):
        self.stop_pct = stop_pct
        self.scale_pct = scale_pct
        self.point_value = point_value
        # 追蹤止盈(2026-06-09 forward 驗證版):獲利曾漲過 +arm_pct 後,從持有期最高點回落
        # trail_pct → 鎖利出場。trail_pct=0 即停用(回到凍結「無止盈、抱到結算」)。
        self.trail_pct = trail_pct
        self.arm_pct = arm_pct
        self.session_start = time(*session_start)
        self.entry_window_end = time(*entry_window_end)
        self.settle_close = time(*settle_close)
        self.reset()

    @property
    def name(self) -> str:
        return "MaxPainExec"

    def _read_signal(self) -> Optional[dict]:
        try:
            return json.loads(SIGNAL_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None

    def on_kbar(self, kbar: KBar, snapshot: MarketSnapshot, **kw) -> Optional[Signal]:
        self._bar_time = kbar.datetime
        today = kbar.datetime.date()
        if today != self._cur_day:
            self._cur_day = today

        bt = kbar.datetime.time()
        if bt < self.session_start or bt >= self.entry_window_end:
            return None

        sig = self._read_signal()
        if not sig or sig.get("state") != "signal_fired" or sig.get("side") != "long":
            return None
        signal_t = sig.get("signal_t")
        ed = sig.get("ed")
        if not signal_t or not ed:
            return None
        # 進場日 = 訊號日的次一交易日;今天須晚於訊號日、且在合理範圍(≤4 天、避免漏掉後追高)
        try:
            d_sig = date.fromisoformat(signal_t)
        except ValueError:
            return None
        if today <= d_sig or (today - d_sig).days > 4:
            return None
        if self._acted_signal == signal_t:            # 同一訊號已進過 → 不重進
            return None

        price = snapshot.price
        self._acted_signal = signal_t
        self._mp_ed = ed                              # ISO 字串(供引擎 _strategy_state 持久化)
        self._mp_s1 = price
        self._mp_scaled = False
        self._mp_hi = price                           # 追蹤止盈:持有期最高點(從進場價起算)
        self._mp_armed = False
        return Signal(direction=SignalDirection.BUY, strength=0.7,
                      stop_loss=round(price * (1 - self.stop_pct), 1), take_profit=0.0,
                      reason=f"maxpain long dist{sig.get('dist')}", source=self.name)

    def check_scale(self, position: Position, snapshot: MarketSnapshot) -> Optional[Signal]:
        """持倉中:漲到 S1×(1+scale_pct) 且尚未加碼 → 回傳加第2口的同向 Signal(引擎 _execute_scale 執行)。"""
        if self._mp_scaled or position.side != Side.LONG:
            return None
        s1 = self._mp_s1
        if s1 <= 0:
            return None
        if snapshot.price >= s1 * (1 + self.scale_pct):
            self._mp_scaled = True
            s = Signal(direction=SignalDirection.BUY, strength=0.7,
                       stop_loss=position.stop_loss, take_profit=0.0,
                       reason=f"maxpain +{self.scale_pct*100:.0f}% 加碼", source=self.name)
            s.close_quantity = 1                      # 借此欄帶「加 1 口」
            return s
        return None

    def check_exit(self, position: Position, snapshot: MarketSnapshot) -> Optional[Signal]:
        """−2% 由引擎硬停(stop_loss=S1×0.98);這裡管 追蹤止盈 + 結算日強平。
        ed/最高點/武裝 走 _strategy_state 跨重啟還原。"""
        ts = snapshot.timestamp
        if ts is None or not self._mp_ed:
            return None
        # 追蹤止盈(每 tick):更新最高點 → 漲過 +arm% 武裝 → 武裝後從高點回落 trail% 鎖利出場。
        px = snapshot.price
        if self.trail_pct > 0 and self._mp_s1 > 0 and px > 0:
            if px > self._mp_hi:
                self._mp_hi = px
            if not self._mp_armed and px >= self._mp_s1 * (1 + self.arm_pct):
                self._mp_armed = True
            if self._mp_armed and px <= self._mp_hi * (1 - self.trail_pct):
                return Signal(direction=SignalDirection.CLOSE, strength=1.0,
                              stop_loss=px, take_profit=px,
                              reason=f"maxpain 追蹤止盈 -{self.trail_pct*100:.2f}%"
                                     f"(高{self._mp_hi:.0f}→{px:.0f})", source=self.name)
        try:
            ed = date.fromisoformat(self._mp_ed)
        except (ValueError, TypeError):
            return None
        d = ts.date()
        hit = (d > ed) or (d == ed and ts.time() >= self.settle_close)
        if hit:
            return Signal(direction=SignalDirection.CLOSE, strength=1.0,
                          stop_loss=snapshot.price, take_profit=snapshot.price,
                          reason="maxpain 週選結算強平", source=self.name)
        return None

    def get_parameters(self) -> dict:
        return {"stop_pct": self.stop_pct, "scale_pct": self.scale_pct,
                "trail_pct": self.trail_pct, "arm_pct": self.arm_pct,
                "settle_close": str(self.settle_close)}

    def reset(self):
        self._cur_day = None
        self._bar_time = None
        self._acted_signal = None
        # 多日持倉狀態(引擎 _restore_strategy_state 會在重啟+持倉時覆寫還原)
        self._mp_ed = None
        self._mp_s1 = 0.0
        self._mp_scaled = False
        self._mp_hi = 0.0
        self._mp_armed = False
