"""
Stage 3 (ORB 版本): Opening Range Breakout — Signal Detection + Labeling
=========================================================================
專為夜盤 ORB 策略設計的 ML 訓練標籤生成器。

與 labels.py（Breakout 版）的差異：
  labels.py    → 偵測 ADX+BB 擠壓突破信號（日盤用）
  labels_orb.py → 偵測開盤區間突破信號（夜盤用）

Pipeline 位置：
  Stage 1: collect_data.py   → {instrument}_5m.parquet
  Stage 2: features.py       → {instrument}_features.parquet
  Stage 3: labels_orb.py     → {instrument}_orb_signals.parquet + ml_orb_dataset.parquet
  Stage 4+: cv.py / models.py / ...（與 Breakout 版共用）

ORB 信號邏輯：
  1. 每個 session 開始後，收集前 N 根 5min K（開盤區間）
  2. 記錄開盤區間的最高價（orb_high）和最低價（orb_low）
  3. 區間建立後：收盤突破 orb_high → LONG；突破 orb_low → SHORT
  4. 每個 session 最多一筆交易
  5. 出場：ATR 追蹤止損 / 早切止損 / max_bars 時間出場 / session 結束

訓練商品選擇（與 labels.py 不同）：
  夜盤 MXF 的 ORB 在 21:30 TST（= US 09:30 ET）開始
  → 訓練資料應使用「美股開盤 ORB」的商品：QQQM、SPXL、TECL、TQQQ、IVV 等
  → 這些商品的 ORB 在 14:30 UTC（09:30 ET）開始，與 MXF 夜盤同步
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from datetime import time
from typing import Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent.parent
ML_DATA_DIR = ROOT / "data" / "historical" / "ml"

# ─────────────────────────────────────────────────────────────────────────────
# ORB 商品設定
# ─────────────────────────────────────────────────────────────────────────────

# MXF/TMF 夜盤：21:30 TST 開始，04:00 TST 強制平倉
_MXF_ORB_CFG = {
    "session_start":    time(21, 30),   # 夜盤開始（TST）
    "session_end":      time(4, 0),     # 強制平倉時間（次日 04:00 TST）
    "crosses_midnight": True,           # 跨夜 session
    "orb_bars":         9,              # 開盤區間根數（9×5min = 45min）
    "entry_after":      time(22, 15),   # 最早進場時間（ORB 結束後）
    "sl_atr":           2.0,
    "max_sl_pts":       120.0,         # 停損點數上限（防 ATR 極大時 SL 過寬，如 3/25 -233pts）
    "trail_trigger_atr":0.8,           # 獲利超過 0.8×ATR 才啟動追蹤（原值正確）
    "trail_dist_atr":   0.3,           # 追蹤距離縮小（原值 1.25 > trigger 0.8 → 必定虧損出場）
    "max_bars":         60,             # 5hr（5×60=300min）
    "early_cut_bars":   30,             # 2.5hr
    "early_cut_loss_atr": 1.5,
    # ── 方案 B1：TMF 只保留 ORB 寬度過濾（移除 EMA200 和 RSI）
    # 台指夜盤受美股情緒驅動，EMA200 趨勢過濾在夜盤反效果
    "min_orb_width_atr": 3.0,          # ORB 寬度下限（太窄=假突破）
    "max_orb_width_atr": 5.0,          # ORB 寬度上限（太寬=混亂市場）
    "max_breakout_vol_ratio": 2.0,     # B2：突破量過濾（高量假突破排除）
    "trend_filter":     None,          # B1: TMF 不套 EMA200 趨勢過濾
    # rsi_max_long / rsi_min_short 不設 → 使用預設值（100/0，即不過濾）
    "file_alias":       "TMF_night",   # 實際檔名前綴（TMF_night_5m.parquet）
}

# 美股 ETF / 槓桿 ETF：14:30 UTC（= 09:30 ET）開盤，21:00 UTC 收盤
# ORB 在 14:30 UTC 開始（與 MXF 夜盤 21:30 TST 同步）
_US_ORB_CFG = {
    "session_start":    time(14, 30),   # 09:30 ET = 14:30 UTC
    "session_end":      time(21, 0),    # 16:00 ET = 21:00 UTC
    "crosses_midnight": False,
    "orb_bars":         9,              # 9×5min = 45min（ORB 結束於 15:15 UTC）
    "entry_after":      time(15, 15),   # 最早進場（ORB 結束後）
    "sl_atr":           2.0,
    "trail_trigger_atr":0.8,
    "trail_dist_atr":   1.25,
    "max_bars":         72,             # ~6hr 美股交易時段
    "early_cut_bars":   30,
    "early_cut_loss_atr": 1.5,
    # ── 方案 B 信號品質過濾器 ──
    "min_orb_width_atr": 3.0,
    "max_orb_width_atr": 5.0,
    "trend_filter":     "ema200",
    "rsi_max_long":     70,
    "rsi_min_short":    30,
}

INSTRUMENT_ORB_CONFIGS: dict[str, dict] = {
    # 部署目標（MXF 夜盤）
    # instrument 名稱為 "TMF"（與 pipeline gate 一致）
    # 實際資料檔案: TMF_night_5m.parquet（由 file_alias 欄位指定）
    "TMF": _MXF_ORB_CFG,

    # 美股訓練資料（與 MXF 夜盤同步開盤，最佳訓練來源）
    "SPXL":   _US_ORB_CFG,              # S&P 500 3x — 樣本最多
    "TECL":   _US_ORB_CFG.copy(),       # Tech 3x — 與 TMF 結構最相近
    "TQQQ":   _US_ORB_CFG.copy(),       # NQ 3x
    "QQQM":   _US_ORB_CFG.copy(),       # NQ100 — 夜盤 MXF 的主要參考指標
    "IVV":    _US_ORB_CFG.copy(),       # SPX
    "XLK":    _US_ORB_CFG.copy(),       # IT sector
    "SOXX":   _US_ORB_CFG.copy(),       # 半導體
    "IBB":    _US_ORB_CFG.copy(),       # Biotech
    "FNGS":   _US_ORB_CFG.copy(),       # FANG+
}

# ─────────────────────────────────────────────────────────────────────────────
# 指標計算（與 labels.py 共用，只保留 ORB 所需）
# ─────────────────────────────────────────────────────────────────────────────

def _compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """計算 ATR 和基礎指標（ORB 只需要 ATR）"""
    df = df.copy()
    high, low, close = df["high"], df["low"], df["close"]

    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    tr.iloc[0] = high.iloc[0] - low.iloc[0]

    # ATR-14 Wilder
    atr = tr.rolling(14, min_periods=1).mean()
    alpha = 1.0 / 14
    for i in range(1, len(atr)):
        atr.iloc[i] = atr.iloc[i - 1] * (1 - alpha) + tr.iloc[i] * alpha
    df["atr"] = atr

    # EMA200（趨勢參考，可選）
    df["ema200"] = close.ewm(span=200, adjust=False).mean()

    return df


def _make_record(
    instrument, entry_bar_idx, entry_time, exit_bar_idx, exit_time,
    direction, entry_price, stop_price, tp_price, exit_price,
    exit_reason, r_multiple, pnl_pct, atr_at_entry,
    orb_high, orb_low, orb_width_atr,
):
    win = 1 if r_multiple > 0 else 0
    return {
        "instrument":       instrument,
        "entry_bar":        entry_bar_idx,
        "entry_time":       entry_time,
        "exit_bar":         exit_bar_idx,
        "exit_time":        exit_time,
        "direction":        direction,
        "entry_price":      round(float(entry_price), 4),
        "stop_price":       round(float(stop_price), 4),
        "tp_price":         round(float(tp_price), 4),
        "exit_price":       round(float(exit_price), 4),
        "exit_reason":      exit_reason,
        "r_multiple":       round(float(r_multiple), 4),
        "pnl_pct":          round(float(pnl_pct), 4),
        "win":              win,
        "atr_at_entry":     round(float(atr_at_entry), 4),
        # ORB 特有欄位
        "orb_high":         round(float(orb_high), 4),
        "orb_low":          round(float(orb_low), 4),
        "orb_width_atr":    round(float(orb_width_atr), 4),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Session key（處理跨夜 session）
# ─────────────────────────────────────────────────────────────────────────────

def _get_session_key(ts: pd.Timestamp, cfg: dict) -> str:
    """
    回傳這根 K 棒所屬的 session 識別字串。
    跨夜 session（MXF 21:30 TST）：00:00~05:30 → 前一天的 session。
    非跨夜 session（美股 14:30 UTC）：直接用當天日期。
    """
    bar_time = ts.time()
    sess_start = cfg["session_start"]
    sess_end   = cfg["session_end"]
    crosses    = cfg["crosses_midnight"]

    if crosses:
        # 跨夜：21:30~23:59 → 當天；00:00~05:30 → 前一天
        if bar_time >= sess_start:
            return ts.strftime("%Y-%m-%d") + "-N"
        elif bar_time <= time(5, 30):
            from datetime import timedelta
            prev = (ts - timedelta(days=1)).strftime("%Y-%m-%d")
            return prev + "-N"
        else:
            return ""  # 不在 session 時段
    else:
        # 不跨夜：只有 session_start ~ session_end 之間
        if sess_start <= bar_time <= sess_end:
            return ts.strftime("%Y-%m-%d") + "-D"
        else:
            return ""


def _in_session(ts: pd.Timestamp, cfg: dict) -> bool:
    """判斷此時間點是否在 session 內"""
    return _get_session_key(ts, cfg) != ""


# ─────────────────────────────────────────────────────────────────────────────
# 核心：detect_and_label_orb
# ─────────────────────────────────────────────────────────────────────────────

def detect_and_label_orb(
    df_5m: pd.DataFrame,
    features_df: pd.DataFrame,
    instrument: str,
    cfg: dict,
) -> pd.DataFrame:
    """
    在單一商品的 5min K 線上，偵測 ORB 信號並模擬出場，生成 ML 訓練標籤。

    Parameters
    ----------
    df_5m : pd.DataFrame
        5min OHLCV，需有 open/high/low/close/volume 欄位，以 datetime 為 index 或欄位。
    features_df : pd.DataFrame
        Stage 2 特徵矩陣，以 datetime 為 index。
    instrument : str
        商品名稱。
    cfg : dict
        來自 INSTRUMENT_ORB_CONFIGS 的設定。

    Returns
    -------
    pd.DataFrame
        每筆交易一行，含標籤欄位和特徵欄位。
    """
    # ── 正規化 index
    df = df_5m.copy()
    if "datetime" in df.columns:
        df = df.set_index("datetime")
    df.index = pd.to_datetime(df.index)
    df = df.sort_index()

    if len(df) < 200:
        print(f"  [{instrument}] 資料不足（{len(df)} 根），跳過。")
        return pd.DataFrame()

    # ── 計算指標
    df = _compute_indicators(df)

    # ── 特徵查找表
    feat_cols = [c for c in features_df.columns if c.startswith(("f_", "fd_"))]
    if "datetime" in features_df.columns:
        feat_lookup = features_df.set_index("datetime")[feat_cols]
    else:
        feat_lookup = features_df[feat_cols]
    feat_lookup.index = pd.to_datetime(feat_lookup.index)

    # ── 從 cfg 取參數
    entry_after       = cfg["entry_after"]
    orb_bars_target   = cfg["orb_bars"]
    sl_atr            = cfg["sl_atr"]
    max_sl_pts        = cfg.get("max_sl_pts", 0.0)           # 停損點數上限（0=不限）
    trail_trigger     = cfg["trail_trigger_atr"]
    trail_dist        = cfg["trail_dist_atr"]
    max_bars          = cfg["max_bars"]
    early_cut_bars    = cfg["early_cut_bars"]
    early_cut_loss_atr= cfg["early_cut_loss_atr"]
    min_orb_width_atr = cfg["min_orb_width_atr"]
    max_orb_width_atr = cfg.get("max_orb_width_atr", None)   # 方案 B：ORB 寬度上限
    trend_filter      = cfg.get("trend_filter", None)         # 方案 B："ema200" 或 None
    rsi_max_long      = cfg.get("rsi_max_long", 100)          # 方案 B：LONG RSI 上限
    rsi_min_short     = cfg.get("rsi_min_short", 0)           # 方案 B：SHORT RSI 下限
    max_breakout_vol_ratio = cfg.get("max_breakout_vol_ratio", None)   # B2：突破量過濾

    # ── 陣列
    n           = len(df)
    idx_arr     = df.index
    close_arr   = df["close"].values
    high_arr    = df["high"].values
    low_arr     = df["low"].values
    atr_arr     = df["atr"].values
    ema200_arr  = df["ema200"].values  # 方案 B：EMA200 趨勢方向過濾

    # ── Session 狀態
    current_session_key: str = ""
    orb_high: float = 0.0
    orb_low:  float = float("inf")
    orb_bar_count: int = 0
    orb_ready: bool = False
    session_trade_done: bool = False

    # ── 交易狀態
    in_trade: bool = False
    trade_dir: int = 0
    entry_price: float = 0.0
    stop_price: float = 0.0
    tp_price: float = 0.0
    atr_at_entry: float = 0.0
    entry_bar_idx: int = 0
    bars_held: int = 0
    trail_stop: float = 0.0
    trail_active: bool = False
    orb_high_at_entry: float = 0.0
    orb_low_at_entry: float = 0.0
    orb_width_atr_at_entry: float = 0.0

    records = []
    warmup = 30  # ORB 不需要長指標預熱，ATR-14 就夠

    for i in range(warmup, n):
        ts       = idx_arr[i]
        bar_time = ts.time()

        # ── Session 判斷
        sess_key = _get_session_key(ts, cfg)

        if sess_key == "":
            # 不在 session 內
            if in_trade:
                # Session 結束，強制平倉（前一根收盤）
                exit_price = close_arr[i - 1]
                pnl_dir    = (exit_price - entry_price) * trade_dir
                risk       = abs(entry_price - stop_price) if abs(entry_price - stop_price) > 1e-8 else atr_at_entry * sl_atr
                r_mult     = pnl_dir / risk if risk > 0 else 0.0
                records.append(_make_record(
                    instrument, entry_bar_idx, idx_arr[entry_bar_idx],
                    i - 1, idx_arr[i - 1], trade_dir,
                    entry_price, stop_price, tp_price, exit_price,
                    "session_end", r_mult, pnl_dir / entry_price * 100,
                    atr_at_entry, orb_high_at_entry, orb_low_at_entry, orb_width_atr_at_entry,
                ))
                in_trade = False
                session_trade_done = True
            continue

        # ── 新 Session 開始
        if sess_key != current_session_key:
            current_session_key = sess_key
            orb_high = 0.0
            orb_low  = float("inf")
            orb_bar_count = 0
            orb_ready = False
            session_trade_done = False
            # 注意：不重置 in_trade（不應在新 session 開始時就平倉）

        atr_val  = atr_arr[i] if not np.isnan(atr_arr[i]) and atr_arr[i] > 0 else 1.0
        close_val = close_arr[i]
        high_val  = high_arr[i]
        low_val   = low_arr[i]

        # ── 管理開放中的交易出場
        if in_trade:
            bars_held += 1
            pnl_pts    = (close_val - entry_price) * trade_dir

            exit_price = None
            exit_reason = ""

            # 1. 硬止損
            if trade_dir == 1 and low_val <= stop_price:
                exit_price  = stop_price
                exit_reason = "stop_loss"
            elif trade_dir == -1 and high_val >= stop_price:
                exit_price  = stop_price
                exit_reason = "stop_loss"

            # 2. 追蹤止損啟動與更新
            if exit_price is None:
                pnl_atr = pnl_pts / atr_at_entry if atr_at_entry > 0 else 0.0
                if pnl_atr >= trail_trigger:
                    trail_active = True

                if trail_active:
                    if trade_dir == 1:
                        new_trail = close_val - trail_dist * atr_at_entry
                        trail_stop = max(trail_stop, new_trail)
                        if low_val <= trail_stop:
                            exit_price  = trail_stop
                            exit_reason = "trail_stop"
                    else:
                        new_trail = close_val + trail_dist * atr_at_entry
                        trail_stop = min(trail_stop, new_trail)
                        if high_val >= trail_stop:
                            exit_price  = trail_stop
                            exit_reason = "trail_stop"

            # 3. 早切止損
            if exit_price is None and bars_held >= early_cut_bars:
                if pnl_pts < -early_cut_loss_atr * atr_at_entry:
                    exit_price  = close_val
                    exit_reason = "early_cut"

            # 4. 最大持倉時間
            if exit_price is None and bars_held >= max_bars:
                exit_price  = close_val
                exit_reason = "max_bars"

            # ── 執行出場
            if exit_price is not None:
                pnl_dir = (exit_price - entry_price) * trade_dir
                risk    = abs(entry_price - stop_price) if abs(entry_price - stop_price) > 1e-8 else atr_at_entry * sl_atr
                r_mult  = pnl_dir / risk if risk > 0 else 0.0

                # 加入特徵
                rec = _make_record(
                    instrument, entry_bar_idx, idx_arr[entry_bar_idx],
                    i, ts, trade_dir,
                    entry_price, stop_price, tp_price, exit_price,
                    exit_reason, r_mult, pnl_dir / entry_price * 100,
                    atr_at_entry, orb_high_at_entry, orb_low_at_entry, orb_width_atr_at_entry,
                )
                # 附加特徵（在 entry bar 時間點取）
                entry_ts = idx_arr[entry_bar_idx]
                if entry_ts in feat_lookup.index:
                    for col in feat_cols:
                        rec[col] = feat_lookup.at[entry_ts, col]
                else:
                    # 找最近的特徵列
                    pos = feat_lookup.index.searchsorted(entry_ts)
                    if pos > 0:
                        for col in feat_cols:
                            rec[col] = feat_lookup.iloc[pos - 1][col]

                records.append(rec)
                in_trade = False
                session_trade_done = True
            continue  # 持倉中不偵測新信號

        # ── ORB 區間建立
        if not orb_ready:
            if orb_bar_count < orb_bars_target:
                # 收集開盤區間 K 棒
                orb_high = max(orb_high, high_val)
                orb_low  = min(orb_low, low_val)
                orb_bar_count += 1
            if orb_bar_count >= orb_bars_target:
                # 區間建立完成，檢查是否有效
                orb_width = orb_high - orb_low
                orb_w_ratio = orb_width / atr_val if atr_val > 0 else 0.0
                too_narrow = orb_w_ratio < min_orb_width_atr
                too_wide   = (max_orb_width_atr is not None) and (orb_w_ratio > max_orb_width_atr)
                if too_narrow or too_wide:
                    # 區間過窄或過寬（混亂市場），跳過本 session
                    orb_ready = False
                    session_trade_done = True
                else:
                    orb_ready = True
            continue  # 區間建立期間不進場

        # ── 突破偵測（ORB 建立完成 + 未進場 + 在 entry_after 之後）
        if session_trade_done:
            continue

        # 跨夜 session 處理：00:00~05:30 的 bar_time 數值上 < entry_after（22:15）
        # 但實際上已在 ORB 之後，需特殊判斷
        crosses = cfg.get("crosses_midnight", False)
        if crosses and bar_time <= time(5, 30):
            past_entry = True   # 跨夜後已過 entry_after
        else:
            past_entry = bar_time >= entry_after

        if not past_entry:
            continue

        orb_width   = orb_high - orb_low
        orb_width_atr_ratio = orb_width / atr_val if atr_val > 0 else 0.0

        # ── 方案 B：突破前置過濾
        # Filter A: EMA200 趨勢方向
        ema200_val = ema200_arr[i]
        above_ema200 = (not np.isnan(ema200_val)) and (close_val > ema200_val)
        below_ema200 = (not np.isnan(ema200_val)) and (close_val < ema200_val)

        # Filter C: RSI（從 feat_lookup 取當根 bar 的 RSI）
        rsi_val = 50.0  # 預設中性值
        if trend_filter is not None and ts in feat_lookup.index and "f_rsi" in feat_lookup.columns:
            v = feat_lookup.at[ts, "f_rsi"]
            if pd.notna(v):
                rsi_val = float(v)
        elif trend_filter is not None:
            pos = feat_lookup.index.searchsorted(ts)
            if 0 < pos <= len(feat_lookup):
                v = feat_lookup.iloc[pos - 1].get("f_rsi", 50.0)
                if pd.notna(v):
                    rsi_val = float(v)

        # LONG 突破
        if close_val > orb_high:
            # 方案 B 過濾：LONG 需在 EMA200 上方且 RSI 未過熱
            if trend_filter == "ema200" and (not above_ema200 or rsi_val > rsi_max_long):
                session_trade_done = True
                continue

            # B2: 突破量過濾（高量 = 假突破，量縮才是真突破）
            if max_breakout_vol_ratio is not None:
                _vol = 1.0
                if ts in feat_lookup.index and "f_vol_ratio" in feat_lookup.columns:
                    _v = feat_lookup.at[ts, "f_vol_ratio"]
                    if pd.notna(_v):
                        _vol = float(_v)
                else:
                    _pos = feat_lookup.index.searchsorted(ts)
                    if 0 < _pos <= len(feat_lookup) and "f_vol_ratio" in feat_lookup.columns:
                        _v = feat_lookup.iloc[_pos - 1].get("f_vol_ratio", 1.0)
                        if pd.notna(_v):
                            _vol = float(_v)
                if _vol > max_breakout_vol_ratio:
                    session_trade_done = True
                    continue

            direction   = 1
            ep          = close_val
            sl_dist     = sl_atr * atr_val
            if max_sl_pts > 0:
                sl_dist = min(sl_dist, max_sl_pts)
            sp          = ep - sl_dist
            tpp         = ep + 10.0 * atr_val  # 固定 TP（通常由追蹤止損先觸及）

            in_trade              = True
            trade_dir             = direction
            entry_price           = ep
            stop_price            = sp
            tp_price              = tpp
            atr_at_entry          = atr_val
            entry_bar_idx         = i
            bars_held             = 0
            trail_stop            = sp
            trail_active          = False
            orb_high_at_entry     = orb_high
            orb_low_at_entry      = orb_low
            orb_width_atr_at_entry = orb_width_atr_ratio

        # SHORT 突破
        elif close_val < orb_low:
            # 方案 B 過濾：SHORT 需在 EMA200 下方且 RSI 未過冷
            if trend_filter == "ema200" and (not below_ema200 or rsi_val < rsi_min_short):
                session_trade_done = True
                continue

            # B2: 突破量過濾（高量 = 假突破，量縮才是真突破）
            if max_breakout_vol_ratio is not None:
                _vol = 1.0
                if ts in feat_lookup.index and "f_vol_ratio" in feat_lookup.columns:
                    _v = feat_lookup.at[ts, "f_vol_ratio"]
                    if pd.notna(_v):
                        _vol = float(_v)
                else:
                    _pos = feat_lookup.index.searchsorted(ts)
                    if 0 < _pos <= len(feat_lookup) and "f_vol_ratio" in feat_lookup.columns:
                        _v = feat_lookup.iloc[_pos - 1].get("f_vol_ratio", 1.0)
                        if pd.notna(_v):
                            _vol = float(_v)
                if _vol > max_breakout_vol_ratio:
                    session_trade_done = True
                    continue

            direction   = -1
            ep          = close_val
            sl_dist     = sl_atr * atr_val
            if max_sl_pts > 0:
                sl_dist = min(sl_dist, max_sl_pts)
            sp          = ep + sl_dist
            tpp         = ep - 10.0 * atr_val

            in_trade              = True
            trade_dir             = direction
            entry_price           = ep
            stop_price            = sp
            tp_price              = tpp
            atr_at_entry          = atr_val
            entry_bar_idx         = i
            bars_held             = 0
            trail_stop            = sp
            trail_active          = False
            orb_high_at_entry     = orb_high
            orb_low_at_entry      = orb_low
            orb_width_atr_at_entry = orb_width_atr_ratio

    if not records:
        return pd.DataFrame()

    result_df = pd.DataFrame(records)
    result_df["win"] = (result_df["r_multiple"] > 0).astype(int)
    return result_df


# ─────────────────────────────────────────────────────────────────────────────
# 主流程（對應 labels.py 的主流程，但針對 ORB 商品）
# ─────────────────────────────────────────────────────────────────────────────

def run_orb_labeling(
    instruments: list = None,
    data_dir: Path = None,
    output_suffix: str = "_orb",
) -> pd.DataFrame:
    """
    對所有 ORB 商品執行信號偵測與標籤生成，輸出 ml_orb_dataset.parquet。

    Parameters
    ----------
    instruments : list, optional
        要處理的商品清單，None = 全部 INSTRUMENT_ORB_CONFIGS
    data_dir : Path, optional
        資料目錄，None = ML_DATA_DIR
    output_suffix : str
        信號檔名後綴（{instrument}{suffix}_signals.parquet）

    Returns
    -------
    pd.DataFrame
        合併後的 ML ORB 資料集
    """
    if data_dir is None:
        data_dir = ML_DATA_DIR

    if instruments is None:
        instruments = list(INSTRUMENT_ORB_CONFIGS.keys())

    print("=" * 60)
    print("Stage 3 (ORB): ORB Signal Detection + Labeling")
    print("=" * 60)

    all_dfs = []
    skipped = []
    errors  = []

    for inst in instruments:
        if inst not in INSTRUMENT_ORB_CONFIGS:
            print(f"[{inst}] 無 ORB 設定，跳過。")
            skipped.append(inst)
            continue

        cfg = INSTRUMENT_ORB_CONFIGS[inst]
        file_prefix = cfg.get("file_alias", inst)  # 允許 instrument 名稱與檔名前綴不同

        # 載入 5min K 線
        price_path = data_dir / f"{file_prefix}_5m.parquet"
        if not price_path.exists():
            print(f"[{inst}] 找不到價格檔：{price_path}，跳過。")
            skipped.append(inst)
            continue

        # 載入特徵
        feat_path = data_dir / f"{file_prefix}_features.parquet"
        if not feat_path.exists():
            print(f"[{inst}] 找不到特徵檔：{feat_path}，請先執行 features.py。")
            skipped.append(inst)
            continue

        print(f"\n[Stage 3 ORB] Processing {inst} ...")
        try:
            df_price = pd.read_parquet(price_path)
            df_feat  = pd.read_parquet(feat_path)

            result = detect_and_label_orb(df_price, df_feat, inst, cfg)

            if result.empty:
                print(f"  [{inst}] 無有效交易，跳過。")
                skipped.append(inst)
                continue

            n      = len(result)
            wr     = result["win"].mean()
            avg_r  = result["r_multiple"].mean()
            print(f"  [{inst}] {n} trades | win_rate={wr:.1%} | avg_R={avg_r:.2f}")

            # 存個別商品信號檔
            sig_path = data_dir / f"{inst}{output_suffix}_signals.parquet"
            result.to_parquet(sig_path, index=False)
            print(f"  [{inst}] Saved: {sig_path}")

            result["instrument"] = inst
            all_dfs.append(result)

        except Exception as e:
            import traceback
            print(f"  [{inst}] ERROR: {e}")
            traceback.print_exc()
            errors.append(f"{inst}: {e}")

    if not all_dfs:
        print("\n[ERROR] 無任何商品產生交易，請確認資料與設定。")
        return pd.DataFrame()

    # ── 合併
    pooled = pd.concat(all_dfs, ignore_index=True)
    pooled["entry_time"] = pd.to_datetime(pooled["entry_time"])
    pooled = pooled.sort_values("entry_time").reset_index(drop=True)

    # ── 統計
    total_n = len(pooled)
    overall_wr = pooled["win"].mean()
    overall_avg_r = pooled["r_multiple"].mean()

    print(f"\n[Stage 3 ORB] Pooled dataset: {total_n} trades across {len(all_dfs)} instruments")
    print(f"  Overall win_rate={overall_wr:.1%}  avg_R={overall_avg_r:.2f}")

    if skipped:
        print(f"  Instruments skipped: {skipped}")
    if errors:
        print(f"  Errors: {errors}")

    # 出場原因分析
    if "exit_reason" in pooled.columns:
        print("\n  Exit reason breakdown:")
        breakdown = pooled.groupby("exit_reason").agg(
            count=("win", "count"),
            win_rate=("win", "mean"),
            avg_r=("r_multiple", "mean"),
        ).round(3)
        print(breakdown.to_string())

    # ── 儲存
    out_path = data_dir / "ml_orb_dataset.parquet"
    pooled.to_parquet(out_path, index=False)
    print(f"\n  Saved: {out_path}  ({len(pooled)} labeled trades)")

    return pooled


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Stage 3 ORB: Generate ORB training labels")
    parser.add_argument(
        "--instrument", type=str, default=None,
        help="指定單一商品（不指定 = 全部）",
    )
    args = parser.parse_args()

    instruments = [args.instrument] if args.instrument else None
    run_orb_labeling(instruments=instruments)
