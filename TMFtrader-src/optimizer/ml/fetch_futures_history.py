"""
fetch_futures_history.py
========================
補抓 TMF（微型台指，MXF）或 TXF（台指期）歷史 1-min K 線（2020-01 起）。

策略：
  1. 逐月合約（每月結算日 = 第3個週三）逐一下載
  2. 每個合約取「前月結算日+1 ~ 當月結算日」的資料（近月主力期間）
  3. 優先嘗試 MXF (TMF)，失敗則改用 TXF
  4. 輸出：
     - {product}_day_1m.parquet    日盤 08:45~13:30 1-min
     - {product}_night_1m.parquet  夜盤 21:30~04:00（跨夜）1-min
     - {product}_day_5m.parquet    日盤 5-min（供 ML pipeline 直接使用）
     - {product}_night_5m.parquet  夜盤 5-min（供 ML pipeline 直接使用）

用法：
  python optimizer/ml/fetch_futures_history.py
  python optimizer/ml/fetch_futures_history.py --product TXF --start 2020-01
"""

import argparse
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

ROOT     = Path(__file__).parent.parent.parent
DATA_DIR = ROOT / "data" / "historical" / "ml"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Taiwan futures month letter codes
MONTH_CODE = {
    1: "F", 2: "G", 3: "H", 4: "J",  5: "K", 6: "M",
    7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z",
}

# Session windows (TST = UTC+8)
DAY_START   = "08:45"
DAY_END     = "13:30"
NIGHT_START = "21:30"   # 夜盤 21:30 ~ 次日 04:00（跨夜）
NIGHT_END   = "04:00"


# ─────────────────────────────────────────────────────────────────────────────
# Contract code helpers
# ─────────────────────────────────────────────────────────────────────────────

def contract_code(product: str, year: int, month: int) -> str:
    """例：TXF, 2024, 5  →  TXFK4"""
    return f"{product}{MONTH_CODE[month]}{year % 10}"


def third_wednesday(year: int, month: int) -> date:
    """取該月第3個週三（台灣期貨結算日）。"""
    d = date(year, month, 1)
    # 找到第一個週三（weekday=2）
    days_to_wed = (2 - d.weekday()) % 7
    first_wed = d + timedelta(days=days_to_wed)
    return first_wed + timedelta(weeks=2)  # 第3個


def generate_months(start_year: int, start_month: int) -> list[tuple[int, int]]:
    """從 start 到今日，回傳所有 (year, month) 列表。"""
    today = date.today()
    months = []
    y, m = start_year, start_month
    while (y, m) <= (today.year, today.month):
        months.append((y, m))
        m += 1
        if m > 12:
            m = 1; y += 1
    return months


# ─────────────────────────────────────────────────────────────────────────────
# Shioaji login
# ─────────────────────────────────────────────────────────────────────────────

def _login():
    import shioaji as sj
    api_key    = os.environ.get("SHIOAJI_API_KEY")
    secret_key = os.environ.get("SHIOAJI_SECRET_KEY")
    if not api_key or not secret_key:
        raise EnvironmentError("Missing SHIOAJI_API_KEY / SHIOAJI_SECRET_KEY")
    api = sj.Shioaji()
    print("  Logging in to Shioaji ...")
    api.login(api_key=api_key, secret_key=secret_key, receive_window=300000)
    return api


