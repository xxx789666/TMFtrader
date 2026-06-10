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
from datetime import time
from typing import Optional

from loguru import logger

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
            return None   # base 無突破/回踩訊號(最常見、[Scan] 已涵蓋)→ 不記 log

        # v7 日盤限定(2026-06-10 修):base 繼承 24h、但 v7 OOS 僅日盤、且夜盤 realism 證明會虧
        # → 擋掉夜盤(08:45–13:45 以外)進場。只擋進場,持倉出場仍由 check_exit 管理。
        bt = kbar.datetime.time()
        if not (time(8, 45) <= bt <= time(13, 45)):
            logger.info(f"[v7-skip] 夜盤不進場(日盤限定)｜base訊號={sig.reason} dir={sig.direction.value}")
            return None

        # 以下兩道是 v7 專屬閘:base 有訊號、但被 v7 過濾掉時記 log(方便事後查「行情大動但 v7 沒進」)
        # kill-A-short:砍 A-Squeeze 做空(結構性逆勢桶、PF 0.58 最大失血)
        if (self.kill_a_short and "A-Squeeze" in sig.reason
                and sig.direction == SignalDirection.SELL):
            self._cooldown_bars = 0
            logger.info(f"[v7-skip] kill-A-short｜base訊號={sig.reason}（A-Squeeze 做空、v7 砍）")
            return None

        # regime 閘(EMA200 斜率)+ 雙水平對齊(EMA60 同向)
        rej = self._regime_reject(snapshot)
        if rej is not None:
            self._cooldown_bars = 0
            logger.info(f"[v7-skip] {rej}｜base訊號={sig.reason} dir={sig.direction.value}")
            return None
        return sig

    def _regime_reject(self, snapshot: MarketSnapshot) -> Optional[str]:
        """回傳「被哪道閘擋下」的原因字串;None = 通過。"""
        if len(self._ema200_hist) <= self.slope_lookback:
            return "暖機未滿(EMA200 歷史不足)"            # 保守不交易
        atr = snapshot.atr if snapshot.atr > 0 else 1.0
        s200 = self._ema200_hist[-1] - self._ema200_hist[0]
        slope200 = abs(s200) / self.slope_lookback / atr
        if slope200 < self.slope_thr:
            return f"EMA200 斜率閘 slope={slope200:.4f}<thr{self.slope_thr}(慢線太平/盤整)"
        if self.require_dual_slope:
            if len(self._ema60_hist) <= self.slope_lookback:
                return "暖機未滿(EMA60 歷史不足)"
            s60 = self._ema60_hist[-1] - self._ema60_hist[0]
            if (s200 > 0) != (s60 > 0):
                return f"雙水平不同向 EMA200{'↑' if s200 > 0 else '↓'} vs EMA60{'↑' if s60 > 0 else '↓'}"
        return None

    def reset(self):
        super().reset()
        self._ema200_hist = []
        self._ema60_hist = []
