"""
ORB (Opening Range Breakout) 策略 — 夜盤版
============================================
設計用途：台指微期（MXF）夜盤美股時段（21:30-04:00 TST）
  與 QQQ/NASDAQ 走勢高度相關，利用美股開盤後的方向確立進場。

邏輯：
  1. 每日 21:30 開始記錄開盤區間（預設前 30 分鐘 = 6 根 5min K）
  2. 22:00 後：收盤突破區間高點 → LONG；突破低點 → SHORT
  3. 止損：ATR 倍數（sl_atr），或區間對邊（sl_type='range'）
  4. 出場：追蹤止損 / max_bars 時間出場 / 04:00 強制平倉
  5. 每個夜盤 session 最多一筆交易

驗證流程：
  Step 1: 在 QQQM（64 個月 MT5 資料）驗證信號品質
  Step 2: 在 MXF 夜盤（21:30-04:00）驗證實盤可執行性
  若兩者均達標（WR ≥ 55%, PF ≥ 1.5），合併日盤+夜盤策略
"""

import pickle
from collections import deque
from datetime import datetime, time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger as _logger

from strategy.base import BaseStrategy, Signal, SignalDirection
from core.market_data import KBar, MarketSnapshot
from core.position import Position, Side


class ORBStrategy(BaseStrategy):
    """
    Opening Range Breakout 開盤區間突破策略（夜盤美股時段）
    """

    @property
    def name(self) -> str:
        return "ORB_Night"

    def __init__(
        self,
        # ── 開盤區間設定 ──────────────────────────────
        orb_minutes: int = 30,           # 開盤區間時長（分鐘），建議 20/30/45
        entry_filter_atr: float = 0.0,   # 突破過濾：需超過區間邊界 N×ATR 才進場（0=無過濾）
        # ── B1 Layer 1：ORB 寬度過濾 ─────────────────
        min_orb_width_atr: float = 0.0,  # ORB 寬度下限（ATR 倍數），0=不過濾
        max_orb_width_atr: float = 0.0,  # ORB 寬度上限（ATR 倍數），0=不過濾
        # ── B2 Layer 1：高量假突破過濾 ───────────────
        max_breakout_vol_ratio: float = 0.0,  # 突破時成交量比上限，0=不過濾
        # ── 進場過濾 ─────────────────────────────────
        adx_min: float = 0.0,            # ADX 最低門檻（0=不過濾）
        di_gap_min: float = 0.0,         # DI 差距最小門檻（0=不過濾）
        trend_filter: bool = False,      # EMA200 趨勢過濾（夜盤建議關閉，因日盤EMA200無參考性）
        allow_both_directions: bool = True,  # True=雙向（Long+Short）；False=只做多
        # ── 止損設定 ─────────────────────────────────
        sl_type: str = 'atr',            # 'atr'=ATR倍數止損 / 'range'=區間對邊止損
        sl_atr: float = 2.0,             # sl_type='atr' 時有效：止損距離（ATR 倍數）
        sl_range_buffer: float = 0.3,    # sl_type='range' 時：對邊 + N×ATR 緩衝
        max_sl_pts: float = 0.0,         # 停損點數上限（0=不限），防止 ATR 極大時 SL 過寬
        max_loss_twd: float = 4000.0,    # 金額硬止損（TWD），0=停用
        # ── 出場設定 ─────────────────────────────────
        tp_atr: float = 10.0,            # 固定停利（ATR 倍數），通常不觸及，由追蹤止損出場
        trail_trigger_atr: float = 0.8,  # 獲利超過此 ATR 倍數後啟動追蹤止損
        trail_dist_atr: float = 0.3,     # 追蹤止損距離（ATR 倍數）；必須 < trail_trigger_atr
        max_bars: int = 60,              # 時間出場（根數），5min×60=300min=5hr
        early_cut_bars: int = 30,        # 早切止損根數（持倉超過N根且虧損）
        early_cut_loss_atr: float = 1.5, # 早切止損虧損門檻（ATR 倍數）
        point_value: float = 10.0,       # 每點價值（MXF = 10 TWD/點）
        # ── 時段設定 ─────────────────────────────────
        session_start: tuple = (21, 30), # 夜盤開始時間（台灣時間）
        force_close_time: tuple = (4, 0),  # 強制平倉時間（04:00 TST = US 市場接近收盤）
        # ── B2 ML Filter ─────────────────────────────
        ml_model_path: str = "",     # orb_filter_b2.pkl 路徑（空=不使用）
        ml_features_path: str = "",  # selected_features_b2.txt 路徑
        ml_threshold: float = 0.40,  # ML 過濾閾值（推薦 0.40）
    ):
        self.orb_minutes = orb_minutes
        self.orb_bars = max(1, orb_minutes // 5)   # 5min K 根數
        self.entry_filter_atr = entry_filter_atr
        self.min_orb_width_atr = min_orb_width_atr
        self.max_orb_width_atr = max_orb_width_atr
        self.max_breakout_vol_ratio = max_breakout_vol_ratio
        self.adx_min = adx_min
        self.di_gap_min = di_gap_min
        self.trend_filter = trend_filter
        self.allow_both_directions = allow_both_directions
        self.sl_type = sl_type
        self.sl_atr = sl_atr
        self.sl_range_buffer = sl_range_buffer
        self.max_sl_pts = max_sl_pts
        self.max_loss_twd = max_loss_twd
        self.tp_atr = tp_atr
        self.trail_trigger_atr = trail_trigger_atr
        self.trail_dist_atr = trail_dist_atr
        self.max_bars = max_bars
        self.early_cut_bars = early_cut_bars
        self.early_cut_loss_atr = early_cut_loss_atr
        self.point_value = point_value
        self.session_start = time(*session_start)
        self.force_close_time = time(*force_close_time)
        self.ml_threshold = ml_threshold

        # ── B2 ML Filter 載入 ─────────────────────────
        self._bars: deque = deque(maxlen=300)
        self._ml_model = None
        self._ml_features: list[str] = []
        if ml_model_path:
            self._load_ml(ml_model_path, ml_features_path)

        # ── 狀態 ─────────────────────────────────────
        self._orb_session_key: str = ""   # 當前 ORB session 識別（YYYY-MM-DD-NN）
        self._orb_high: float = 0.0       # 開盤區間最高價
        self._orb_low: float = float('inf')  # 開盤區間最低價
        self._orb_bar_count: int = 0      # 已收集的開盤區間 K 棒數
        self._orb_ready: bool = False     # 開盤區間是否已建立
        self._entered: bool = False       # 本 session 是否已進場
        self._entry_atr: float = 0.0
        self._trail_best: float = 0.0
        self._range_width: float = 0.0   # 區間寬度（用於 range 止損計算）
        self._current_bar_time: Optional[datetime] = None

    # ─────────────────────────────────────────────────────
    # B2 ML Filter
    # ─────────────────────────────────────────────────────
    def _load_ml(self, model_path: str, features_path: str):
        try:
            with open(model_path, "rb") as f:
                self._ml_model = pickle.load(f)
            self._ml_features = [
                ln.strip() for ln in Path(features_path).read_text(encoding="utf-8").splitlines()
                if ln.strip()
            ]
            _logger.info(f"[ORB ML] model={Path(model_path).name} features={len(self._ml_features)} threshold={self.ml_threshold}")
        except Exception as e:
            _logger.warning(f"[ORB ML] 載入失敗: {e}，使用 Layer 1 only")

    def _compute_features(self) -> dict:
        """從最近 300 根 5min bar 計算 B2 所需的 30 個特徵。"""
        bars = list(self._bars)
        if len(bars) < 50:
            return {}
        df = pd.DataFrame(bars)
        c  = df["close"].values.astype(np.float64)
        h  = df["high"].values.astype(np.float64)
        lo = df["low"].values.astype(np.float64)
        v  = df["volume"].values.astype(np.float64)
        n  = len(c)

        def _lag(arr, k):
            out = np.empty_like(arr); out[:k] = np.nan; out[k:] = arr[:-k]; return out

        def _ema(arr, span):
            return pd.Series(arr).ewm(span=span, adjust=False).mean().values

        def _roll(arr, w, fn):
            return getattr(pd.Series(arr).rolling(w, min_periods=1), fn)().values

        def _sdiv(a, b, fill=0.0):
            with np.errstate(divide="ignore", invalid="ignore"):
                return np.where(np.abs(b) > 1e-8, a / b, fill).astype(np.float32)

        # ATR-14 Wilder
        pc = np.empty(n); pc[0] = c[0]; pc[1:] = c[:-1]
        tr = np.maximum(h - lo, np.maximum(np.abs(h - pc), np.abs(lo - pc)))
        atr14 = pd.Series(tr).ewm(alpha=1/14, adjust=False).mean().values
        atr_long = _roll(atr14, 50, "mean")

        ema5 = _ema(c, 5); ema10 = _ema(c, 10); ema20 = _ema(c, 20); ema200 = _ema(c, 200)

        def _rsi(arr, p):
            d = np.diff(arr); g = np.where(d>0,d,0.); ls = np.where(d<0,-d,0.)
            ag = np.mean(g[:p]) if len(g)>=p else 0.; al = np.mean(ls[:p]) if len(ls)>=p else 1e-8
            out = np.full(len(arr), 50.)
            for i in range(p, len(arr)-1):
                ag=(ag*(p-1)+g[i])/p; al=(al*(p-1)+ls[i])/p
                out[i+1]=100-100/(1+ag/al) if al>0 else 100.
            return out.astype(np.float32)

        rsi14=_rsi(c,14); rsi7=_rsi(c,7); rsi21=_rsi(c,21)

        bb_mid=_roll(c,20,"mean"); bb_std=_roll(c,20,"std")
        bb_upper=bb_mid+2*bb_std; bb_lower=bb_mid-2*bb_std
        bb_width=_sdiv(bb_upper-bb_lower, bb_mid); bb_width_ma=_roll(bb_width,20,"mean")

        pdm=np.zeros(n); ndm=np.zeros(n)
        for i in range(1,n):
            up=h[i]-h[i-1]; dn=lo[i-1]-lo[i]
            pdm[i]=up if (up>dn and up>0) else 0.; ndm[i]=dn if (dn>up and dn>0) else 0.
        def _adx(period):
            st=pd.Series(tr).ewm(alpha=1/period,adjust=False).mean().values
            sp=pd.Series(pdm).ewm(alpha=1/period,adjust=False).mean().values
            sn=pd.Series(ndm).ewm(alpha=1/period,adjust=False).mean().values
            pdi=_sdiv(100*sp,st); mdi=_sdiv(100*sn,st)
            dx=_sdiv(np.abs(pdi-mdi),pdi+mdi)*100
            return pdi,mdi,pd.Series(dx).ewm(alpha=1/period,adjust=False).mean().values
        pdi14,mdi14,adx14_v=_adx(14); pdi21,mdi21,adx21_v=_adx(21)

        lo14=_roll(lo,14,"min"); hi14=_roll(h,14,"max")
        stoch_k=_sdiv(100*(c-lo14),hi14-lo14,50.); stoch_d=_roll(stoch_k,3,"mean")

        ml=_ema(c,12); ms=_ema(c,26); macd_line=ml-ms
        macd_sig=_ema(macd_line,9); macd_hist=macd_line-macd_sig

        log_ret=np.concatenate([[0.],np.diff(np.log(np.maximum(c,1e-8)))])
        ret_skew20=pd.Series(log_ret).rolling(20,min_periods=5).skew().values
        hl_range_pct=_sdiv((h-lo).astype(np.float32),c)*100.

        hi200=_roll(h,200,"max"); lo200=_roll(lo,200,"min")
        dist_high200=_sdiv(hi200-c,atr14); dist_low200=_sdiv(c-lo200,atr14)

        prev_o=_lag(df["open"].values.astype(np.float64),1); prev_c_=_lag(c,1)
        engulf=np.zeros(n,dtype=np.float32)
        for i in range(1,n):
            if prev_o[i] is not None and not np.isnan(prev_o[i]):
                bull=(c[i]>prev_o[i])and(df["open"].values[i]<prev_c_[i])
                bear=(c[i]<prev_o[i])and(df["open"].values[i]>prev_c_[i])
                engulf[i]=1. if bull else(-1. if bear else 0.)

        ret10=pd.Series(c).pct_change(10).values; ret10_l5=_lag(ret10,5)
        vpt=np.cumsum(v*log_ret)
        close_std20=pd.Series(c).rolling(20,min_periods=1).std().values
        f_crs20=_sdiv(close_std20,atr14)

        i=-1
        def _v(arr): return float(arr[i]) if not np.isnan(arr[i]) else 0.

        return {
            "f_engulfing":           _v(engulf),
            "f_ema5_vs_ema20_l5":    _v(_lag(_sdiv(ema5-ema20,atr14),5)),
            "f_rsi21":               _v(rsi21),
            "f_cci14_l1":            _v(_lag(_sdiv(c-_roll(c,14,"mean"),0.015*_roll(c,14,"std")),1)),
            "f_hl_range_pct":        _v(hl_range_pct),
            "f_di_bull_strength":    _v(_sdiv(pdi14-mdi14,atr14)),
            "f_pdi21":               _v(pdi21),
            "f_bb_squeeze":          float(bb_width[i]<bb_width_ma[i]),
            "f_ema20_slope":         _v(_sdiv(ema20-_lag(ema20,5),atr14)/5.),
            "f_adx_l3":              _v(_lag(adx14_v,3)),
            "f_ema_align_x_adx":     _v(_sdiv(ema20-ema200,atr14)*adx14_v/100.),
            "f_bb_pos_l5":           _v(_lag(_sdiv(c-bb_lower,bb_upper-bb_lower,0.5),5)),
            "f_ema20_vs_ema200":     _v(_sdiv(ema20-ema200,atr14)),
            "f_dist_recent_low200":  _v(dist_low200),
            "f_adx21_l1":            _v(_lag(adx21_v,1)),
            "f_ema10_slope":         _v(_sdiv(ema10-_lag(ema10,5),atr14)/5.),
            "f_high_vs_ema20":       _v(_sdiv(h-ema20,atr14)),
            "f_adx21_slope":         _v(_sdiv(adx21_v-_lag(adx21_v,3),atr14)),
            "f_rsi_ma5":             _v(_roll(rsi14,5,"mean")),
            "f_rsi":                 _v(rsi14),
            "f_rsi7_vs_rsi21":       _v(rsi7-rsi21),
            "f_pdi_l1":              _v(_lag(pdi14,1)),
            "f_dist_recent_high200": _v(dist_high200),
            "f_macd_hist":           _v(macd_hist),
            "f_ret10_l5":            _v(ret10_l5),
            "f_vol_price_trend":     _v(pd.Series(vpt).diff(5).values),
            "f_adx21_l3":            _v(_lag(adx21_v,3)),
            "f_ret_skew20":          _v(ret_skew20),
            "f_stoch_k_l3":          _v(_lag(stoch_k,3)),
            "f_close_roll_std20":    _v(f_crs20),
        }

    def _ml_predict(self) -> tuple[bool, float]:
        """B2 ML Filter：無模型直接通過，有模型則預測機率。"""
        if self._ml_model is None:
            return True, 1.0
        feats = self._compute_features()
        if not feats:
            return False, 0.0
        row = {f: feats.get(f, 0.0) for f in self._ml_features}
        X = pd.DataFrame([row])[self._ml_features]
        prob = float(self._ml_model.predict_proba(X)[0, 1])
        return prob >= self.ml_threshold, prob

    # ─────────────────────────────────────────────────────
    # Session 識別（夜盤從 21:30 開始，跨越午夜）
    # ─────────────────────────────────────────────────────
    def _get_session_key(self, bar_dt: datetime) -> str:
        """
        Session key 邏輯：
        - MXF 夜盤 (session_start=21:30)：21:30~23:59 → 當天；00:00~05:00 → 前天
        - QQQM UTC+2 (session_start=16:30)：16:30~20:55 → 當天（無跨夜）
        以 session_start 那一天的日期 + 'N' 為 key。
        """
        t = bar_dt.time()
        if t >= self.session_start:
            # 當天日期（e.g. 21:30~23:59 或 16:30~20:55）
            return bar_dt.strftime('%Y-%m-%d') + '-N'
        elif self.session_start >= time(20, 0) and t <= time(5, 30):
            # 跨夜 session（session_start 在晚上）：00:00~05:30 → 前一天
            from datetime import timedelta
            prev = bar_dt.date() - timedelta(days=1)
            return prev.strftime('%Y-%m-%d') + '-N'
        else:
            # 不在 session 時段
            return ""

    # ─────────────────────────────────────────────────────
    # 進場
    # ─────────────────────────────────────────────────────
    def on_kbar(self, kbar: KBar, snapshot: MarketSnapshot, **kwargs) -> Optional[Signal]:
        self._current_bar_time = kbar.datetime
        # 將每根 5min K 棒存入 buffer，供 ML 特徵計算使用
        self._bars.append({
            "open": kbar.open, "high": kbar.high,
            "low": kbar.low, "close": kbar.close, "volume": kbar.volume,
        })
        price = snapshot.price
        atr = snapshot.atr if snapshot.atr > 0 else 1.0
        bar_time = kbar.datetime.time()

        # ── 只處理 session 時段
        # 跨夜 session（session_start >= 20:00）：包含 00:00~05:30
        # 日間 session（session_start < 20:00）：只含 session_start 之後
        if self.session_start >= time(20, 0):
            in_session = bar_time >= self.session_start or bar_time <= time(5, 30)
        else:
            in_session = bar_time >= self.session_start
        if not in_session:
            return None

        # ── Session 識別與重置
        session_key = self._get_session_key(kbar.datetime)
        if not session_key:
            return None

        if session_key != self._orb_session_key:
            # 新的夜盤 session 開始
            self._orb_session_key = session_key
            self._orb_high = 0.0
            self._orb_low = float('inf')
            self._orb_bar_count = 0
            self._orb_ready = False
            self._entered = False

        # ── 暖機期（全局 bar_count < 80 跳過）
        if snapshot.bar_count < 80:
            return None

        # ── 本 session 已進場 → 不再找新信號
        if self._entered:
            return None

        # ── 建立開盤區間（前 orb_bars 根）
        if not self._orb_ready:
            self._orb_bar_count += 1
            self._orb_high = max(self._orb_high, kbar.high)
            self._orb_low = min(self._orb_low, kbar.low)
            if self._orb_bar_count >= self.orb_bars:
                self._range_width = self._orb_high - self._orb_low
                orb_w_atr = self._range_width / atr if atr > 0 else 0.0
                # B1 Layer 1：ORB 寬度過濾（3-5 ATR）
                if self.min_orb_width_atr > 0 and orb_w_atr < self.min_orb_width_atr:
                    _logger.info(f"[ORB] 區間太窄 {orb_w_atr:.2f}×ATR < {self.min_orb_width_atr}，跳過")
                    self._entered = True   # 本 session 不再進場
                    return None
                if self.max_orb_width_atr > 0 and orb_w_atr > self.max_orb_width_atr:
                    _logger.info(f"[ORB] 區間太寬 {orb_w_atr:.2f}×ATR > {self.max_orb_width_atr}，跳過")
                    self._entered = True
                    return None
                self._orb_ready = True
                _logger.debug(
                    f"[ORB] {kbar.datetime} 區間建立 "
                    f"High={self._orb_high:.0f} Low={self._orb_low:.0f} "
                    f"Width={self._range_width:.0f} ({orb_w_atr:.2f}×ATR)"
                )
            return None

        # ── 強制平倉時段不進場
        if self.force_close_time <= time(6, 0):
            # MXF 夜盤：04:00~05:30
            near_close = bar_time >= self.force_close_time and bar_time <= time(5, 30)
        else:
            # QQQM UTC+2：>= 20:55
            near_close = bar_time >= self.force_close_time
        if near_close:
            return None

        # ── ADX 過濾
        if self.adx_min > 0 and snapshot.adx < self.adx_min:
            return None

        # ── 進場條件
        entry_threshold = self.entry_filter_atr * atr
        long_trigger  = self._orb_high + entry_threshold
        short_trigger = self._orb_low  - entry_threshold

        # ── 區間寬度防呆（太窄不可靠）
        if self._range_width < atr * 0.3:
            return None

        signal = None
        vol_ratio = getattr(snapshot, "volume_ratio", 1.0) or 1.0

        # ── LONG：突破區間高點
        long_ok = price > long_trigger
        if long_ok:
            # Bug fix: vol_ratio 只在實際突破時才判斷（不在每根 K 棒都判斷）
            if self.max_breakout_vol_ratio > 0 and vol_ratio > self.max_breakout_vol_ratio:
                _logger.info(f"[ORB] 高量假突破過濾(LONG) vol_ratio={vol_ratio:.2f} > {self.max_breakout_vol_ratio}，跳過")
                self._entered = True
                return None
            di_ok = True
            if self.di_gap_min > 0:
                di_ok = snapshot.plus_di - snapshot.minus_di >= self.di_gap_min
            if self.trend_filter:
                di_ok = di_ok and (snapshot.price > snapshot.ema200)
            if di_ok:
                sl = self._calc_sl(price, atr, is_long=True)
                tp = price + self.tp_atr * atr
                signal = Signal(
                    direction=SignalDirection.BUY,
                    strength=0.80,
                    stop_loss=sl,
                    take_profit=tp,
                    reason=(
                        f"ORB-LONG  range=[{self._orb_low:.0f},{self._orb_high:.0f}] "
                        f"width={self._range_width:.0f} adx={snapshot.adx:.0f} volR={vol_ratio:.2f}"
                    ),
                    source=self.name,
                )

        # ── SHORT：突破區間低點
        if signal is None and self.allow_both_directions:
            short_ok = price < short_trigger
            if short_ok:
                # Bug fix: vol_ratio 只在實際突破時才判斷
                if self.max_breakout_vol_ratio > 0 and vol_ratio > self.max_breakout_vol_ratio:
                    _logger.info(f"[ORB] 高量假突破過濾(SHORT) vol_ratio={vol_ratio:.2f} > {self.max_breakout_vol_ratio}，跳過")
                    self._entered = True
                    return None
                di_ok = True
                if self.di_gap_min > 0:
                    di_ok = snapshot.minus_di - snapshot.plus_di >= self.di_gap_min
                if self.trend_filter:
                    di_ok = di_ok and (snapshot.price < snapshot.ema200)
                if di_ok:
                    sl = self._calc_sl(price, atr, is_long=False)
                    tp = price - self.tp_atr * atr
                    signal = Signal(
                        direction=SignalDirection.SELL,
                        strength=0.80,
                        stop_loss=sl,
                        take_profit=tp,
                        reason=(
                            f"ORB-SHORT range=[{self._orb_low:.0f},{self._orb_high:.0f}] "
                            f"width={self._range_width:.0f} adx={snapshot.adx:.0f} volR={vol_ratio:.2f}"
                        ),
                        source=self.name,
                    )

        if signal is not None:
            # ── B2 ML Filter（Layer 2）
            ml_pass, ml_prob = self._ml_predict()
            if not ml_pass:
                _logger.info(f"[ORB ML] SKIP prob={ml_prob:.3f} < {self.ml_threshold}")
                self._entered = True
                return None
            if self._ml_model is not None:
                _logger.info(f"[ORB ML] PASS prob={ml_prob:.3f} >= {self.ml_threshold}")

            self._entered = True
            self._entry_atr = atr
            self._trail_best = price
            _logger.info(
                f"[ORB] {kbar.datetime} {signal.direction.value} "
                f"@ {price:.0f}  SL={signal.stop_loss:.0f}  "
                f"reason={signal.reason}"
            )

        return signal

    def _calc_sl(self, price: float, atr: float, is_long: bool) -> float:
        """計算止損價（含 max_sl_pts 上限保護）"""
        if self.sl_type == 'range':
            buf = self.sl_range_buffer * atr
            sl = (self._orb_low - buf) if is_long else (self._orb_high + buf)
        else:  # 'atr'
            sl_dist = self.sl_atr * atr
            if self.max_sl_pts > 0:
                sl_dist = min(sl_dist, self.max_sl_pts)
            sl = (price - sl_dist) if is_long else (price + sl_dist)
        return sl

    # ─────────────────────────────────────────────────────
    # 出場
    # ─────────────────────────────────────────────────────
    def check_exit(self, position: Position, snapshot: MarketSnapshot) -> Optional[Signal]:
        price = snapshot.price
        atr = self._entry_atr if self._entry_atr > 0 else max(snapshot.atr, 1.0)
        is_long = (position.side == Side.LONG)

        def close_sig(reason: str) -> Signal:
            return Signal(
                direction=SignalDirection.CLOSE,
                strength=1.0, stop_loss=price, take_profit=price,
                reason=reason, source=self.name,
            )

        # ── 金額硬止損
        if self.max_loss_twd > 0:
            loss_pts = (position.entry_price - price) if is_long else (price - position.entry_price)
            if loss_pts > 0:
                loss_twd = loss_pts * position.quantity * self.point_value
                if loss_twd >= self.max_loss_twd:
                    return close_sig(f"金額止損 -{loss_twd:,.0f}TWD")

        # ── 強制盤末平倉
        # MXF夜盤：force_close=04:00 TST，條件 04:00<=bar<=05:00
        # QQQM UTC：force_close=20:45 UTC，條件只需 >= force_close（不限 <=05:00）
        if self._current_bar_time is not None:
            bar_time = self._current_bar_time.time()
            # 若 force_close_time 在凌晨附近（<= 06:00）→ MXF夜盤邏輯
            # 否則（e.g. 20:45 UTC）→ 直接以 >= force_close 觸發
            if self.force_close_time <= time(6, 0):
                triggered = bar_time >= self.force_close_time and bar_time <= time(5, 30)
            else:
                triggered = bar_time >= self.force_close_time
            if triggered:
                return close_sig("盤末強制平倉")

        # ── 時間出場
        if position.bars_since_entry >= self.max_bars:
            return close_sig(f"時間出場 {position.bars_since_entry}根")

        # ── 早切止損
        if self.early_cut_bars > 0 and position.bars_since_entry >= self.early_cut_bars:
            entry_p = position.entry_price
            loss_pts = (entry_p - price) if is_long else (price - entry_p)
            if loss_pts >= self.early_cut_loss_atr * atr:
                return close_sig(f"早切止損 {position.bars_since_entry}根 -{loss_pts:.0f}pts")

        # ── 追蹤止損
        entry = position.entry_price
        profit_pts = (price - entry) if is_long else (entry - price)
        profit_atr = profit_pts / atr

        if is_long:
            self._trail_best = max(self._trail_best, price)
            if profit_atr >= self.trail_trigger_atr:
                trail_stop = self._trail_best - self.trail_dist_atr * atr
                if price <= trail_stop:
                    return close_sig(f"追蹤出場 {trail_stop:.0f}")
        else:
            self._trail_best = min(self._trail_best, price)
            if profit_atr >= self.trail_trigger_atr:
                trail_stop = self._trail_best + self.trail_dist_atr * atr
                if price >= trail_stop:
                    return close_sig(f"追蹤出場 {trail_stop:.0f}")

        return None

    def get_parameters(self) -> dict:
        return {
            "orb_minutes": self.orb_minutes,
            "entry_filter_atr": self.entry_filter_atr,
            "adx_min": self.adx_min,
            "sl_type": self.sl_type,
            "sl_atr": self.sl_atr,
            "trail_trigger_atr": self.trail_trigger_atr,
            "trail_dist_atr": self.trail_dist_atr,
            "max_bars": self.max_bars,
        }

    def reset(self):
        self._orb_session_key = ""
        self._orb_high = 0.0
        self._orb_low = float('inf')
        self._orb_bar_count = 0
        self._orb_ready = False
        self._entered = False
        self._entry_atr = 0.0
        self._trail_best = 0.0
