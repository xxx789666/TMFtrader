"""
開盤後自動拉取真實 K 棒 + 跑回測
用法：python scripts/auto_backtest.py
（建議在 09:00 後執行，Shioaji 需要盤中才能登入）
"""

import sys
import os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import shioaji as sj
from datetime import datetime, timedelta
import pandas as pd
from dotenv import load_dotenv

load_dotenv()


def fetch_and_backtest():
    # ---- 1. 登入 Shioaji ----
    api = sj.Shioaji()
    print("[1/4] Login Shioaji...")
    try:
        api.login(
            api_key=os.environ["SHIOAJI_API_KEY"],
            secret_key=os.environ["SHIOAJI_SECRET_KEY"],
        )
    except Exception as e:
        print(f"FAIL: {e}")
        print("Shioaji 需要在盤中（08:45~13:45）或盤前才能登入")
        return

    # ---- 2. 拉取 TMF 近月合約歷史 K 棒 ----
    print("[2/4] Fetch TMF historical kbars...")
    try:
        contract = min(
            [c for c in api.Contracts.Futures.MXF
             if hasattr(c, "delivery_month") and c.delivery_month],
            key=lambda c: c.delivery_month,
        )
    except Exception:
        contract = api.Contracts.Futures.MXF.MXF1
    print(f"  Contract: {contract.code} ({contract.name})")

    all_bars = []
    end_date = datetime.now()
    # 拉 60 天，每批 5 天
    for batch_start in range(0, 60, 5):
        batch_end_dt = end_date - timedelta(days=batch_start)
        batch_start_dt = batch_end_dt - timedelta(days=5)
        start_str = batch_start_dt.strftime("%Y-%m-%d")
        end_str = batch_end_dt.strftime("%Y-%m-%d")
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
                        "open": float(kbars.Open[i]),
                        "high": float(kbars.High[i]),
                        "low": float(kbars.Low[i]),
                        "close": float(kbars.Close[i]),
                        "volume": int(kbars.Volume[i]),
                    })
                print(f"  {start_str}~{end_str}: {len(kbars.Close)} bars")
        except Exception as e:
            print(f"  {start_str}~{end_str}: FAIL {e}")

    api.logout()

    if not all_bars:
        print("No data fetched!")
        return

    df = pd.DataFrame(all_bars)
    df = df.drop_duplicates(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)

    # 存檔
    data_dir = Path(__file__).parent.parent / "data" / "historical"
    data_dir.mkdir(parents=True, exist_ok=True)
    filename = f"tmf_{end_date.strftime('%Y%m%d')}_1m.csv"
    filepath = data_dir / filename
    df.to_csv(filepath, index=False)
    print(f"  Saved: {filepath} ({len(df)} bars)")
    print(f"  Range: {df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]}")
    print(f"  Price: {df['close'].min():.0f} ~ {df['close'].max():.0f}")

    # ---- 3. 跑回測 ----
    print("[3/4] Run backtest...")
    from core.logger import setup_logger
    setup_logger(console_level="WARNING")

    from backtest.engine import BacktestEngine
    from backtest.report import BacktestReport
    from strategy.momentum import AdaptiveMomentumStrategy

    engine = BacktestEngine(
        initial_balance=43000.0,
        slippage=1,
        commission=18.0,
        instrument="TMF",
    )
    result = engine.run(df, AdaptiveMomentumStrategy(), "balanced")

    # ---- 4. 報告 ----
    print("[4/4] Results:")
    report = BacktestReport(result)
    report.print_report()

    # 存回測結果
    import json
    result_dir = Path(__file__).parent.parent / "data" / "backtest_results"
    result_dir.mkdir(parents=True, exist_ok=True)
    result_file = result_dir / f"backtest_{end_date.strftime('%Y%m%d')}.json"
    with open(result_file, "w", encoding="utf-8") as f:
        json.dump({
            "date": end_date.isoformat(),
            "data_file": str(filepath),
            "total_bars": result.total_bars,
            "trades": len(result.trades),
            "initial_balance": result.initial_balance,
            "final_balance": result.final_balance,
            "total_pnl": result.final_balance - result.initial_balance,
            "return_pct": (result.final_balance - result.initial_balance) / result.initial_balance * 100,
            "trade_details": result.trades,
        }, f, ensure_ascii=False, indent=2)
    print(f"  Result saved: {result_file}")


if __name__ == "__main__":
    fetch_and_backtest()
