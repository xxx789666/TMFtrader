"""
TMF 優化策略 v1 — 回測執行腳本
優化日期：2026-04-10
資料區間：2026-01-29 ~ 2026-04-10（60天，37,837根K棒）

用法：
    python optimized_v1/run_backtest.py
    python optimized_v1/run_backtest.py --balance 200000
    python optimized_v1/run_backtest.py --risk conservative
    python optimized_v1/run_backtest.py --export results.json
"""

import sys
import json
import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.stdout.reconfigure(encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="TMF 優化策略 v1 回測")
    parser.add_argument("--data",    default=None,       help="CSV 路徑（預設自動尋找最新檔）")
    parser.add_argument("--balance", type=float, default=200000.0, help="初始資金（預設 200,000）")
    parser.add_argument("--risk",    default="balanced",
                        choices=["conservative", "balanced", "aggressive"],
                        help="風險等級（預設 balanced）")
    parser.add_argument("--export",  default=None,       help="輸出 JSON 報告路徑")
    args = parser.parse_args()

    from core.logger import setup_logger
    setup_logger(console_level="WARNING")

    from backtest.data_loader import DataLoader
    from backtest.engine import BacktestEngine
    from backtest.report import BacktestReport
    from strategy.momentum import AdaptiveMomentumStrategy

    # ── 尋找資料檔 ──────────────────────────────────────────────────────────
    if args.data:
        data_path = args.data
    else:
        data_dir = PROJECT_ROOT / "data" / "historical"
        csv_files = sorted(data_dir.glob("tmf_*.csv"), reverse=True)
        if not csv_files:
            print("找不到 TMF 歷史資料，請先執行：")
            print("  python scripts/fetch_historical.py --days 60")
            sys.exit(1)
        data_path = str(csv_files[0])

    print(f"\n  載入資料：{data_path}")
    data = DataLoader.load_csv(data_path)
    print(f"  K棒數量：{len(data)} 根")
    print(f"  資料期間：{data.iloc[0]['datetime']} ~ {data.iloc[-1]['datetime']}")
    print()

    # ── 執行回測 ─────────────────────────────────────────────────────────────
    strategy = AdaptiveMomentumStrategy()
    engine   = BacktestEngine(
        initial_balance=args.balance,
        slippage=1,
        commission=18.0,
        instrument="TMF",
    )

    print(f"  策略：{strategy.name}")
    print(f"  資金：{args.balance:,.0f} 元 ｜ 風險等級：{args.risk}")
    print()

    result = engine.run(data, strategy, args.risk)

    # ── 輸出報告 ─────────────────────────────────────────────────────────────
    report = BacktestReport(result)
    report.print_report()

    # ── 匯出 JSON（選用）──────────────────────────────────────────────────────
    if args.export:
        export_path = Path(args.export)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        with open(export_path, "w", encoding="utf-8") as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
        print(f"  報告已匯出：{export_path}")


if __name__ == "__main__":
    main()
