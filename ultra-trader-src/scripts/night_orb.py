"""
paper_night_orb.py — TMF 夜盤 ORB Trading（B2 ML Filter）
===============================================================
功能：
  - 訂閱 TMF（微台）即時 Tick，聚合成 5 分鐘 K 棒
  - ORB 信號偵測（21:30 ~ 22:15 建立開盤區間）
  - B2 ML Filter（threshold=0.40，使用訓練時完全相同的特徵計算）
  - Mode：
    * paper：只 log 到 CSV（不下真實訂單、不需要 CA）
    * live：送真實 Market 單給永豐、log 到 live_night_orb_*.csv
    根據 .env TRADING_MODE 自動切換、或用 --paper / --live 強制覆寫

歷史備忘：
  - 2026-05-20 加入 live 模式、原合約 MXF 改 TMF（與日盤 breakout 一致）
  - 原檔名 paper_night_orb.py 保留（cron / watchdog 仍指此檔）
  - paper 行為與舊版 100% 一致

用法：
  python scripts/paper_night_orb.py              # 從 .env 讀 TRADING_MODE
  python scripts/paper_night_orb.py --paper      # 強制 paper（覆寫 .env）
  python scripts/paper_night_orb.py --live       # 強制 live
  python scripts/paper_night_orb.py --threshold 0.50
  python scripts/paper_night_orb.py --no-ml       # 純 Layer 1 對比記錄
"""

import argparse
import os
import sys
import time
import pickle
import threading
from collections import deque
from datetime import datetime, date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from loguru import logger

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")
load_dotenv(ROOT / ".env")

# ─── Mode flag（live / paper）─ 早 init 給後續 code 用 ───
# 預設先讀 .env、main() 內 argparse 可覆寫
_MODE: str = os.environ.get("TRADING_MODE", "paper").lower()
def _is_live() -> bool: return _MODE == "live"

# ─── TG 推送（沿用 core/notify.py 的 tg() 函式、失敗靜默）────
try:
    from core.notify import tg as _tg
except Exception:
    def _tg(msg: str): pass  # 沒 core/notify 就 noop

def tg_night(msg: str):
    """夜盤專用 TG 推送、依 mode 加不同前綴"""
    try:
        prefix = "🌙 [Night ORB LIVE]" if _is_live() else "🌙 [Night ORB]"
        _tg(f"{prefix} {msg}")
    except Exception:
        pass

# ─── 跨策略持倉鎖（與 engine.py 共用 data/active_position.json）────
try:
    from core import position_lock
except Exception:
    class _NoLock:
        @staticmethod
        def is_blocked(o): return None
        @staticmethod
        def acquire(**kw): pass
        @staticmethod
        def release(o): pass
    position_lock = _NoLock()

# ─── 路徑設定 ───────────────────────────────────────────────
MODEL_PATH    = ROOT / "deployed_strategies" / "tmf_orb_night" / "orb_filter_b2.pkl"
FEATURES_PATH = ROOT / "deployed_strategies" / "tmf_orb_night" / "selected_features_b2.txt"
PAPER_DIR     = ROOT / "data" / "paper_trading"
PAPER_DIR.mkdir(parents=True, exist_ok=True)

# ─── ORB 參數（v1 + B2）────────────────────────────────────
ORB_BARS          = 9          # 9 × 5min = 45 分鐘開盤區間
SESSION_START_H   = 21
SESSION_START_M   = 30
FORCE_CLOSE_H     = 4
FORCE_CLOSE_M     = 0
MIN_ORB_WIDTH_ATR = 3.0        # B1: 最小 ORB 寬度
MAX_ORB_WIDTH_ATR = 5.0        # B1: 最大 ORB 寬度
SL_ATR            = 2.0
TRAIL_TRIGGER_ATR = 0.8
TRAIL_DIST_ATR    = 1.25
MAX_BARS          = 60
EARLY_CUT_BARS    = 30
EARLY_CUT_LOSS    = 1.5


