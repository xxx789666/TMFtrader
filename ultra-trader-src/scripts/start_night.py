"""夜盤 ORB Server 啟動腳本（TMFN / Port 8889）"""
import os, sys
from pathlib import Path

# 在 dotenv / 任何 import 之前強制設定
os.environ["TRADING_MODE"]    = "paper"
os.environ["RISK_PROFILE"]    = "tmf_3x"
os.environ["INSTRUMENTS"]     = "TMFN"
os.environ["INITIAL_BALANCE"] = "203930"  # 5/1 後夜盤累計 +3,930（-180 -150 +1920 +2340）
os.environ["DASHBOARD_PORT"]  = "8889"
os.environ["TIMEFRAME"]       = "5"

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.logger import setup_logger
setup_logger()

from core.engine import TradingEngine
engine = TradingEngine()
if not engine.initialize():
    print("[X] 引擎初始化失敗")
    sys.exit(1)

port = int(os.getenv("DASHBOARD_PORT", "8889"))
host = os.getenv("DASHBOARD_HOST", "127.0.0.1")

from dashboard.app import create_app
app = create_app(engine)

print(f"  Dashboard: http://{host}:{port}")
print(f"  商品: {', '.join(engine.instruments)}")

import uvicorn
try:
    uvicorn.run(app, host=host, port=port, log_level="warning")
except KeyboardInterrupt:
    engine.stop()
