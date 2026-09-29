"""驗證多策略 position_lock owner 參數化 + reconcile lock-aware。

跑法：python tests/test_multi_strategy_lock.py
（不需 pytest；純 assert，全綠才算過。）
"""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.engine as eng
from core import position_lock
from core.instrument_config import get_spec

# 靜音 TG（reconcile halt 會推播）
eng.tg = lambda *a, **k: None

SPEC_CODE = get_spec("TMF").code


def _clear_lock():
    files = [position_lock.LIVE_LOCK_FILE,
             position_lock.paper_lock_file("day_orb"),
             position_lock.paper_lock_file("breakout_v7"),
             position_lock.paper_lock_file("night_v3")]
    for f in files:
        if f.exists():
            f.unlink()


def _make_engine(spec_type="breakout"):
    e = eng.TradingEngine()
    e.trading_mode = "live"
    e.instruments = ["TMF"]
    e.pipelines = {"TMF": SimpleNamespace(
        spec=SimpleNamespace(strategy_type=spec_type, code=SPEC_CODE))}
    e.position_manager = SimpleNamespace(positions={
        "TMF": SimpleNamespace(is_flat=True, quantity=0,
                               side=SimpleNamespace(value="flat"))})
    e._reconcile_halt = {}
    e._reconcile_last_alert = {}
    return e


def _broker(positions):
    return SimpleNamespace(get_real_positions=lambda: positions)


def _pos(qty, direction="Buy"):
    return [{"code": SPEC_CODE, "quantity": qty, "direction": direction}]


passed = 0

def check(name, cond):
    global passed
    assert cond, f"FAIL: {name}"
    passed += 1
    print(f"  ok: {name}")


# ───────── 1) _position_owner 推導 ─────────
print("[1] _position_owner derivation")
os.environ.pop("STRATEGY_OWNER", None)
e = _make_engine(spec_type="breakout")
check("spec breakout → owner=breakout (向後相容)", e._position_owner("TMF") == "breakout")
e2 = _make_engine(spec_type="day_orb")
check("spec day_orb → owner=day_orb", e2._position_owner("TMF") == "day_orb")
os.environ["STRATEGY_OWNER"] = "night_v3"
check("STRATEGY_OWNER env 覆寫一切", e2._position_owner("TMF") == "night_v3")
os.environ.pop("STRATEGY_OWNER", None)
check("無 pipe → fallback breakout", e._position_owner("ZZZ") == "breakout")

# ───────── 2) position_lock 跨 owner 互斥 ─────────
print("[2] position_lock mutual exclusion")
_clear_lock()
position_lock.acquire(owner="day_orb", side="buy", entry_price=1, instrument="TMF", quantity=1)
check("day_orb 持鎖 → breakout 被擋", position_lock.is_blocked("breakout") is not None)
check("day_orb 持鎖 → day_orb 自己不被擋", position_lock.is_blocked("day_orb") is None)
position_lock.release("breakout")  # 非持有者不得誤刪
check("非持有者 release 不刪鎖", position_lock.get_holder() == "day_orb")
position_lock.release("day_orb")
check("持有者 release 成功", position_lock.get_holder() is None)

# ───────── 3) reconcile lock-aware ─────────
print("[3] reconcile lock-aware (live)")

# 3a 一致(都 flat) → 不 halt
_clear_lock()
e = _make_engine("breakout"); e.broker = _broker([])
e._reconcile_positions()
check("flat/flat 一致 → 不 halt", e._reconcile_halt.get("TMF") is not True)

# 3b 跨策略：我方 flat + 券商 long×1 + 另一 owner(day_orb) 持鎖且相符 → 不 halt
_clear_lock()
position_lock.acquire(owner="day_orb", side="buy", entry_price=1, instrument="TMF", quantity=1)
e = _make_engine("breakout"); e.broker = _broker(_pos(1, "Buy"))
e._reconcile_positions()
check("跨策略持倉(side/qty 相符) → 不 halt", e._reconcile_halt.get("TMF") is not True)