# ─── 特徵計算（與 optimizer/ml/features.py 相同邏輯）──────
def _compute_live_features(bars: list[dict]) -> dict:
    """
    從最近 300 根 5min bar 計算 B1 所需的 30 個特徵。
    bars: list of dict with keys open/high/low/close/volume，按時間升冪。
    """
    if len(bars) < 50:
        return {}

    df = pd.DataFrame(bars)
    c = df["close"].values.astype(np.float64)
    h = df["high"].values.astype(np.float64)
    lo = df["low"].values.astype(np.float64)
    v = df["volume"].values.astype(np.float64)
    n = len(c)

    def _lag(arr, k):
        out = np.empty_like(arr)
        out[:k] = np.nan
        out[k:] = arr[:-k]
        return out

    def _ema(arr, span):
        s = pd.Series(arr)
        return s.ewm(span=span, adjust=False).mean().values

    def _rolling(arr, w, fn):
        s = pd.Series(arr)
        return getattr(s.rolling(w, min_periods=1), fn)().values

    def _safe_div(a, b, fill=0.0):
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.where(np.abs(b) > 1e-8, a / b, fill)
        return r.astype(np.float32)

    # ATR-14 Wilder
    prev_c = np.empty(n); prev_c[0] = c[0]; prev_c[1:] = c[:-1]
    tr = np.maximum(h - lo, np.maximum(np.abs(h - prev_c), np.abs(lo - prev_c)))
    atr14 = pd.Series(tr).ewm(alpha=1/14, adjust=False).mean().values

    # ATR long MA (7-day ≈ 7×24/5 ≈ 84 bars for night session, use 50 as proxy)
    atr_long = _rolling(atr14, 50, "mean")

    # EMA
    ema5   = _ema(c, 5)
    ema10  = _ema(c, 10)
    ema20  = _ema(c, 20)
    ema200 = _ema(c, 200)

    # RSI
    def _rsi(arr, p):
        d = np.diff(arr)
        g = np.where(d > 0, d, 0.0)
        ls = np.where(d < 0, -d, 0.0)
        ag = np.mean(g[:p]) if len(g) >= p else 0.0
        al = np.mean(ls[:p]) if len(ls) >= p else 1e-8
        out = np.full(len(arr), 50.0)
        for i in range(p, len(arr) - 1):
            ag = (ag * (p - 1) + g[i]) / p
            al = (al * (p - 1) + ls[i]) / p
            out[i + 1] = 100 - 100 / (1 + ag / al) if al > 0 else 100.0
        return out.astype(np.float32)

    rsi14 = _rsi(c, 14)
    rsi7  = _rsi(c, 7)
    rsi21 = _rsi(c, 21)

    # BB(20)
    bb_mid = _rolling(c, 20, "mean")
    bb_std = _rolling(c, 20, "std")
    bb_upper = bb_mid + 2 * bb_std
    bb_lower = bb_mid - 2 * bb_std
    bb_width = _safe_div(bb_upper - bb_lower, bb_mid)
    bb_width_ma = _rolling(bb_width, 20, "mean")

    # ADX-14
    pdm = np.zeros(n); ndm = np.zeros(n)
    for i in range(1, n):
        up   = h[i] - h[i-1]
        down = lo[i-1] - lo[i]
        pdm[i] = up   if (up > down   and up   > 0) else 0.0
        ndm[i] = down if (down > up   and down > 0) else 0.0
    str14 = pd.Series(tr).ewm(alpha=1/14, adjust=False).mean().values
    spdm  = pd.Series(pdm).ewm(alpha=1/14, adjust=False).mean().values
    sndm  = pd.Series(ndm).ewm(alpha=1/14, adjust=False).mean().values
    pdi14 = _safe_div(100 * spdm, str14)
    mdi14 = _safe_div(100 * sndm, str14)
    dx    = _safe_div(np.abs(pdi14 - mdi14), pdi14 + mdi14) * 100
    adx14 = pd.Series(dx).ewm(alpha=1/14, adjust=False).mean().values

    # ADX-21
    str21 = pd.Series(tr).ewm(alpha=1/21, adjust=False).mean().values
    spdm21= pd.Series(pdm).ewm(alpha=1/21, adjust=False).mean().values
    sndm21= pd.Series(ndm).ewm(alpha=1/21, adjust=False).mean().values
    pdi21 = _safe_div(100 * spdm21, str21)
    mdi21 = _safe_div(100 * sndm21, str21)
    dx21  = _safe_div(np.abs(pdi21 - mdi21), pdi21 + mdi21) * 100
    adx21 = pd.Series(dx21).ewm(alpha=1/21, adjust=False).mean().values

    # Stochastic K(14,3)
    lo14 = _rolling(lo, 14, "min")
    hi14 = _rolling(h,  14, "max")
    stoch_k = _safe_div(100 * (c - lo14), hi14 - lo14, 50.0)
    stoch_d = _rolling(stoch_k, 3, "mean")

    # MACD(12,26,9)
    macd_fast = _ema(c, 12)
    macd_slow = _ema(c, 26)
    macd_line = macd_fast - macd_slow
    macd_sig  = _ema(macd_line, 9)
    macd_hist_arr = macd_line - macd_sig

    # Log returns / skew
    log_ret = np.diff(np.log(np.maximum(c, 1e-8)))
    log_ret = np.concatenate([[0.0], log_ret])
    ret_skew20 = pd.Series(log_ret).rolling(20, min_periods=5).skew().values

    # High-low range %
    hl_range_pct = _safe_div((h - lo).astype(np.float32), c) * 100.0

    # Distance from recent high/low (200)
    hi200 = _rolling(h,  200, "max")
    lo200 = _rolling(lo, 200, "min")
    dist_high200 = _safe_div(hi200 - c, atr14)
    dist_low200  = _safe_div(c - lo200, atr14)

    # Engulfing pattern
    prev_o = _lag(df["open"].values.astype(np.float64), 1)
    prev_c_ = _lag(c, 1)
    engulf = np.zeros(n, dtype=np.float32)
    for i in range(1, n):
        if prev_o[i] is not None and not np.isnan(prev_o[i]):
            bull = (c[i] > prev_o[i]) and (df["open"].values[i] < prev_c_[i])
            bear = (c[i] < prev_o[i]) and (df["open"].values[i] > prev_c_[i])
            engulf[i] = 1.0 if bull else (-1.0 if bear else 0.0)

    # Ret10 lag5
    ret10 = pd.Series(c).pct_change(10).values
    ret10_l5 = _lag(ret10, 5)

    # Volume-price trend
    vpt = np.cumsum(v * log_ret)

    # Close rolling std
    close_std20 = pd.Series(c).rolling(20, min_periods=1).std().values
    f_close_roll_std20 = _safe_div(close_std20, atr14)

    # Build feature dict (last bar only)
    i = -1
    def v_(arr): return float(arr[i]) if not np.isnan(arr[i]) else 0.0

    return {
        "f_engulfing":          v_(engulf),
        "f_ema5_vs_ema20_l5":   v_(_lag(_safe_div(ema5 - ema20, atr14), 5)),
        "f_rsi21":              v_(rsi21),
        "f_cci14_l1":           v_(_lag(_safe_div(c - _rolling(c, 14, "mean"),
                                                   0.015 * _rolling(c, 14, "std")), 1)),
        "f_hl_range_pct":       v_(hl_range_pct),
        "f_di_bull_strength":   v_(_safe_div(pdi14 - mdi14, atr14)),
        "f_pdi21":              v_(pdi21),
        "f_bb_squeeze":         float(bb_width[i] < bb_width_ma[i]),
        "f_ema20_slope":        v_(_safe_div(ema20 - _lag(ema20, 5), atr14) / 5.0),
        "f_adx_l3":             v_(_lag(adx14, 3)),
        "f_ema_align_x_adx":    v_(_safe_div(ema20 - ema200, atr14) * adx14 / 100.0),
        "f_bb_pos_l5":          v_(_lag(_safe_div(c - bb_lower, bb_upper - bb_lower, 0.5), 5)),
        "f_ema20_vs_ema200":    v_(_safe_div(ema20 - ema200, atr14)),
        "f_dist_recent_low200": v_(dist_low200),
        "f_adx21_l1":           v_(_lag(adx21, 1)),
        "f_ema10_slope":        v_(_safe_div(ema10 - _lag(ema10, 5), atr14) / 5.0),
        "f_high_vs_ema20":      v_(_safe_div(h - ema20, atr14)),
        "f_adx21_slope":        v_(_safe_div(adx21 - _lag(adx21, 3), atr14)),
        "f_rsi_ma5":            v_(_rolling(rsi14, 5, "mean")),
        "f_rsi":                v_(rsi14),
        "f_rsi7_vs_rsi21":      v_(rsi7 - rsi21),
        "f_pdi_l1":             v_(_lag(pdi14, 1)),
        "f_dist_recent_high200":v_(dist_high200),
        "f_macd_hist":          v_(macd_hist_arr),
        "f_ret10_l5":           v_(ret10_l5),
        "f_vol_price_trend":    v_(pd.Series(vpt).diff(5).values),
        "f_adx21_l3":           v_(_lag(adx21, 3)),
        "f_ret_skew20":         v_(ret_skew20),
        "f_stoch_k_l3":         v_(_lag(stoch_k, 3)),
        "f_close_roll_std20":   v_(f_close_roll_std20),
    }