def _probe_product(api, candidates: list[str]) -> str | None:
    """
    試探 shioaji 中實際存在的商品代碼（MXF / TMF / TXF）。
    回傳第一個成功取到合約列表的代碼，或 None。
    """
    for prod in candidates:
        try:
            contracts = getattr(api.Contracts.Futures, prod, None)
            if contracts is not None and len(list(contracts)) > 0:
                print(f"  Product found in shioaji: {prod}")
                return prod
        except Exception:
            pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Download one contract's 1-min bars
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_contract_bars(api, product: str, year: int, month: int,
                         start_date: date, end_date: date) -> list[dict]:
    """
    下載單一合約（product+year+month）在 start_date~end_date 的 1-min K 線。
    回傳 list of dict，或空 list。
    """
    code = contract_code(product, year, month)

    # 取得合約物件
    try:
        futures_group = getattr(api.Contracts.Futures, product)
        contract = futures_group[code]
    except (AttributeError, KeyError, TypeError):
        print(f"    [{code}] contract not found, skipping.")
        return []

    bars = []
    # 逐日下載（shioaji kbars 每次一天）
    cur = start_date
    while cur <= end_date:
        date_str = cur.strftime("%Y-%m-%d")
        try:
            kbars = api.kbars(contract=contract,
                              start=date_str, end=date_str)
            if kbars and hasattr(kbars, "Close") and len(kbars.Close) > 0:
                for i in range(len(kbars.Close)):
                    raw_ts = kbars.ts[i]
                    if isinstance(raw_ts, (int, float)):
                        epoch_sec = raw_ts / 1e9 if raw_ts > 1e12 else raw_ts
                        ts = datetime.fromtimestamp(epoch_sec)
                    elif hasattr(raw_ts, "to_pydatetime"):
                        ts = raw_ts.to_pydatetime()
                        if hasattr(ts, "tzinfo") and ts.tzinfo is not None:
                            ts = ts.astimezone().replace(tzinfo=None)
                    else:
                        ts = raw_ts
                    bars.append({
                        "datetime": ts,
                        "open":   float(kbars.Open[i]),
                        "high":   float(kbars.High[i]),
                        "low":    float(kbars.Low[i]),
                        "close":  float(kbars.Close[i]),
                        "volume": int(kbars.Volume[i]),
                    })
        except Exception as e:
            print(f"    [{code}] {date_str} error: {e}")

        cur += timedelta(days=1)
        time.sleep(0.05)   # 避免 rate limit

    if bars:
        print(f"    [{code}] {start_date} ~ {end_date}: {len(bars)} 1m bars")
    else:
        print(f"    [{code}] {start_date} ~ {end_date}: no data")
    return bars


# ─────────────────────────────────────────────────────────────────────────────
# Session filter & resample helpers
# ─────────────────────────────────────────────────────────────────────────────

def _filter_day_session(df_1m: pd.DataFrame) -> pd.DataFrame:
    """保留日盤 08:45~13:30 的 1-min bars。"""
    t = df_1m.index.time
    mask = (t >= pd.to_datetime(DAY_START).time()) & \
           (t <= pd.to_datetime(DAY_END).time())
    return df_1m[mask]


def _filter_night_session(df_1m: pd.DataFrame) -> pd.DataFrame:
    """保留夜盤 21:30~23:59 + 00:00~04:00（跨夜）的 1-min bars。"""
    t = df_1m.index.time
    night_start = pd.to_datetime(NIGHT_START).time()
    night_end   = pd.to_datetime(NIGHT_END).time()
    # 21:30~23:59
    mask_eve  = t >= night_start
    # 00:00~04:00
    mask_morn = t <= night_end
    return df_1m[mask_eve | mask_morn]


