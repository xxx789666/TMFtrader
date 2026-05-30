"""
TMF 優化策略 v1 — Paper Trading 執行腳本
連接永豐 Shioaji，使用真實行情但不下真實單

用法：
    python optimized_v1/run_paper.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.stdout.reconfigure(encoding="utf-8")


def main():
    from core.logger import setup_logger
    setup_logger(console_level="INFO")

    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")

    import os
    os.environ["TRADING_MODE"] = "paper"
    os.environ["INSTRUMENTS"]  = "TMF"
    os.environ["INITIAL_BALANCE"] = os.environ.get("INITIAL_BALANCE", "200000")

    print("\n  TMF 優化策略 v1 — Paper Trading 模式")
    print("  =" * 22)
    print(f"  資金: {os.environ['INITIAL_BALANCE']} 元")
    print(f"  商品: TMF（微型台指期貨）")
    print(f"  策略: 自適應動量策略（優化 v1）")
    print()

    from scripts.start import main as start_main
    start_main()


if __name__ == "__main__":
    main()
