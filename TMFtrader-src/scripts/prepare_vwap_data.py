"""
prepare_vwap_data.py
P1: 日盤 5m 資料準備 for VWAP Fade 策略

原始資料：data/history/{PREFIX}_1min_{YYYYMM}.parquet
  - 欄位：ts (datetime64), Open, High, Low, Close, Volume, Amount
  - MXF → MXFR1  (2020-03..2026-05, 訓練集)
  - TXF → TXFR1  (2020-03..2026-05, 訓練集)
  - TMF → TMFR1  (2024-07..2026-05, OOS 集，不建訓練集)

輸出：data/vwap_fade/
  MXF_day_5m.parquet       欄位：datetime, open, high, low, close, volume
  TXF_day_5m.parquet
  TMF_oos_day_5m.parquet

用法：python scripts/prepare_vwap_data.py
"""
import sys
import warnings
warnings.filterwarnings("ignore")
sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, ".")

import pandas as pd
import numpy as np
from pathlib import Path
from glob import glob

# ── 常數 ─────────────────────────────────────────────────────────────
SESSION_START = "08:45"
SESSION_END   = "13:45"

HISTORY_DIR   = Path("data/history")
OUT_DIR       = Path("data/vwap_fade")

SYMBOL_MAP = {
    "MXF": ("MXFR1", "MXF_day_5m.parquet",       None),
    "TXF": ("TXFR1", "TXF_day_5m.parquet",       None),
    "TMF": ("TMFR1", "TMF_oos_day_5m.parquet",   None),
}

# 欄位對照：原始 shard 欄位 → 標準化欄位
COL_RENAME = {
    "ts":     "datetime",
    "Open":   "open",
    "High":   "high",
    "Low":    "low",
    "Close":  "close",
    "Volume": "volume",
}
KEEP_COLS = ["datetime", "open", "high", "low", "close", "volume"]


# ── 純函式（單元測試合約，請勿修改邏輯） ─────────────────────────────
def resample_day_session(df_1m: pd.DataFrame, freq: str = "5min") -> pd.DataFrame:
    """
    過濾日盤時段 [08:45, 13:45) 並聚合至 freq bars。

    Input columns: datetime, open, high, low, close, volume
    Output: 相同 columns，datetime = bar 左端時間（含 08:45，不含 13:45）
    """
    df = df_1m.copy()
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.set_index("datetime").sort_index()
    # inclusive="left" => 08:45 <= t < 13:45
    df = df.between_time(SESSION_START, SESSION_END, inclusive="left")
    agg = {
        "open":   "first",
        "high":   "max",
        "low":    "min",
        "close":  "last",
        "volume": "sum",
    }
    parts = []
    for _, day in df.groupby(df.index.date):
        r = day.resample(freq, label="left", closed="left").agg(agg).dropna(subset=["open"])
        parts.append(r)
    return pd.concat(parts).reset_index()


# ── 載入 & 正規化 ─────────────────────────────────────────────────────
def load_monthly_shards(prefix: str) -> pd.DataFrame:
    """Glob 所有月份 shard，concat，欄位正規化。"""
    pattern = str(HISTORY_DIR / f"{prefix}_1min_*.parquet")
    files = sorted(glob(pattern))
    if not files:
        raise FileNotFoundError(f"No shards found: {pattern}")

    dfs = []
    for f in files:
        df = pd.read_parquet(f)
        # 統一欄位名稱
        df = df.rename(columns=COL_RENAME)
        # 只保留需要的欄位
        df = df[[c for c in KEEP_COLS if c in df.columns]]
        dfs.append(df)
    return pd.concat(dfs, ignore_index=True)


# ── 品質報告 ─────────────────────────────────────────────────────────
def quality_report(df_5m: pd.DataFrame, symbol: str) -> dict:
    """
    計算完整性 & 零量比率。

    完整性：以 [08:45, 13:45) 共 60 分鐘 / 5 = 12 bars/day 為分母，
    計算實際 bar 數 / 理論 bar 數。
    """
    df = df_5m.copy()
    df["date"] = df["datetime"].dt.date

    n_bars = len(df)
    n_days = df["date"].nunique()
    # 日盤 08:45..13:45 = 5 小時 = 300 分鐘 / 5 = 60 bars/day
    expected_bars_per_day = 60
    expected_total = n_days * expected_bars_per_day

    completeness = n_bars / expected_total if expected_total > 0 else 0.0
    zero_vol = (df["volume"] == 0).sum()
    zero_vol_ratio = zero_vol / n_bars if n_bars > 0 else 0.0

    result = {
        "symbol":        symbol,
        "n_bars":        n_bars,
        "n_days":        n_days,
        "expected_bars": expected_total,
        "completeness":  completeness,
        "zero_vol":      int(zero_vol),
        "zero_vol_ratio": zero_vol_ratio,
    }

    flag = " *** FLAG: zero_vol > 5% ***" if zero_vol_ratio > 0.05 else ""
    print(
        f"  [{symbol}] days={n_days}, bars={n_bars}/{expected_total}, "
        f"completeness={completeness:.2%}, "
        f"zero_vol={zero_vol}/{n_bars} ({zero_vol_ratio:.2%}){flag}"
    )
    return result


# ── 主流程 ────────────────────────────────────────────────────────────
def build():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    reports = []
    for symbol, (prefix, out_name, _) in SYMBOL_MAP.items():
        print(f"\n=== {symbol} ({prefix}) ===")

        # 載入
        df_raw = load_monthly_shards(prefix)
        print(f"  raw 1m bars: {len(df_raw)}, date range: "
              f"{df_raw['datetime'].min()} ~ {df_raw['datetime'].max()}")

        # Resample
        df_5m = resample_day_session(df_raw, freq="5min")

        # 品質 Gate
        rpt = quality_report(df_5m, symbol)
        reports.append(rpt)

        # 完整性 Gate
        if rpt["completeness"] < 0.99:
            print(f"  WARN: completeness {rpt['completeness']:.2%} < 99%")

        # 寫出
        out_path = OUT_DIR / out_name
        df_5m[KEEP_COLS].to_parquet(out_path, index=False)
        print(f"  => {out_path}  ({out_path.stat().st_size // 1024} KB)")

    return reports


if __name__ == "__main__":
    print("=== prepare_vwap_data: 日盤 5m 資料準備 ===\n")
    reports = build()
    print("\n=== Summary ===")
    for r in reports:
        flag = " [ZERO_VOL_FLAG]" if r["zero_vol_ratio"] > 0.05 else ""
        print(
            f"  {r['symbol']:4s}: completeness={r['completeness']:.3%}, "
            f"zero_vol_ratio={r['zero_vol_ratio']:.3%}{flag}"
        )