# 3c 跨策略但 qty 不符(鎖 1、券商 2) → 仍 halt(真背離)
_clear_lock()
position_lock.acquire(owner="day_orb", side="buy", entry_price=1, instrument="TMF", quantity=1)
e = _make_engine("breakout"); e.broker = _broker(_pos(2, "Buy"))
e._reconcile_positions()
check("跨策略但 qty 不符 → halt", e._reconcile_halt.get("TMF") is True)

# 3d 跨策略但 side 不符(鎖 buy/long、券商 short) → 仍 halt
_clear_lock()
position_lock.acquire(owner="day_orb", side="buy", entry_price=1, instrument="TMF", quantity=1)
e = _make_engine("breakout"); e.broker = _broker(_pos(1, "Sell"))
e._reconcile_positions()
check("跨策略但 side 不符 → halt", e._reconcile_halt.get("TMF") is True)

# 3e 無鎖 + 券商有倉(rogue/手動) → halt
_clear_lock()
e = _make_engine("breakout"); e.broker = _broker(_pos(1, "Buy"))
e._reconcile_positions()
check("無鎖卻有券商倉(rogue) → halt", e._reconcile_halt.get("TMF") is True)

# 3f 我方持鎖(同 owner)卻 flat、券商有倉 → halt(自己的 stale/ghost，非跨策略)
_clear_lock()
position_lock.acquire(owner="breakout", side="buy", entry_price=1, instrument="TMF", quantity=1)
e = _make_engine("breakout"); e.broker = _broker(_pos(1, "Buy"))
e._reconcile_positions()
check("自有 owner 持鎖但引擎 flat → halt(非跨策略)", e._reconcile_halt.get("TMF") is True)

# 3g halt 後券商歸零 → 自動解除 halt
_clear_lock()
e = _make_engine("breakout"); e.broker = _broker(_pos(1, "Buy"))
e._reconcile_positions()
assert e._reconcile_halt.get("TMF") is True
e.broker = _broker([])
e._reconcile_positions()
check("券商歸位後自動解除 halt", e._reconcile_halt.get("TMF") is False)

_clear_lock()

# ───────── 4) paper 隔離：每 owner 獨立鎖；paper 不擋 live、paper 之間互不干擾 ─────────
print("[4] paper isolation (per-owner; paper never blocks live)")
_clear_lock()
os.environ["STRATEGY_OWNER"] = "day_orb"
position_lock.acquire(owner="day_orb", side="buy", entry_price=1, instrument="TMF", quantity=1, mode="paper")
check("paper 鎖寫到 data/paper/day_orb/、不碰 live 檔",
      position_lock.paper_lock_file("day_orb").exists() and not position_lock.LIVE_LOCK_FILE.exists())
check("live breakout 不被 paper 鎖擋（關鍵安全性）",
      position_lock.is_blocked("breakout", mode="live") is None)
os.environ["STRATEGY_OWNER"] = "breakout_v7"
check("另一支 paper(breakout_v7) 與 day_orb 互不干擾（可獨立評估）",
      position_lock.is_blocked("breakout_v7", mode="paper") is None)
os.environ["STRATEGY_OWNER"] = "day_orb"
position_lock.release("day_orb", mode="paper")
check("paper release 只刪自己 owner 的鎖", not position_lock.paper_lock_file("day_orb").exists())
os.environ.pop("STRATEGY_OWNER", None)
position_lock.acquire(owner="breakout", side="buy", entry_price=1, instrument="TMF", quantity=1, mode="live")
check("反向：paper day_orb 不被 live 鎖擋",
      position_lock.is_blocked("day_orb", mode="paper") is None)
position_lock.release("breakout", mode="live")
_clear_lock()

print(f"\nALL PASS ({passed} checks)")
