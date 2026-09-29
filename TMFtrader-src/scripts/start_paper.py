"""隔離 + headless paper 入口。

- cmdline 含 'start_paper.py'(不含 'scripts/start.py')→ 不被 live 的 pkill/pgrep 波及。
- **不啟動 dashboard(FastAPI/uvicorn)**:省記憶體(paper 只需前推數據,不需 UI)。
  引擎事件(進場/出場/止損/止盈/追蹤/盤末強平/熔斷)照樣經 core.notify → Telegram 推播。
- 直接 engine.initialize() + engine.start()(等同 dashboard lifespan 會做的),
  再 block 主執行緒讓 daemon event-loop 持續運作。
- TRADING_MODE / STRATEGY_TYPE / STRATEGY_OWNER / TIMEFRAME / INSTRUMENTS 由 launcher env 帶入。

接受並忽略 dashboard 相關旗標(--port / --no-browser),沿用 --mode / --risk。
"""
import argparse
import os
import sys
import threading
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS.parent))   # PROJECT_ROOT


def main():
    p = argparse.ArgumentParser(description="TMFtrader paper headless runner（無 dashboard、事件走 TG）")
    p.add_argument("--mode")
    p.add_argument("--risk")
    p.add_argument("--port", type=int)                    # 忽略：headless 無 dashboard
    p.add_argument("--no-browser", action="store_true")   # 忽略
    p.add_argument("--no-dashboard", action="store_true")  # 預設行為，留著相容
    args, _ = p.parse_known_args()
    if args.mode:
        os.environ["TRADING_MODE"] = args.mode
    if args.risk:
        os.environ["RISK_PROFILE"] = args.risk

    from core.logger import setup_logger
    setup_logger()
    from loguru import logger
    from core.engine import TradingEngine

    engine = TradingEngine()
    if not engine.initialize():
        from loguru import logger as _lg
        _lg.error("[Paper] 引擎初始化失敗")
        sys.exit(1)
    engine.start()
    logger.info(
        f"[Paper] headless 啟動完成（無 dashboard、事件走 TG）| "
        f"mode={engine.trading_mode} instruments={engine.instruments} TF={engine.timeframe}"
    )
    try:
        threading.Event().wait()   # block 主執行緒;event-loop 在 daemon thread 跑
    except KeyboardInterrupt:
        logger.info("[Paper] 收到中斷,停止引擎...")
        engine.stop()


if __name__ == "__main__":
    main()