# ─── B2 ML Filter ───────────────────────────────────────────
class OrbMLFilter:
    def __init__(self, threshold: float = 0.40, enabled: bool = True):
        self.threshold = threshold
        self.enabled = enabled
        if enabled:
            with open(MODEL_PATH, "rb") as f:
                self.model = pickle.load(f)
            self.features = [l.strip() for l in FEATURES_PATH.read_text().splitlines()
                             if l.strip()]
            logger.info(f"[ML] B2 model loaded, threshold={threshold}, features={len(self.features)}")
        else:
            logger.info("[ML] Disabled — pure Layer 1 ORB")

    def predict(self, feat_dict: dict) -> tuple[bool, float]:
        if not self.enabled:
            return True, 1.0
        if not feat_dict:
            return False, 0.0
        row = {f: feat_dict.get(f, 0.0) for f in self.features}
        X = pd.DataFrame([row])[self.features]
        prob = float(self.model.predict_proba(X)[0, 1])
        return prob >= self.threshold, prob


# ─── Trade Logger（live 跟 paper 分檔）─────────────────────────
class PaperLogger:
    """通用 trade logger、live mode 也用同一個 class、檔名加前綴區分。"""

    def __init__(self):
        prefix = "live_night_orb" if _is_live() else "night_orb"
        fname = f"{prefix}_{date.today().strftime('%Y%m%d')}.csv"
        self.path = PAPER_DIR / fname
        self._write_header()
        logger.info(f"[{'Live' if _is_live() else 'Paper'}] Log → {self.path}")

    def _write_header(self):
        if not self.path.exists():
            with open(self.path, "w", encoding="utf-8") as f:
                f.write("entry_time,exit_time,direction,entry_price,exit_price,"
                        "stop_loss,ml_prob,ml_pass,exit_reason,r_multiple,"
                        "orb_high,orb_low,orb_width_atr,session,mode,quantity\n")

    def log(self, rec: dict):
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(",".join(str(rec.get(k, "")) for k in [
                "entry_time", "exit_time", "direction", "entry_price",
                "exit_price", "stop_loss", "ml_prob", "ml_pass",
                "exit_reason", "r_multiple", "orb_high", "orb_low",
                "orb_width_atr", "session", "mode", "quantity"
            ]) + "\n")
        r = rec.get("r_multiple", 0)
        tag = "[Live]" if rec.get("mode") == "live" else "[Paper]"
        logger.info(f"{tag} CLOSED {rec['direction']} R={r:+.3f} ({rec['exit_reason']})")


