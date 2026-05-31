"""跨 process 持倉鎖 — 確保「同一交易模式下」同時只有一個策略持倉。

設計:
  - 鎖檔**依模式分離**(關鍵:避免 paper 策略誤擋 live 真實下單):
      live / simulation → data/active_position.json
      paper            → data/active_position_paper.json
  - 進場前:check is_blocked() → 若被「同模式的對方策略」持倉、跳過
  - 進場後:acquire() 寫鎖
  - 出場後:release() 刪鎖
  - Stale 保護:> 12h 未更新自動 unlink(避免 process crash 留下殭屍鎖)

owner 範例:live breakout='breakout';paper day_orb='day_orb' / night_v3='night_v3'。
mode 由 caller(engine)傳入(預設 'live' 以向後相容既有呼叫)。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

_DATA = Path(__file__).resolve().parent.parent / "data"
LIVE_LOCK_FILE = _DATA / "active_position.json"
PAPER_LOCK_FILE = _DATA / "active_position_paper.json"
LOCK_FILE = LIVE_LOCK_FILE  # 向後相容別名（舊程式/測試直接引用）
STALE_HOURS = 12


def _lock_file(mode: str = "live") -> Path:
    """paper 用獨立鎖檔,其餘(live/simulation)用主鎖檔。"""
    return PAPER_LOCK_FILE if mode == "paper" else LIVE_LOCK_FILE


def _read(mode: str = "live") -> Optional[dict]:
    f = _lock_file(mode)
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _is_stale(data: dict) -> bool:
    return (time.time() - data.get("entry_unix", 0)) > STALE_HOURS * 3600


def get_holder(mode: str = "live") -> Optional[str]:
    """回傳該模式目前持鎖者、或 None。"""
    data = _read(mode)
    if not data:
        return None
    if _is_stale(data):
        try:
            _lock_file(mode).unlink()
        except OSError:
            pass
        return None
    return data.get("owner")


def is_blocked(my_owner: str, mode: str = "live") -> Optional[dict]:
    """若**同模式下的對方**策略持倉、回傳鎖內容;否則 None(含我自己持有的情況)。"""
    data = _read(mode)
    if not data:
        return None
    if _is_stale(data):
        try:
            _lock_file(mode).unlink()
        except OSError:
            pass
        return None
    holder = data.get("owner")
    if holder and holder != my_owner:
        return data
    return None


def acquire(owner: str, side: str, entry_price: float, instrument: str,
            quantity: int = 1, **extra) -> None:
    """寫鎖。鎖檔依 extra['mode']('paper'/'live')決定。Caller 應先 check is_blocked()=None。"""
    mode = extra.get("mode", "live")
    f = _lock_file(mode)
    f.parent.mkdir(parents=True, exist_ok=True)
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
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def release(owner: str, mode: str = "live") -> None:
    """刪鎖(只有自己持有時才刪、避免誤刪對方的)。"""
    f = _lock_file(mode)
    if not f.exists():
        return
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if data.get("owner") == owner:
            f.unlink()
    except Exception:
        # 檔案壞了直接刪
        try:
            f.unlink()
        except OSError:
            pass
