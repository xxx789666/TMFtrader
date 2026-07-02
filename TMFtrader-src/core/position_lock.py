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
LOCK_FILE = LIVE_LOCK_FILE  # 向後相容別名（舊程式/測試直接引用）
STALE_HOURS = 12


def paper_owner() -> str:
    return os.getenv("STRATEGY_OWNER", "").strip() or "paper"


def paper_lock_file(owner: str | None = None) -> Path:
    return _DATA / "paper" / (owner or paper_owner()) / "active_position.json"


def _lock_file(mode: str = "live") -> Path:
    """live/simulation 用主鎖檔;paper 用「每個 owner 獨立」鎖檔
    (data/paper/<owner>/active_position.json)→ paper 既不擋 live、各 paper 策略間也互不干擾,
    可獨立前推評估。"""
    return paper_lock_file() if mode == "paper" else LIVE_LOCK_FILE


def _read(mode: str = "live") -> Optional[dict]:
    f = _lock_file(mode)
    if not f.exists():
        return None
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _is_stale(data: dict) -> bool:
    # 多日持倉策略(maxpain_exec live 抱到週選結算)可在 acquire 時帶 stale_hours 覆寫
    # (launcher env POSITION_LOCK_STALE_HOURS);否則沿用預設 12h(日內策略殭屍鎖保護)。
    try:
        limit = float(data.get("stale_hours") or STALE_HOURS)
    except (TypeError, ValueError):
        limit = STALE_HOURS
    return (time.time() - data.get("entry_unix", 0)) > limit * 3600


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
    """寫鎖。鎖檔依 extra['mode']('paper'/'live')決定。Caller 應先 check is_blocked()=None。
    launcher 設 POSITION_LOCK_STALE_HOURS(如 maxpain_exec live 多日持倉設 220)→ 寫進鎖檔,
    讓「所有讀鎖的 process」都用該時效判 stale(否則 12h 預設會把多日倉的鎖當殭屍刪掉)。"""
    mode = extra.get("mode", "live")
    env_stale = os.getenv("POSITION_LOCK_STALE_HOURS", "").strip()
    if env_stale and "stale_hours" not in extra:
        try:
            extra["stale_hours"] = float(env_stale)
        except ValueError:
            pass
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