def _resample_5m(df_1m: pd.DataFrame) -> pd.DataFrame:
    """1-min → 5-min（index 保留為 datetime）。"""
    df = df_1m.copy()
    df_5m = df.resample("5min", closed="left", label="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).dropna(subset=["open"])
    return df_5m.reset_index()


# ─────────────────────────────────────────────────────────────────────────────
# Main download loop
# ─────────────────────────────────────────────────────────────────────────────

def fetch_history(product: str, start_year: int, start_month: int) -> dict:
    """
    下載 product 從 start_year/start_month 起的完整歷史 1-min K 線。
    回傳 {"day_1m": df, "night_1m": df}。
    """
    months = generate_months(start_year, start_month)
    print(f"\n  Fetching {product} from {start_year}-{start_month:02d} "
          f"({len(months)} months) ...")

    api = _login()
    all_bars: list[dict] = []

    try:
        for idx, (year, month) in enumerate(months):
            # 本月合約的活躍期：上月結算日+1 ~ 本月結算日
            settle_this  = third_wednesday(year, month)
            if month == 1:
                settle_prev = third_wednesday(year - 1, 12)
            else:
                settle_prev = third_wednesday(year, month - 1)

            period_start = settle_prev + timedelta(days=1)
            period_end   = settle_this

            # 不超過今天
            today = date.today()
            if period_start > today:
                break
            period_end = min(period_end, today)

            print(f"\n  [{year}-{month:02d}] {product} "
                  f"contract={contract_code(product, year, month)}  "
                  f"{period_start} ~ {period_end}")

            bars = _fetch_contract_bars(
                api, product, year, month, period_start, period_end
            )
            all_bars.extend(bars)

            # 每10個合約短暫休息（避免 session timeout）
            if (idx + 1) % 10 == 0:
                time.sleep(2)

    finally:
        try:
            api.logout()
        except Exception:
            pass

    if not all_bars:
        raise RuntimeError(f"{product}: no bars downloaded")

    # 整理成 DataFrame
    df = (pd.DataFrame(all_bars)
          .drop_duplicates(subset=["datetime"])
          .sort_values("datetime")
          .set_index("datetime"))
    df.index = pd.to_datetime(df.index)
    print(f"\n  Total raw 1-min bars: {len(df):,} "
          f"({df.index[0]} ~ {df.index[-1]})")

    return {
        "day_1m":   _filter_day_session(df),
        "night_1m": _filter_night_session(df),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Save outputs
# ─────────────────────────────────────────────────────────────────────────────

def save_outputs(data: dict, product: str) -> None:
    """儲存 1m 和 5m parquet，並印出統計。"""
    day_1m   = data["day_1m"]
    night_1m = data["night_1m"]
    day_5m   = _resample_5m(day_1m)
    night_5m = _resample_5m(night_1m)

    paths = {
        "day_1m":   DATA_DIR / f"{product}_day_1m.parquet",
        "night_1m": DATA_DIR / f"{product}_night_1m.parquet",
        "day_5m":   DATA_DIR / f"{product}_day_5m.parquet",
        "night_5m": DATA_DIR / f"{product}_night_5m.parquet",
    }

    day_1m.reset_index().to_parquet(paths["day_1m"],   index=False)
    night_1m.reset_index().to_parquet(paths["night_1m"], index=False)
    day_5m.to_parquet(paths["day_5m"],   index=False)
    night_5m.to_parquet(paths["night_5m"], index=False)

    print(f"\n  ✅ 日盤  1m: {len(day_1m):,} bars → {paths['day_1m'].name}")
    print(f"  ✅ 夜盤  1m: {len(night_1m):,} bars → {paths['night_1m'].name}")
    print(f"  ✅ 日盤  5m: {len(day_5m):,} bars → {paths['day_5m'].name}")
    print(f"  ✅ 夜盤  5m: {len(night_5m):,} bars → {paths['night_5m'].name}")

    print(f"\n  日盤期間: {day_5m['datetime'].iloc[0]} ~ {day_5m['datetime'].iloc[-1]}")
    print(f"  夜盤期間: {night_5m['datetime'].iloc[0]} ~ {night_5m['datetime'].iloc[-1]}")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Fetch TMF/TXF futures history")
    parser.add_argument("--product", default="auto",
                        help="MXF | TMF | TXF | auto (default: auto-detect)")
    parser.add_argument("--start", default="2020-01",
                        help="Start year-month, e.g. 2020-01 (default)")
    args = parser.parse_args()

    start_year, start_month = map(int, args.start.split("-"))

    print("=" * 60)
    print("  Taiwan Futures History Downloader")
    print(f"  Start: {start_year}-{start_month:02d}")
    print("=" * 60)

    if args.product == "auto":
        # 先 probe 有哪些商品可用
        import shioaji as sj
        import os
        api = sj.Shioaji()
        api.login(api_key=os.environ["SHIOAJI_API_KEY"],
                  secret_key=os.environ["SHIOAJI_SECRET_KEY"],
                  receive_window=300000)
        product = _probe_product(api, ["MXF", "TMF", "TXF"])
        api.logout()
        if product is None:
            print("  ERROR: Cannot find MXF/TMF/TXF in shioaji Contracts.")
            sys.exit(1)
    else:
        product = args.product.upper()

    print(f"\n  Using product: {product}")

    try:
        data = fetch_history(product, start_year, start_month)
        save_outputs(data, product)
        print(f"\n  Done. Data saved to: {DATA_DIR}")
    except Exception as e:
        import traceback
        print(f"\n  FATAL ERROR: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
