"""Stage 1: Multi-instrument data collection for ML pipeline.

Collects 5-minute OHLCV data for Nasdaq-correlated instruments and saves each
as a parquet file in data/historical/ml/.

Instrument list:
  TW futures : TMF, TX
  Leveraged ETF : SPXL (S&P 3x), TECL (Tech 3x), TQQQ (NQ 3x)
  Index ETF  : QQQM (NQ100), IVV (SPX)
  Sector ETF : XLK (IT), SOXX (Semiconductor/SOX), IBB (Biotech/NBI), FNGS (FANG+)
  CFD index  : NAS100 (NDX — verify symbol name with your broker)
  Crypto     : BTCUSDT only (altcoins removed — low Nasdaq correlation)

Usage:
    python optimizer/ml/collect_data.py
    python optimizer/ml/collect_data.py --skip-existing false
"""

import sys
import os
import time
import argparse
from pathlib import Path
from datetime import datetime, timedelta, date

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')

import pandas as pd
import numpy as np
import requests
from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).parent.parent.parent

# ---------------------------------------------------------------------------
# Instrument registry
# ---------------------------------------------------------------------------
INSTRUMENTS = {
    "TMF":      {"type": "shioaji_existing"},
    "TX":       {"type": "shioaji",  "session": ("08:45", "13:30")},

    # ── US Leveraged ETFs (same product structure as TMF) ─────────────────────
    # All timestamps: naive UTC; session = US regular market hours (14:30–21:00 UTC)
    # SPXL = S&P 500 3x, TECL = Technology sector 3x, TQQQ = Nasdaq 100 3x
    # These carry the strongest training signal for TMF-class breakouts.
    "SPXL":     {"type": "mt5", "mt5_symbol": "SPXL", "session": ("14:30", "21:00")},
    "TECL":     {"type": "mt5", "mt5_symbol": "TECL", "session": ("14:30", "21:00")},
    "TQQQ":     {"type": "mt5", "mt5_symbol": "TQQQ", "session": ("14:30", "21:00")},  # Nasdaq 3x

    # ── US Non-leveraged ETFs (Nasdaq / broad market) ─────────────────────────
    "QQQM":     {"type": "mt5", "mt5_symbol": "QQQM", "session": ("14:30", "21:00")},  # Nasdaq 100
    "IVV":      {"type": "mt5", "mt5_symbol": "IVV",  "session": ("14:30", "21:00")},  # S&P 500

    # ── US Sector ETFs (Nasdaq-correlated) ────────────────────────────────────
    "XLK":      {"type": "mt5", "mt5_symbol": "XLK",  "session": ("14:30", "21:00")},  # S&P 500 IT sector
    "SOXX":     {"type": "mt5", "mt5_symbol": "SOXX", "session": ("14:30", "21:00")},  # Semiconductor (SOX proxy)
    "IBB":      {"type": "mt5", "mt5_symbol": "IBB",  "session": ("14:30", "21:00")},  # Nasdaq Biotech (NBI proxy)
    "FNGS":     {"type": "mt5", "mt5_symbol": "FNGS", "session": ("14:30", "21:00")},  # NYSE FANG+ ETF

    # ── Nasdaq 100 Index CFD ──────────────────────────────────────────────────
    # NOTE: Symbol name is broker-dependent. Common variants:
    #   IC Markets / Pepperstone: "NAS100"
    #   FXCM: "NAS100"
    #   Other brokers: "USTEC", "NDX100", "NDX"
    # Verify in your MT5 Market Watch before enabling.
    "NAS100":   {"type": "mt5", "mt5_symbol": "NAS100", "session": ("14:30", "21:00")},  # Nasdaq 100 CFD (NDX)

    # ── NQ Futures (commented out — rolling contract complexity) ──────────────
    # "NQ":    {"type": "mt5", "mt5_symbol": "NQ", "session": ("14:30", "21:00")},

    # ── Crypto: BTC only (other altcoins dropped — low Nasdaq correlation) ────
    "BTCUSDT":  {"type": "binance",  "session": ("08:00", "16:00")},
}

# ---------------------------------------------------------------------------
# Helper: resample 1-min OHLCV to 5-min and filter to session window
# ---------------------------------------------------------------------------

