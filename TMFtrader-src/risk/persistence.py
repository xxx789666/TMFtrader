"""
TMFtrader 風控狀態持久化
peak_equity、熔斷狀態、每日損益 — 重啟不遺失
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger


import os

_DATA_DIR = Path(__file__).parent.parent / "data"

def _state_file() -> Path:
    """風控狀態檔路徑。
    - paper(且有 STRATEGY_OWNER):data/paper/<owner>/risk_state.json
      → 絕不污染 live 的 risk_state.json(否則 paper 平模擬單會覆蓋 live 的 peak/熔斷狀態)。
    - 否則(live/simulation)依 DASHBOARD_PORT 區分日盤(8888)/夜盤(8889)。
    """
    if os.getenv("TRADING_MODE", "").strip().lower() == "paper":
        owner = os.getenv("STRATEGY_OWNER", "").strip()
        if owner:
            return _DATA_DIR / "paper" / owner / "risk_state.json"
    port = os.getenv("DASHBOARD_PORT", "8888")
    suffix = "_night" if port == "8889" else ""
    return _DATA_DIR / f"risk_state{suffix}.json"


def save_risk_state(
    peak_equity: float,
    daily_loss: float,
    consecutive_losses: int,
    circuit_state: str,
    halt_reason: str,
    today: str,
):
    """儲存風控狀態到磁碟"""
    state = {
        "peak_equity": peak_equity,
        "daily_loss": daily_loss,
        "consecutive_losses": consecutive_losses,
        "circuit_state": circuit_state,
        "halt_reason": halt_reason,
        "today": today,
        "updated_at": datetime.now().isoformat(),
    }
    state_file = _state_file()
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.error(f"[Persist] 儲存風控狀態失敗: {e}")


def load_risk_state() -> Optional[dict]:
    """從磁碟載入風控狀態"""
    state_file = _state_file()
    try:
        if state_file.exists():
            data = json.loads(state_file.read_text(encoding="utf-8"))
            logger.info(f"[Persist] 載入風控狀態 ({state_file.name}): peak={data.get('peak_equity', 0):,.0f} daily_loss={data.get('daily_loss', 0):,.0f} state={data.get('circuit_state', 'active')}")
            return data
    except Exception as e:
        logger.warning(f"[Persist] 載入風控狀態失敗: {e}")
    return None
