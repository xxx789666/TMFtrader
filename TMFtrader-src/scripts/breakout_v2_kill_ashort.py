"""v2 — 砍掉 A-空單(數據歸因逼出的假設),其餘全留。再過 WFO 驗證器。

歸因(attribution_breakout.py)顯示:A-short 桶 −100,703 / PF 0.58 = 最大失血源
(結構性多頭裡追跌破突破做空);A-long/B-long/B-short 皆正。
v2 = 原 BreakoutTrend,但**抑制「A-Squeeze SHORT」進場**(其餘不動)。
否證:若 OOS 仍不過門檻 → 「空單虧」只是多頭 regime 紅利、非 edge。
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import warnings; warnings.filterwarnings("ignore")

from strategy.breakout import BreakoutTrendStrategy
from strategy.base import SignalDirection


class BreakoutKillAShort(BreakoutTrendStrategy):
    """原策略,但砍掉 A-Squeeze 的空單(數據:該桶 PF 0.58、最大虧損源)。"""

    def on_kbar(self, kbar, snapshot, **kw):
        sig = super().on_kbar(kbar, snapshot, **kw)
        if sig is not None and "A-Squeeze" in sig.reason and sig.direction == SignalDirection.SELL:
            self._cooldown_bars = 0          # 撤銷 super 為這筆設的冷卻(這筆不發生)
            return None
        return sig


from scripts.optimize_breakout_wfo import run_wfo, FIXED, CONTRACTS, _suggest_breakout


def make_v2(params, contract):
    return BreakoutKillAShort(**{**FIXED, **params, "max_loss_twd": CONTRACTS[contract]["max_loss"]})


if __name__ == "__main__":
    nt = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    run_wfo(make_v2, _suggest_breakout, "v2_kill_ashort", n_trials=nt)