def resample_to_5m(df_1m: pd.DataFrame,
                   session_start: str,
                   session_end: str) -> pd.DataFrame:
    """Resample 1-min bars to 5-min, then keep only bars within session.

    Args:
        df_1m: DataFrame with columns [datetime, open, high, low, close, volume].
               datetime must be a proper datetime (no tz).
        session_start: 'HH:MM'
        session_end:   'HH:MM'  (inclusive — last bar whose label is <= end)

    Returns:
        DataFrame with 5-min bars and same columns.
    """
    df = df_1m.copy()
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.set_index("datetime").sort_index()

    # Resample: open=first, high=max, low=min, close=last, volume=sum
    df_5m = df.resample("5min", closed="left", label="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).dropna(subset=["open", "close"])

    # Filter to session window
    t_start = pd.Timestamp(f"1970-01-01 {session_start}").time()
    t_end   = pd.Timestamp(f"1970-01-01 {session_end}").time()
    mask = (df_5m.index.time >= t_start) & (df_5m.index.time <= t_end)
    df_5m = df_5m[mask].reset_index().rename(columns={"index": "datetime"})
    df_5m["datetime"] = pd.to_datetime(df_5m["datetime"])  # ensure Timestamp, no tz
    return df_5m[["datetime", "open", "high", "low", "close", "volume"]]


# ---------------------------------------------------------------------------
# Loader 1: TMF — load existing CSV and resample
# ---------------------------------------------------------------------------

def load_tmf_existing() -> pd.DataFrame:
    """Load TMF from the pre-existing full 1-min CSV, resample to 5-min."""
    csv_path = ROOT / "data" / "historical" / "tmf_20260411_full_1m.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"TMF CSV not found: {csv_path}")

    print(f"  Loading TMF from {csv_path} ...")
    df = pd.read_csv(csv_path, parse_dates=["datetime"])
    print(f"  Raw 1-min bars: {len(df):,}  ({df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]})")

    df_5m = resample_to_5m(df, "08:45", "13:30")
    print(f"  5-min bars after session filter: {len(df_5m):,}")
    return df_5m


# ---------------------------------------------------------------------------
# Loader 2: TX — download from Shioaji
# ---------------------------------------------------------------------------

def _shioaji_login():
    """Login to Shioaji and return api instance."""
    import shioaji as sj

    api_key    = os.environ.get("SHIOAJI_API_KEY")
    secret_key = os.environ.get("SHIOAJI_SECRET_KEY")
    person_id  = os.environ.get("SHIOAJI_PERSON_ID")
    ca_password = os.environ.get("SHIOAJI_CA_PASSWORD")

    if not api_key or not secret_key:
        raise EnvironmentError("Missing SHIOAJI_API_KEY / SHIOAJI_SECRET_KEY")

    api = sj.Shioaji()
    print("  Logging in to Shioaji ...")
    api.login(api_key=api_key, secret_key=secret_key, receive_window=300000)

    if person_id and ca_password:
        try:
            api.activate_ca(
                ca_path=os.environ.get("SHIOAJI_CA_PATH", ""),
                ca_passwd=ca_password,
                person_id=person_id,
            )
        except Exception as e:
            print(f"  Warning: CA activation failed (non-critical): {e}")

    return api


def download_tx_shioaji(days: int = 800) -> pd.DataFrame:
    """Download TXF (near-month) 1-min kbars from Shioaji and resample to 5-min."""
    api = _shioaji_login()

    try:
        # Get near-month TXF contract
        txf_contracts = [c for c in api.Contracts.Futures.TXF
                         if hasattr(c, "delivery_month") and c.delivery_month]
        if not txf_contracts:
            raise RuntimeError("No TXF contracts with delivery_month found")
        contract = min(txf_contracts, key=lambda c: c.delivery_month)
        print(f"  Contract: {contract.code} ({getattr(contract, 'name', '')})")

        all_bars = []
        batch_size = 5
        remaining = days
        current_end = datetime.now()

        while remaining > 0:
            fetch_days = min(batch_size, remaining)
            start = current_end - timedelta(days=fetch_days)
            start_str = start.strftime("%Y-%m-%d")
            end_str   = current_end.strftime("%Y-%m-%d")
            print(f"    Fetching {start_str} ~ {end_str} ...")

            try:
                kbars = api.kbars(contract=contract, start=start_str, end=end_str)
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
                        all_bars.append({
                            "datetime": ts,
                            "open":   float(kbars.Open[i]),
                            "high":   float(kbars.High[i]),
                            "low":    float(kbars.Low[i]),
                            "close":  float(kbars.Close[i]),
                            "volume": int(kbars.Volume[i]),
                        })
                    print(f"      -> {len(kbars.Close)} bars")
                else:
                    print(f"      -> no data")
            except Exception as e:
                print(f"      -> fetch error: {e}")

            current_end = start - timedelta(days=1)
            remaining  -= fetch_days

    finally:
        api.logout()

    if not all_bars:
        raise RuntimeError("TX: no bars downloaded")

    df_1m = (pd.DataFrame(all_bars)
             .drop_duplicates(subset=["datetime"])
             .sort_values("datetime")
             .reset_index(drop=True))
    print(f"  Raw 1-min bars: {len(df_1m):,}  ({df_1m['datetime'].iloc[0]} ~ {df_1m['datetime'].iloc[-1]})")

    df_5m = resample_to_5m(df_1m, "08:45", "13:30")
    print(f"  5-min bars after session filter: {len(df_5m):,}")
    return df_5m


# ---------------------------------------------------------------------------
# Loader 3: Binance crypto — download via public REST API
# ---------------------------------------------------------------------------

BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
# weight=2 per call (limit=1000), limit=1200/min → max 600 calls/min
# conservative: 0.12s sleep between batches
_BINANCE_SLEEP = 0.12
# Use 5min interval directly — 5x fewer API calls than 1min
_BINANCE_INTERVAL = "5m"
_BINANCE_BAR_MS   = 5 * 60 * 1000  # 5min in milliseconds


def _binance_fetch_batch(symbol: str, start_ms: int, end_ms: int) -> list:
    """Fetch up to 1000 5-min klines from Binance for the given ms range."""
    params = {
        "symbol":    symbol,
        "interval":  _BINANCE_INTERVAL,
        "startTime": start_ms,
        "endTime":   end_ms,
        "limit":     1000,
    }
    resp = requests.get(BINANCE_KLINES_URL, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def download_binance(symbol: str,
                     start_date: str = "2021-01-01") -> pd.DataFrame:
    """Download 5-min klines from Binance directly, filter 08:00-16:00 TWN."""
    import datetime as _dt
    start_dt = _dt.datetime.strptime(start_date, "%Y-%m-%d")
    end_dt   = _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None)

    start_ms = int(start_dt.timestamp() * 1000)
    end_ms   = int(end_dt.timestamp() * 1000)

    print(f"  Downloading {symbol} 5m from {start_date} to {end_dt.strftime('%Y-%m-%d')} ...")

    all_rows = []
    batch_start = start_ms
    batch_num   = 0

    while batch_start < end_ms:
        # 1000 bars × 5min = 5000 min per batch
        batch_end = min(batch_start + 1000 * _BINANCE_BAR_MS, end_ms)

        try:
            rows = _binance_fetch_batch(symbol, batch_start, batch_end)
        except requests.HTTPError as e:
            print(f"    Warning: HTTP error on batch {batch_num}: {e} — retrying in 5s")
            time.sleep(5)
            try:
                rows = _binance_fetch_batch(symbol, batch_start, batch_end)
            except Exception as e2:
                print(f"    Skipping batch after retry failure: {e2}")
                rows = []
        except Exception as e:
            print(f"    Warning: error on batch {batch_num}: {e}")
            rows = []

        if rows:
            for r in rows:
                # r = [open_time_ms, open, high, low, close, volume, ...]
                # convert open_time from UTC ms to UTC+8 (TWN)
                ts_utc = _dt.datetime.fromtimestamp(r[0] / 1000.0, tz=_dt.timezone.utc)
                ts_twn = (ts_utc + _dt.timedelta(hours=8)).replace(tzinfo=None)
                all_rows.append({
                    "datetime": ts_twn,
                    "open":   float(r[1]),
                    "high":   float(r[2]),
                    "low":    float(r[3]),
                    "close":  float(r[4]),
                    "volume": float(r[5]),
                })
            # Advance to one bar after last returned candle open_time
            batch_start = rows[-1][0] + _BINANCE_BAR_MS
        else:
            batch_start = batch_end + 1

        batch_num += 1
        if batch_num % 50 == 0:
            latest = all_rows[-1]["datetime"] if all_rows else "n/a"
            print(f"    ...batch {batch_num}, collected {len(all_rows):,} bars, latest: {latest}")

        time.sleep(_BINANCE_SLEEP)

    if not all_rows:
        raise RuntimeError(f"{symbol}: no data downloaded from Binance")

    df_5m = (pd.DataFrame(all_rows)
             .drop_duplicates(subset=["datetime"])
             .sort_values("datetime")
             .reset_index(drop=True))
    print(f"  Raw 5-min bars: {len(df_5m):,}  ({df_5m['datetime'].iloc[0]} ~ {df_5m['datetime'].iloc[-1]})")

    # Filter 08:00-16:00 TWN (Asian session)
    mask = (df_5m["datetime"].dt.time >= pd.Timestamp("08:00").time()) & \
           (df_5m["datetime"].dt.time <= pd.Timestamp("16:00").time())
    df_5m = df_5m[mask].reset_index(drop=True)
    print(f"  5-min bars after 08:00-16:00 filter: {len(df_5m):,}")
    return df_5m


# ---------------------------------------------------------------------------
# Loader 4: MT5 — download via MetaTrader5 Python API
# ---------------------------------------------------------------------------

def download_mt5(mt5_symbol: str, session: tuple = None) -> pd.DataFrame:
    """
    Pull all available 5-min OHLCV bars from an open MT5 terminal.

    Timestamps are returned as naive UTC (Unix epoch from MT5 'time' field).
    US equity ETFs (QQQM, IVV) only have bars during their own market hours
    so no additional session filter is needed; we still apply one as a safeguard.

    Args:
        mt5_symbol : Symbol name as it appears in MT5 (e.g. 'QQQM').
        session    : Optional ('HH:MM', 'HH:MM') UTC window to keep.
    """
    try:
        import MetaTrader5 as mt5
    except ImportError:
        raise ImportError("MetaTrader5 package not installed. Run: pip install MetaTrader5")

    if not mt5.initialize():
        raise RuntimeError(f"MT5 initialize() failed: {mt5.last_error()}")

    try:
        info = mt5.symbol_info(mt5_symbol)
        if info is None:
            raise ValueError(f"Symbol '{mt5_symbol}' not found in MT5. "
                             f"Check symbol name or add it to Market Watch.")

        # MT5 caps single call at 99999 bars; pull all available history
        MAX_BARS = 99999
        rates = mt5.copy_rates_from_pos(mt5_symbol, mt5.TIMEFRAME_M5, 0, MAX_BARS)
        if rates is None or len(rates) == 0:
            raise RuntimeError(f"No 5-min data for {mt5_symbol}. "
                               f"MT5 error: {mt5.last_error()}")

        df = pd.DataFrame(rates)
        # 'time' column = Unix timestamp seconds (UTC)
        df["datetime"] = (pd.to_datetime(df["time"], unit="s", utc=True)
                            .dt.tz_localize(None))   # naive UTC
        vol_col = "real_volume" if "real_volume" in df.columns and df["real_volume"].sum() > 0 else "tick_volume"
        df = df.rename(columns={vol_col: "volume"})
        df = (df[["datetime", "open", "high", "low", "close", "volume"]]
                .sort_values("datetime")
                .reset_index(drop=True))

        print(f"  Raw 5-min bars: {len(df):,}  "
              f"({df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]})")

        # Apply session filter (UTC window)
        if session:
            t_start = pd.Timestamp(f"1970-01-01 {session[0]}").time()
            t_end   = pd.Timestamp(f"1970-01-01 {session[1]}").time()
            mask = (df["datetime"].dt.time >= t_start) & \
                   (df["datetime"].dt.time <= t_end)
            df = df[mask].reset_index(drop=True)
            print(f"  5-min bars after {session[0]}-{session[1]} UTC filter: {len(df):,}")

        return df

    finally:
        mt5.shutdown()


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def collect_all(output_dir: Path,
                skip_existing: bool = True,
                instruments_override: dict = None) -> None:
    """Download all instruments and save as parquet files.

    Args:
        output_dir:           Directory to write {instrument}_5m.parquet files.
        skip_existing:        If True, skip instruments whose parquet already exists.
        instruments_override: Optional dict to restrict which instruments are collected.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n=== Stage 1: Multi-Instrument Data Collection ===")
    print(f"Output directory: {output_dir}\n")

    errors = {}
    active_instruments = instruments_override if instruments_override is not None else INSTRUMENTS

    for name, cfg in active_instruments.items():
        out_path = output_dir / f"{name}_5m.parquet"
        if skip_existing and out_path.exists():
            size_mb = out_path.stat().st_size / 1024 / 1024
            print(f"[{name}] SKIP (already exists, {size_mb:.1f} MB): {out_path}")
            continue

        print(f"\n[{name}] Collecting ({cfg['type']}) ...")
        t0 = time.perf_counter()

        try:
            inst_type = cfg["type"]

            if inst_type == "shioaji_existing":
                df = load_tmf_existing()

            elif inst_type == "shioaji":
                df = download_tx_shioaji(days=800)

            elif inst_type == "binance":
                df = download_binance(name, start_date="2021-01-01")

            elif inst_type == "mt5":
                mt5_sym = cfg.get("mt5_symbol", name)
                session = cfg.get("session")
                df = download_mt5(mt5_sym, session=session)

            else:
                raise ValueError(f"Unknown instrument type: {inst_type}")

            # Validate columns
            required = {"datetime", "open", "high", "low", "close", "volume"}
            missing = required - set(df.columns)
            if missing:
                raise ValueError(f"Missing columns: {missing}")

            # Ensure datetime is tz-naive Timestamp
            df["datetime"] = pd.to_datetime(df["datetime"]).dt.tz_localize(None)

            # Ensure numeric types
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")

            df = df.dropna(subset=["open", "close"]).reset_index(drop=True)

            # Save as parquet
            df.to_parquet(out_path, index=False)
            elapsed = time.perf_counter() - t0
            size_mb = out_path.stat().st_size / 1024 / 1024
            print(f"[{name}] Saved {len(df):,} bars -> {out_path} ({size_mb:.1f} MB, {elapsed:.1f}s)")

        except Exception as e:
            elapsed = time.perf_counter() - t0
            print(f"[{name}] ERROR after {elapsed:.1f}s: {e}")
            errors[name] = str(e)

    print("\n=== Collection complete ===")
    if errors:
        print(f"Errors ({len(errors)}):")
        for name, msg in errors.items():
            print(f"  {name}: {msg}")
    else:
        print("All instruments collected successfully.")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Stage 1: Collect multi-instrument 5-min OHLCV data for ML pipeline"
    )
    parser.add_argument(
        "--output-dir",
        default=str(ROOT / "data" / "historical" / "ml"),
        help="Directory to save parquet files (default: data/historical/ml/)",
    )
    parser.add_argument(
        "--skip-existing",
        default="true",
        choices=["true", "false"],
        help="Skip instruments that already have a parquet file (default: true)",
    )
    parser.add_argument(
        "--instrument",
        default=None,
        help="Collect only this instrument (e.g. BTCUSDT). Default: all.",
    )
    args = parser.parse_args()

    skip = args.skip_existing.lower() == "true"
    out  = Path(args.output_dir)

    if args.instrument:
        inst = args.instrument.upper()
        if inst not in INSTRUMENTS:
            print(f"Unknown instrument: {inst}. Valid: {list(INSTRUMENTS)}")
            sys.exit(1)
        # Temporarily restrict to the requested instrument
        selected = {inst: INSTRUMENTS[inst]}
        collect_all(out, skip_existing=skip, instruments_override=selected)
        return

    collect_all(out, skip_existing=skip)


if __name__ == "__main__":
    main()
