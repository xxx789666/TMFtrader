"""BreakoutTrend V2 — 依 OOS 驗證發現重新設計「進場邏輯」,再丟回 WFO 驗證器。

OOS 學到的事:
- 原版輸在震盪盤(假突破 whipsaw);Mode A「壓縮→波動擴張突破」是追擴張、最容易被巴。
- 只有乾淨趨勢(高 ER)年賺 → edge(若有)在「趨勢延續」,不在「波動突破」。

V2 假設(結構性改進場、非調旋鈕):
1. **砍掉 Mode A**(波動突破)—— 移除 whipsaw 主要來源。
2. **只做 EMA200 對齊的趨勢回調**:價在 EMA200 正確側(margin 外)+ EMA 多頭排列
   (ema5>ema20>ema60)+ ADX 強 + DI 同向 + ema5 回踩貼近 ema20(淺回調)才進。
   = 「在確認趨勢中買回檔」,不追突破。
出場/風控/口數全沿用原版(check_exit 不變)。然後過同一個嚴格 OOS 驗證器。
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import warnings; warnings.filterwarnings("ignore")
from datetime import time

from strategy.breakout import BreakoutTrendStrategy
from strategy.base import Signal, SignalDirection


class BreakoutV2(BreakoutTrendStrategy):
    """只保留『EMA200 對齊的趨勢回調』進場;移除壓縮突破 Mode A。"""

    def on_kbar(self, kbar, snapshot, **kw):
        price = snapshot.price
        atr = snapshot.atr if snapshot.atr > 0 else 1.0
        bt = kbar.datetime.time()

        bd = kbar.datetime.strftime("%Y-%m-%d")
        if bd != self._current_date:
            self._current_date = bd; self._session_open = 0.0

        if snapshot.bar_count < 80:
            self._prev_ema5 = snapshot.ema5; self._prev_ema20 = snapshot.ema20
            return None
        if self._cooldown_bars > 0:
            self._cooldown_bars -= 1
            self._prev_ema5 = snapshot.ema5; self._prev_ema20 = snapshot.ema20
            return None
        in_day = time(8, 45) <= bt <= time(13, 45)
        in_night = bt >= time(15, 0) or bt <= time(5, 0)
        if not (in_day or in_night):
            self._prev_ema5 = snapshot.ema5; self._prev_ema20 = snapshot.ema20
            return None

        signal = None
        ema200 = snapshot.ema200
        if ema200 > 0:
            eff_adx = self.min_adx
            if in_day and bt >= time(11, 0):
                eff_adx = max(self.min_adx, self.afternoon_min_adx)
            margin = self.ema200_margin_atr * atr
            near = abs(snapshot.ema5 - snapshot.ema20) <= self.pullback_ema_gap * atr  # 淺回檔

            up = (price > ema200 + margin
                  and snapshot.ema5 > snapshot.ema20 > snapshot.ema60
                  and snapshot.adx > eff_adx
                  and snapshot.plus_di > snapshot.minus_di + self.min_di_gap
                  and near)
            dn = (price < ema200 - margin
                  and snapshot.ema5 < snapshot.ema20 < snapshot.ema60
                  and snapshot.adx > eff_adx
                  and snapshot.minus_di > snapshot.plus_di + self.min_di_gap
                  and near)

            if up:
                signal = Signal(direction=SignalDirection.BUY, strength=0.8,
                                stop_loss=price - self.sl_atr * atr, take_profit=price + self.tp_atr * atr,
                                reason="V2 trend-pullback LONG", source=self.name)
            elif dn:
                signal = Signal(direction=SignalDirection.SELL, strength=0.8,
                                stop_loss=price + self.sl_atr * atr, take_profit=price - self.tp_atr * atr,
                                reason="V2 trend-pullback SHORT", source=self.name)

        if signal is not None:
            self._entry_atr = atr; self._trail_best = price
            signal.trail_dist_pts = round(self.trail_dist_atr * atr)
            self._breakeven_active = False; self._scaled_out = False; self._cooldown_bars = 5

        self._prev_ema5 = snapshot.ema5; self._prev_ema20 = snapshot.ema20
        return signal


from scripts.optimize_breakout_wfo import run_wfo, FIXED, CONTRACTS


def suggest_v2(trial):
    return dict(
        min_adx=trial.suggest_float("min_adx", 20.0, 32.0, step=1.0),
        min_di_gap=trial.suggest_float("min_di_gap", 5.0, 20.0, step=1.0),
        pullback_ema_gap=trial.suggest_float("pullback_ema_gap", 0.10, 0.60, step=0.05),
        ema200_margin_atr=trial.suggest_float("ema200_margin_atr", 0.0, 1.0, step=0.1),
        trail_trigger_atr=trial.suggest_float("trail_trigger_atr", 0.8, 2.0, step=0.1),
        trail_dist_atr=trial.suggest_float("trail_dist_atr", 0.3, 2.0, step=0.1),
    )


def make_v2(params, contract):
    p = dict(FIXED)
    p.pop("min_di_gap", None); p.pop("pullback_ema_gap", None)   # 改由 suggest 提供
    p.update(params)
    p["afternoon_min_adx"] = params["min_adx"]   # V2 午後不另加嚴
    p["max_loss_twd"] = CONTRACTS[contract]["max_loss"]
    return BreakoutV2(**p)


if __name__ == "__main__":
    nt = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    run_wfo(make_v2, suggest_v2, "breakout_v2_trendpullback", n_trials=nt)