# ─── Live Order Manager（送真實單、追蹤成交回報）─────────────────
class LiveOrderManager:
    """
    集中管理 live 模式真實單送出 + 成交追蹤。

    使用：
        mgr = LiveOrderManager(api, contract)
        avg, qty = mgr.place_market(side="Buy", qty=1, timeout=8)
        avg, qty = mgr.close_market(side="Sell", qty=1, timeout=8)

    執行緒模型（重要）：
      - SDK 內部 callback thread 推 deal event → set_order_callback 接收
      - on_tick callback thread 觸發 place_market / close_market
      - 兩 thread 共用 self._deal_evt（threading.Event）等待 fill
      - 用 self._lock 序列化 place_order 呼叫、避免兩筆同時撞 broker
    """

    def __init__(self, api, contract):
        self.api = api
        self.contract = contract
        self._lock = threading.Lock()
        self._deal_evt = threading.Event()
        self._last_deal: dict | None = None
        # 強 ref 防 GC（同 broker.py 5/20 教訓、見 [[shioaji-1-3-3-live-callback-race]]）
        self._cb_ref = self._on_order
        api.set_order_callback(self._cb_ref)
        logger.info("[Live] order callback registered")

    def _on_order(self, stat, msg):
        """成交回報 callback。SDK 內部 thread 呼叫、不可在此 call api.*"""
        try:
            logger.info(f"[Live] order_cb stat={stat} | {msg}")
            if hasattr(msg, "price") and hasattr(msg, "quantity"):
                self._last_deal = {
                    "action":   str(getattr(msg, "action", "")),
                    "quantity": int(msg.quantity),
                    "price":    float(msg.price),
                    "code":     str(getattr(msg, "code", "")),
                }
                self._deal_evt.set()
        except Exception as e:
            logger.error(f"[Live] order_cb error (swallowed): {e}")

    def _wait_deal(self, timeout: float) -> dict | None:
        if self._deal_evt.wait(timeout):
            d = self._last_deal
            return d
        return None

    def _place(self, side: str, qty: int, timeout: float) -> tuple[float, int]:
        """送 Market order、等成交、回 (avg_price, filled_qty)。
        失敗 / timeout 回 (0.0, 0)。"""
        import shioaji as sj
        with self._lock:
            self._deal_evt.clear()
            self._last_deal = None
            try:
                order = self.api.Order(
                    action=getattr(sj.constant.Action, side),     # Buy / Sell
                    price=0,                                       # market 不需要價
                    quantity=qty,
                    price_type=sj.constant.FuturesPriceType.MKT,
                    order_type=sj.constant.OrderType.IOC,
                    octype=sj.constant.FuturesOCType.Auto,
                    account=self.api.futopt_account,
                )
                trade = self.api.place_order(self.contract, order)
                logger.info(f"[Live] place_order sent: {side} x{qty} | trade={trade}")
            except Exception as e:
                logger.error(f"[Live] place_order failed: {e}")
                return 0.0, 0
        # 等成交（lock 已 release、不阻塞 callback）
        deal = self._wait_deal(timeout)
        if not deal:
            logger.error(f"[Live] {side} 等成交 timeout({timeout}s)、嘗試查 update_status")
            try:
                self.api.update_status(self.api.futopt_account)
            except Exception:
                pass
            return 0.0, 0
        return deal["price"], deal["quantity"]

    def place_market(self, side: str, qty: int, timeout: float = 8.0) -> tuple[float, int]:
        """進場 market（Buy=做多、Sell=做空）"""
        return self._place(side, qty, timeout)

    def close_market(self, side: str, qty: int, timeout: float = 8.0) -> tuple[float, int]:
        """出場 market（多單 close 用 Sell、空單 close 用 Buy）"""
        return self._place(side, qty, timeout)


