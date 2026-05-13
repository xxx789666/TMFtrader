"""跨 process 持倉鎖 — 確保 breakout（日盤策略 24h）與 ORB（夜盤策略）同時只有一個策略持倉。

設計：
  - 共用檔案：data/active_position.json
  - 進場前：check is_blocked() → 若被對方持倉、跳過
  - 進場後：acquire() 寫鎖
  - 出場後：release() 刪鎖
  - Stale 保護：> 12h 未更新自動 unlink（避免 process crash 留下殭屍鎖）

兩個 process 各自呼叫、不會干擾：
  breakout (engine.py): owner='breakout'
  ORB (paper_night_orb.py): owner='orb'
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

LOCK_FILE = Path(__file__).resolve().parent.parent / "data" / "active_position.json"
STALE_HOURS = 12


def _read() -> Optional[dict]:
    if not LOCK_FILE.exists():
        return None
    try:
        return json.loads(LOCK_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _is_stale(data: dict) -> bool:
    return (time.time() - data.get("entry_unix", 0)) > STALE_HOURS * 3600


def get_holder() -> Optional[str]:
    """回傳目前持鎖者 'breakout' / 'orb'、或 None。"""
    data = _read()
    if not data:
        return None
    if _is_stale(data):
        try:
            LOCK_FILE.unlink()
        except OSError:
            pass
        return None
    return data.get("owner")


def is_blocked(my_owner: str) -> Optional[dict]:
    """若**對方**策略持倉、回傳鎖內容；否則回 None（含我自己持有的情況）。"""
    data = _read()
    if not data:
        return None
    if _is_stale(data):
        try:
            LOCK_FILE.unlink()
        except OSError:
            pass
        return None
    holder = data.get("owner")
    if holder and holder != my_owner:
        return data
    return None


def acquire(owner: str, side: str, entry_price: float, instrument: str,
            quantity: int = 1, **extra) -> None:
    """寫鎖。Caller 應該已先 check is_blocked() = None。"""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "owner": owner,
        "side": side,
        "entry_price": entry_price,
        "instrument": instrument,
        "quantity": quantity,
        "entry_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "entry_unix": time.time(),
        "pid": os.getpid(),
        **extra,
    }
    LOCK_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def release(owner: str) -> None:
    """刪鎖（只有自己持有時才刪、避免誤刪對方的）。"""
    if not LOCK_FILE.exists():
        return
    try:
        data = json.loads(LOCK_FILE.read_text(encoding="utf-8"))
        if data.get("owner") == owner:
            LOCK_FILE.unlink()
    except Exception:
        # 檔案壞了直接刪
        try:
            LOCK_FILE.unlink()
        except OSError:
            pass
