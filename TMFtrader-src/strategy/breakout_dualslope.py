"""BreakoutDualSlopeStrategy — v7 確認 edge 的單一自包含策略類(可直接 port 進 live)。

把研究期 v3→v5→v7 的三層 wrapper 合併成一個 BreakoutTrendStrategy 子類:
- kill-A-short          : 砍掉 A-Squeeze 的做空(數據歸因:結構性逆勢桶,PF 0.58 最大失血)。
- regime 閘(事前)      : EMA200 過去 N 根斜率 / ATR ≥ slope_thr 才允許進場(盤整不交易)。
- 雙水平對齊            : 再要求 EMA60 斜率與 EMA200 斜率同向(中期+長期一致)。
- 出場/進場參數         : 預設 = v7 釘死值(slope_thr=0.015、trail 1.2/1.25、early_cut=50、
                          min_adx=21、afternoon_min_adx=34、expand_ratio=1.20)。

⚠️ 適用範圍:**日盤限定**(夜盤 realism 證明會虧,見 docs/v7_realism_fullday)。
   point_value 預設 10(money-stop 為寬鬆 backstop,真實 P&L 由 engine 的 instrument spec 算,
   與研究 OOS 口徑一致)。
"""
from typing import Optional

from strategy.breakout import BreakoutTrendStrategy
from strategy.base import SignalDirection
from core.market_data import KBar, MarketSnapshot

# v7 確認配置(進場閾值=各 OOS 窗最佳值中位數;其餘=釘死消融值)
V7_DEFAULTS = dict(
    expand_ratio=1.20, pullback_ema_gap=0.20, min_di_gap=10.0,
    trail_trigger_atr=1.2, trail_dist_atr=1.25, early_cut_bars=50, early_cut_loss_atr=1.5,
    momentum_rsi_bear=46.0, momentum_rsi_bull=52.0,
    min_adx=21.0, afternoon_min_adx=34.0, squeeze_grace_bars=1,
)


class BreakoutDualSlopeStrategy(BreakoutTrendStrategy):
    """v7:kill-A-short + 事前 EMA200 斜率閘 + EMA60/EMA200 雙水平對齊。日盤限定。"""

    def __init__(self, slope_lookback: int = 48, slope_thr: float = 0.015,
                 kill_a_short: bool = True, require_dual_slope: bool = True, **kw):
        self.slope_lookback = slope_lookback
        self.slope_thr = slope_thr
        self.kill_a_short = kill_a_short
        self.require_dual_slope = require_dual_slope
        self._ema200_hist: list[float] = []
        self._ema60_hist: list[float] = []
        super().__init__(**{**V7_DEFAULTS, **kw})   # kw 可覆寫(如 max_loss_twd / point_value)

    @property
    def name(self) -> str:
        return "BreakoutDualSlope"

    def on_kbar(self, kbar: KBar, snapshot: MarketSnapshot, **kw) -> Optional[object]:
        # 每根更新 EMA 歷史(事前可得:只用到當下與過去)
        for hist, val in ((self._ema200_hist, snapshot.ema200), (self._ema60_hist, snapshot.ema60)):
            if val and val > 0:
                hist.append(val)
                if len(hist) > self.slope_lookback + 1:
                    hist.pop(0)

        sig = super().on_kbar(kbar, snapshot, **kw)
        if sig is None:
            return None

        # kill-A-short:砍 A-Squeeze 做空
        if (self.kill_a_short and "A-Squeeze" in sig.reason
                and sig.direction == SignalDirection.SELL):
            self._cooldown_bars = 0
            return None

        # regime 閘(EMA200 斜率)+ 雙水平對齊(EMA60 同向)
        if not self._regime_ok(snapshot):
            self._cooldown_bars = 0
            return None
        return sig

    def _regime_ok(self, snapshot: MarketSnapshot) -> bool:
        if len(self._ema200_hist) <= self.slope_lookback:
            return False                                  # 暖機未滿 → 保守不交易
        atr = snapshot.atr if snapshot.atr > 0 else 1.0
        s200 = self._ema200_hist[-1] - self._ema200_hist[0]
        if abs(s200) / self.slope_lookback / atr < self.slope_thr:
            return False                                  # 斜率平坦(盤整)
        if self.require_dual_slope:
            if len(self._ema60_hist) <= self.slope_lookback:
                return False
            s60 = self._ema60_hist[-1] - self._ema60_hist[0]
            if (s200 > 0) != (s60 > 0):
                return False                              # 中期與長期不同向
        return True

    def reset(self):
        super().reset()
        self._ema200_hist = []
        self._ema60_hist = []