# ─── 5-min Bar Aggregator ───────────────────────────────────
class Bar5mAggregator:
    def __init__(self, callback):
        self.cb = callback
        self._cur = None

    def on_tick(self, ts: datetime, price: float, vol: int):
        bar_time = ts.replace(second=0, microsecond=0)
        bar_time = bar_time.replace(minute=(bar_time.minute // 5) * 5)

        if self._cur is None or bar_time != self._cur["time"]:
            if self._cur:
                self.cb(self._cur)
            self._cur = {"time": bar_time, "open": price, "high": price,
                         "low": price, "close": price, "volume": vol}
        else:
            self._cur["high"]  = max(self._cur["high"], price)
            self._cur["low"]   = min(self._cur["low"], price)
            self._cur["close"] = price
            self._cur["volume"] += vol


# ─── Night ORB Engine ────────────────────────────────────────
class NightORBEngine:
    def __init__(self, ml_filter: OrbMLFilter, paper: PaperLogger,
                 order_mgr=None, sizer=None):
        self.ml = ml_filter
        self.paper = paper
        self.order_mgr = order_mgr    # None=paper mode、LiveOrderManager=live mode
        self.sizer = sizer            # None=paper、PositionSizer=live
        self.bars: deque = deque(maxlen=300)   # rolling 300 bars for features

        # session state
        self._session: str = ""
        self._orb_high = 0.0
        self._orb_low  = float("inf")
        self._orb_count = 0
        self._orb_ready = False
        self._session_done = False

        # trade state
        self._in_trade = False
        self._direction = 0
        self._entry_price = 0.0
        self._stop_price = 0.0
        self._atr_entry = 0.0
        self._trail_stop = 0.0
        self._trail_active = False
        self._bars_held = 0
        self._entry_time = None
        self._orb_high_e = 0.0
        self._orb_low_e  = 0.0
        self._orb_w_atr  = 0.0
        self._ml_prob    = 0.0
        self._session_entry = ""
        self._quantity = 0           # 真實成交口數（live）/ 訊號口數 1（paper）

    def _get_session(self, ts: datetime) -> str:
        t = ts.time()
        from datetime import time as dtime
        ss = dtime(SESSION_START_H, SESSION_START_M)
        if t >= ss:
            return ts.strftime("%Y-%m-%d") + "-N"
        elif t <= dtime(5, 30):
            prev = (ts - timedelta(days=1)).strftime("%Y-%m-%d")
            return prev + "-N"
        return ""

    def _atr(self) -> float:
        if len(self.bars) < 14:
            return 1.0
        bars = list(self.bars)[-14:]
        trs = []
        for i in range(1, len(bars)):
            tr = max(bars[i]["high"] - bars[i]["low"],
                     abs(bars[i]["high"] - bars[i-1]["close"]),
                     abs(bars[i]["low"]  - bars[i-1]["close"]))
            trs.append(tr)
        return float(np.mean(trs)) if trs else 1.0

    def on_bar(self, bar: dict):
        ts    = bar["time"]
        price = bar["close"]
        high  = bar["high"]
        low   = bar["low"]

        # 加入 buffer
        self.bars.append({
            "open": bar["open"], "high": high, "low": low,
            "close": price, "volume": bar["volume"],
        })

        sess = self._get_session(ts)
        if not sess:
            # 非 session 時段
            if self._in_trade:
                self._close("session_end", price, ts)
            return

        from datetime import time as dtime
        # 強制平倉時段（04:00~05:00，每根 K 棒都觸發直到平倉成功）
        t = ts.time()
        if (dtime(FORCE_CLOSE_H, FORCE_CLOSE_M) <= t <= dtime(5, 0)):
            if self._in_trade:
                logger.warning(f"[ForceClose] {t} 強制平倉 @ {price:.0f}")
                self._close("force_close", price, ts)
            return

        # 新 session
        if sess != self._session:
            self._session = sess
            self._orb_high = 0.0
            self._orb_low  = float("inf")
            self._orb_count = 0
            self._orb_ready = False
            self._session_done = False
            logger.info(f"[ORB] New session: {sess}")

        atr = self._atr()

        # ── 管理持倉出場
        if self._in_trade:
            self._bars_held += 1
            pnl = (price - self._entry_price) * self._direction
            exit_p = None; reason = ""

            # 止損
            if self._direction == 1 and low <= self._stop_price:
                exit_p = self._stop_price; reason = "stop_loss"
            elif self._direction == -1 and high >= self._stop_price:
                exit_p = self._stop_price; reason = "stop_loss"

            # 追蹤止損
            if exit_p is None:
                pnl_atr = pnl / self._atr_entry if self._atr_entry > 0 else 0.0
                if pnl_atr >= TRAIL_TRIGGER_ATR:
                    self._trail_active = True
                if self._trail_active:
                    if self._direction == 1:
                        new_t = price - TRAIL_DIST_ATR * self._atr_entry
                        self._trail_stop = max(self._trail_stop, new_t)
                        if low <= self._trail_stop:
                            exit_p = self._trail_stop; reason = "trail_stop"
                    else:
                        new_t = price + TRAIL_DIST_ATR * self._atr_entry
                        self._trail_stop = min(self._trail_stop, new_t)
                        if high >= self._trail_stop:
                            exit_p = self._trail_stop; reason = "trail_stop"

            # 早切
            if exit_p is None and self._bars_held >= EARLY_CUT_BARS:
                if pnl < -EARLY_CUT_LOSS * self._atr_entry:
                    exit_p = price; reason = "early_cut"

            # 時間出場
            if exit_p is None and self._bars_held >= MAX_BARS:
                exit_p = price; reason = "max_bars"

            if exit_p is not None:
                self._close(reason, exit_p, ts)
            return

        # ── ORB 區間建立（session_done 後直接 short-circuit、避免 TG 重複 spam）
        if self._session_done:
            return

        if not self._orb_ready:
            self._orb_count += 1
            self._orb_high = max(self._orb_high, high)
            self._orb_low  = min(self._orb_low, low)
            if self._orb_count >= ORB_BARS:
                # ATR warm-up check：bars < 14 時 _atr 回 default 1.0、width/1.0 暴量、不可信
                if len(self.bars) < 14:
                    logger.warning(f"[ORB] count={self._orb_count} 但 bars 只有 {len(self.bars)} 不到 14、ATR 不可信、本夜放棄")
                    tg_night(f"區間跳過：ATR warm-up 不足（bars={len(self.bars)}）、今夜停手")
                    self._session_done = True
                    return
                orb_w = self._orb_high - self._orb_low
                orb_w_atr = orb_w / atr if atr > 0 else 0.0
                if MIN_ORB_WIDTH_ATR <= orb_w_atr <= MAX_ORB_WIDTH_ATR:
                    self._orb_ready = True
                    logger.info(f"[ORB] Range ready: [{self._orb_low:.0f},{self._orb_high:.0f}] "
                                f"width={orb_w_atr:.2f}×ATR")
                    tg_night(f"區間建立 [{self._orb_low:.0f},{self._orb_high:.0f}] width={orb_w_atr:.2f}×ATR")
                else:
                    self._session_done = True
                    logger.info(f"[ORB] Range skipped: width={orb_w_atr:.2f}×ATR "
                                f"(need {MIN_ORB_WIDTH_ATR}-{MAX_ORB_WIDTH_ATR})")
                    tg_night(f"區間跳過 width={orb_w_atr:.2f}×ATR 不在 {MIN_ORB_WIDTH_ATR}-{MAX_ORB_WIDTH_ATR} 範圍、今夜停手")
            return

        # ── 突破偵測（需 > 22:15）
        from datetime import time as dtime
        entry_after = dtime(SESSION_START_H, SESSION_START_M + ORB_BARS * 5 // 60,
                            (SESSION_START_M + ORB_BARS * 5) % 60)
        cross_after = t >= entry_after or t <= dtime(5, 30)
        if not cross_after:
            return

        orb_w_atr = (self._orb_high - self._orb_low) / atr if atr > 0 else 0.0

        direction = 0
        if price > self._orb_high:
            direction = 1
        elif price < self._orb_low:
            direction = -1

        if direction == 0:
            return

        # ── B1 ML Filter
        feats = _compute_live_features(list(self.bars))
        ml_pass, ml_prob = self.ml.predict(feats)

        sig_dir = 'LONG' if direction==1 else 'SHORT'
        logger.info(f"[ORB] Signal {sig_dir} @ {price:.0f} "
                    f"| ML prob={ml_prob:.3f} {'✅ PASS' if ml_pass else '❌ SKIP'}")
        tg_night(f"訊號 {sig_dir} @ {price:.0f} | ML prob={ml_prob:.3f} {'✅ PASS' if ml_pass else '❌ SKIP'}")

        if not ml_pass:
            self._session_done = True
            return

        # ──【跨策略持倉鎖】── 若 breakout 已持倉、ORB 跳過進場
        blocker = position_lock.is_blocked("orb")
        if blocker:
            logger.info(f"[Lock] ORB 進場跳過：{blocker.get('owner')} 已持倉 "
                        f"({blocker.get('side')} {blocker.get('instrument')} @ {blocker.get('entry_price')})")
            tg_night(f"⛔ 訊號跳過：{blocker.get('owner')} 已持倉")
            self._session_done = True
            return

        # ── 進場
        sl = price - SL_ATR * atr * direction
        enter_dir = 'LONG' if direction == 1 else 'SHORT'

        # 算口數（live: PositionSizer 公式、paper: 固定 1）
        stop_dist = abs(price - sl)
        if self.sizer is not None:
            qty = self.sizer.calculate(
                account_balance=float(os.environ.get("INITIAL_BALANCE", "95000")),
                stop_distance=stop_dist,
                point_value=10.0,   # TMF
            )
            if qty <= 0:
                logger.warning(f"[Live] PositionSizer 算出 qty=0、跳過進場")
                tg_night(f"⚠️ 訊號跳過 qty=0（風險過大）")
                self._session_done = True
                return
        else:
            qty = 1

        # Live 模式：先送真實單、等成交、用真實成交價當 entry_price
        actual_price = price
        actual_qty = qty
        if self.order_mgr is not None:
            side = "Buy" if direction == 1 else "Sell"
            logger.info(f"[Live] sending market {side} x{qty}...")
            avg, filled = self.order_mgr.place_market(side, qty, timeout=8.0)
            if filled <= 0 or avg <= 0:
                logger.error(f"[Live] 進場失敗（filled={filled}、avg={avg}）、放棄此訊號")
                tg_night(f"🚫 進場失敗 {enter_dir} 未成交、放棄")
                self._session_done = True
                return
            actual_price = avg
            actual_qty = filled
            # 真實成交價 ≠ 訊號價、重算 SL
            sl = actual_price - SL_ATR * atr * direction

        self._in_trade    = True
        self._direction   = direction
        self._entry_price = actual_price
        self._stop_price  = sl
        self._atr_entry   = atr
        self._trail_stop  = sl
        self._trail_active= False
        self._bars_held   = 0
        self._entry_time  = ts
        self._orb_high_e  = self._orb_high
        self._orb_low_e   = self._orb_low
        self._orb_w_atr   = orb_w_atr
        self._ml_prob     = ml_prob
        self._session_entry = sess
        self._session_done = True
        self._quantity = actual_qty

        mode_tag = "LIVE" if self.order_mgr else "PAPER"
        logger.info(f"[Trade] ENTER {enter_dir} x{actual_qty} "
                    f"@ {actual_price:.0f}  SL={sl:.0f}  ATR={atr:.1f} ({mode_tag})")
        tg_night(f"📥 進場 {enter_dir} x{actual_qty} @ {actual_price:.0f}  "
                 f"SL={sl:.0f}  ATR={atr:.1f}  ML={ml_prob:.2f}")
        # 取得跨策略持倉鎖
        position_lock.acquire(
            owner="orb", side=enter_dir.lower(),
            entry_price=float(actual_price), instrument="TMF", quantity=actual_qty,
            mode=("live" if self.order_mgr else "paper"),
            reason=f"ORB B2 ML prob={ml_prob:.3f}",
        )

    def _close(self, reason: str, price: float, ts: datetime):
        # Live 模式：先送真實平倉單、用真實成交價算 PnL
        actual_exit = price
        if self.order_mgr is not None and self._quantity > 0:
            # 平倉方向：原 LONG → Sell 平倉、原 SHORT → Buy 平倉
            close_side = "Sell" if self._direction == 1 else "Buy"
            logger.info(f"[Live] sending close market {close_side} x{self._quantity} ({reason})")
            avg, filled = self.order_mgr.close_market(close_side, self._quantity, timeout=8.0)
            if filled > 0 and avg > 0:
                actual_exit = avg
            else:
                logger.error(f"[Live] 平倉失敗（filled={filled}、avg={avg}）、"
                             f"sticky！log 用 signal price={price}、必須手動 App 平倉")
                tg_night(f"🚨 平倉失敗 {reason} @ {price:.0f} 未成交、"
                         f"持倉口數 {self._quantity}、立即手動 App 平倉！")
                # 不 release lock、避免下一個訊號又進場、必須人工介入
                return

        pnl = (actual_exit - self._entry_price) * self._direction
        risk = abs(self._entry_price - self._stop_price)
        r_mult = pnl / risk if risk > 0 else 0.0
        mode_str = "live" if self.order_mgr else "paper"
        rec = {
            "entry_time":   self._entry_time,
            "exit_time":    ts,
            "direction":    "LONG" if self._direction == 1 else "SHORT",
            "entry_price":  round(self._entry_price, 1),
            "exit_price":   round(actual_exit, 1),
            "stop_loss":    round(self._stop_price, 1),
            "ml_prob":      round(self._ml_prob, 4),
            "ml_pass":      True,
            "exit_reason":  reason,
            "r_multiple":   round(r_mult, 4),
            "orb_high":     round(self._orb_high_e, 1),
            "orb_low":      round(self._orb_low_e, 1),
            "orb_width_atr":round(self._orb_w_atr, 3),
            "session":      self._session_entry,
            "mode":         mode_str,
            "quantity":     self._quantity or 1,
        }
        self.paper.log(rec)
        self._in_trade = False
        self._quantity = 0
        # TG 推送出場
        emoji = "🎯" if r_mult > 0 else "🛑"
        tg_night(f"{emoji} 出場 {rec['direction']} @ {actual_exit:.0f} "
                 f"進場 {self._entry_price:.0f} | R={r_mult:+.2f} | 原因: {reason}")
        # 釋放跨策略持倉鎖
        position_lock.release("orb")


# ─── 強制平倉守護執行緒 ───────────────────────────────────────
def _force_close_guard(engine: "NightORBEngine", api, contract, stop_event: threading.Event):
    """
    獨立執行緒：每分鐘檢查時間，04:05~04:45 若仍有持倉強制平倉。
    防範 K 棒停止進入（市場極度流動性不足）時 on_bar 無法觸發的情況。
    """
    from datetime import time as dtime
    while not stop_event.is_set():
        time.sleep(30)
        now_t = datetime.now().time()
        if dtime(4, 5) <= now_t <= dtime(4, 45) and engine._in_trade:
            logger.warning(f"[Guard] 04:00 後仍持倉！守護執行緒強制平倉")
            try:
                snap = api.snapshots([contract])
                p = float(snap[0].close) if snap else engine._entry_price
            except Exception:
                p = engine._entry_price
            engine._close("guard_force_close", p, datetime.now())


# ─── Main ────────────────────────────────────────────────────
def main():
    global _MODE
    parser = argparse.ArgumentParser()
    parser.add_argument("--threshold", type=float, default=0.40)
    parser.add_argument("--no-ml", action="store_true")
    parser.add_argument("--live", action="store_true", help="強制 live 模式（覆寫 .env）")
    parser.add_argument("--paper", action="store_true", help="強制 paper 模式（覆寫 .env）")
    args = parser.parse_args()

    # Argparse 覆寫 .env
    if args.live:
        _MODE = "live"
    elif args.paper:
        _MODE = "paper"
    mode_label = "LIVE" if _is_live() else "Paper"

    logger.info("=" * 55)
    logger.info(f"  TMF 夜盤 ORB {mode_label} Trading — B2 ML Filter")
    logger.info(f"  ML: {'disabled' if args.no_ml else f'enabled (threshold={args.threshold})'}")
    logger.info(f"  Mode: {_MODE}")
    logger.info("=" * 55)

    ml = OrbMLFilter(threshold=args.threshold, enabled=not args.no_ml)
    paper = PaperLogger()

    # ── Shioaji 登入
    # 2026-05-22 修法：login(fetch_contract=False) + 手動 fetch_contracts with retry
    # 對應 broker.py 同樣修法、見 [[sinopac_fetch_contracts_partial_after_reset]]
    # 永豐 quota reset 後偶爾 contract server partial fail、過去 5/13 / 5/18 / 5/22 都撞過
    import shioaji as sj
    api = sj.Shioaji(simulation=False)
    api.login(
        api_key=os.environ["SHIOAJI_API_KEY"],
        secret_key=os.environ["SHIOAJI_SECRET_KEY"],
        receive_window=300000,
        fetch_contract=False,
    )
    logger.info("[Login] Shioaji OK")

    # 手動 fetch_contracts + retry（最多 3 次、間隔 60s 給永豐 server 穩定時間）
    for _retry in range(3):
        try:
            api.fetch_contracts(contracts_timeout=30000)
            if _retry > 0:
                logger.info(f"[Shioaji] fetch_contracts retry #{_retry} succeeded")
            break
        except Exception as fc_err:
            logger.warning(f"[Shioaji] fetch_contracts attempt #{_retry+1} partial: {fc_err}")
            if _retry < 2:
                time.sleep(60)

    # Sanity check：確認 TMF contract 真的可用
    try:
        _test_contract = api.Contracts.Futures.TMF.TMFR1
        if _test_contract is None or not getattr(_test_contract, "code", ""):
            logger.error("[Shioaji] TMFR1 contract 未載入完整、放棄啟動")
            tg_night("🚨 啟動失敗：TMF 合約未載入完整、夜盤 ORB 無法運作")
            sys.exit(1)
    except Exception as e:
        logger.error(f"[Shioaji] TMF contract 取得失敗: {e}")
        tg_night(f"🚨 啟動失敗：TMF 合約取得失敗 {e}")
        sys.exit(1)

    # ── Live 模式必須 activate_ca + 確認 futopt_account.signed
    order_mgr = None
    sizer = None
    if _is_live():
        ca_path = os.environ.get("SHIOAJI_CA_PATH", "")
        ca_pw = os.environ.get("SHIOAJI_CA_PASSWORD", "")
        person_id = os.environ.get("SHIOAJI_PERSON_ID", "")
        if not ca_path:
            logger.error("[Live] SHIOAJI_CA_PATH 未設定、無法 activate_ca、放棄 live mode")
            sys.exit(1)
        api.activate_ca(ca_path=ca_path, ca_passwd=ca_pw, person_id=person_id)
        logger.info("[Live] CA activated")
        if not api.futopt_account or not getattr(api.futopt_account, "signed", False):
            logger.error("[Live] futopt_account.signed=False、API 同意書未簽、放棄 live mode")
            sys.exit(1)
        logger.info(f"[Live] futopt_account signed: {api.futopt_account.account_id}")

        # PositionSizer：跟日盤共用 RISK_PROFILE
        from risk.position_sizing import PositionSizer
        profile = os.environ.get("RISK_PROFILE", "balanced")
        sizer = PositionSizer(profile)
        logger.info(f"[Live] PositionSizer profile={profile} "
                    f"max_contracts={sizer.preset.max_contracts}")

    # ── 合約 TMF（微台、與日盤 breakout 一致、5/20 從 MXF 改）
    contract = api.Contracts.Futures.TMF.TMFR1
    logger.info(f"[Contract] {contract.code} ({contract.name})")

    # ── Live 模式：起 LiveOrderManager
    if _is_live():
        order_mgr = LiveOrderManager(api, contract)

    # ── Engine 帶 order_mgr + sizer（paper 模式為 None）
    engine = NightORBEngine(ml, paper, order_mgr=order_mgr, sizer=sizer)
    agg = Bar5mAggregator(engine.on_bar)

    # ── Tick heartbeat 狀態（monotonic clock 防 NTP 跳動）
    _hb_state = {"last": None, "disconnected": False}

    # ── Tick 訂閱
    def on_tick(exchange, tick):
        _hb_state["last"] = time.monotonic()
        if _hb_state["disconnected"]:
            _hb_state["disconnected"] = False
            tg_night(f"✅ Tick 恢復 @ {datetime.now():%H:%M:%S}")
            logger.info("[Heartbeat] Tick 恢復")
        try:
            ts = datetime.fromtimestamp(tick.datetime / 1e9) \
                if isinstance(tick.datetime, (int, float)) else tick.datetime
            agg.on_tick(ts, float(tick.close), int(tick.volume))
        except Exception as e:
            logger.error(f"[Tick] {e}")

    api.quote.subscribe(contract, quote_type=sj.constant.QuoteType.Tick)
    api.quote.set_on_tick_fop_v1_callback(on_tick)

    # ── 強制平倉守護執行緒
    stop_event = threading.Event()
    guard_thread = threading.Thread(
        target=_force_close_guard,
        args=(engine, api, contract, stop_event),
        daemon=True,
        name="ForceCloseGuard",
    )
    guard_thread.start()

    # ── Tick heartbeat 監控（>120s 無 tick 視為斷線、僅在 21:00-04:30 主時段告警）
    def _tick_heartbeat(stop_ev, timeout_sec=120):
        from datetime import time as dtime
        while not stop_ev.is_set():
            time.sleep(10)
            t = datetime.now().time()
            in_window = (t >= dtime(21, 0)) or (t <= dtime(4, 30))
            if not in_window:
                continue
            last = _hb_state["last"]
            if last is None:
                continue  # 還沒收到第一筆 tick
            elapsed = time.monotonic() - last
            if elapsed > timeout_sec and not _hb_state["disconnected"]:
                _hb_state["disconnected"] = True
                logger.error(f"[Heartbeat] {elapsed:.0f}s 未收到 tick")
                tg_night(f"🚨 Tick 中斷 {elapsed:.0f}s 沒收到 @ {datetime.now():%H:%M:%S}")

    hb_thread = threading.Thread(
        target=_tick_heartbeat,
        args=(stop_event,),
        daemon=True,
        name="TickHeartbeat",
    )
    hb_thread.start()

    logger.info(f"[Subscribe] {contract.code} tick feed active. Waiting for bars...")
    logger.info(f"[{'Live' if _is_live() else 'Paper'}] Log: {paper.path}")
    logger.info("Press Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("\n[Stop] Ctrl+C received.")
    finally:
        stop_event.set()
        if engine._in_trade:
            snap = api.snapshots([contract])
            p = snap[0].close if snap else engine._entry_price
            engine._close("manual_stop", p, datetime.now())
        api.quote.unsubscribe(contract, quote_type=sj.constant.QuoteType.Tick)
        api.logout()
        logger.info("[Done] Logged out.")


if __name__ == "__main__":
    main()
