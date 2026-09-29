# -*- coding: utf-8 -*-
"""出場前券商部位對帳 fail-safe 回歸測試(2026-07-16 事故:chips live 首日 user App 手動平倉,
引擎不知情、13:44 強平差 6 分鐘送出反向單變真錢裸空單)。

驗證 engine._broker_net_position / _exit_reconcile_decision:
  - 券商確認空手([]) + 引擎自以為有倉 → absorb(不送單)
  - 券商反向部位 → absorb
  - 券商同向足量 → send
  - 券商同向不足(外部減碼) → reduce(只平剩餘)
  - 查詢失敗(None/例外/無介面) → send(fail-open,保命出場優先)
  - code 前綴對映(MXFH6 算 MXF、TXF 不算)

跑法:python tests/test_reconcile_exit.py(不需 pytest;純 assert,全綠才算過。)
"""
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.engine as eng

passed = 0


def check(name, cond):
    global passed
    assert cond, f"FAIL: {name}"
    passed += 1
    print(f"  ok: {name}")


def _engine_with(broker):
    e = eng.TradingEngine.__new__(eng.TradingEngine)   # 不跑 __init__,只測純函式
    e.broker = broker
    return e


def _b(positions):
    return SimpleNamespace(get_real_positions=lambda: positions)


def _raising_broker():
    def _boom():
        raise RuntimeError("connection lost")
    return SimpleNamespace(get_real_positions=_boom)


MXF1L = [{"code": "MXFH6", "quantity": 1, "direction": "Action.Buy"}]
MXF2L = [{"code": "MXFH6", "quantity": 2, "direction": "Action.Buy"}]
MXF1S = [{"code": "MXFH6", "quantity": 1, "direction": "Action.Sell"}]

# ───────── 1) _broker_net_position ─────────
print("[1] _broker_net_position")
check("確認空手([]) → 0", _engine_with(_b([])) ._broker_net_position("MXF") == 0)
check("多1口(MXFH6→MXF 前綴對映) → +1", _engine_with(_b(MXF1L))._broker_net_position("MXF") == 1)
check("空1口 → -1", _engine_with(_b(MXF1S))._broker_net_position("MXF") == -1)
check("查詢失敗(None) → None", _engine_with(_b(None))._broker_net_position("MXF") is None)
check("查詢例外 → None", _engine_with(_raising_broker())._broker_net_position("MXF") is None)
check("無 get_real_positions 介面 → None",
      _engine_with(SimpleNamespace())._broker_net_position("MXF") is None)
check("他商品(TXF)不計入 MXF",
      _engine_with(_b([{"code": "TXFH6", "quantity": 3, "direction": "Action.Buy"}]))
      ._broker_net_position("MXF") == 0)
check("多空混和取淨(2多+1空=+1)",
      _engine_with(_b(MXF2L + MXF1S))._broker_net_position("MXF") == 1)

# ───────── 2) _exit_reconcile_decision:核心事故場景 ─────────
print("[2] decision — 2026-07-16 事故場景")
d, q = _engine_with(_b([]))._exit_reconcile_decision("MXF", +1)
check("引擎自以為多1、券商已空手(user App 平倉) → absorb 不送單", d == "absorb" and q == 0)
d, q = _engine_with(_b(MXF1S))._exit_reconcile_decision("MXF", +1)
check("引擎自以為多1、券商反向空1 → absorb 不送單", d == "absorb" and q == -1)

# ───────── 3) decision:正常/減碼/fail-open ─────────
print("[3] decision — 正常/減碼/fail-open")
d, q = _engine_with(_b(MXF1L))._exit_reconcile_decision("MXF", +1)
check("同向足量(多1 vs 多1) → send 1", d == "send" and q == 1)
d, q = _engine_with(_b(MXF2L))._exit_reconcile_decision("MXF", +2)
check("同向足量(多2 vs 多2) → send 2", d == "send" and q == 2)
d, q = _engine_with(_b(MXF1L))._exit_reconcile_decision("MXF", +2)
check("外部減碼(引擎多2、券商剩1) → reduce 只平1", d == "reduce" and q == 1)
d, q = _engine_with(_b(MXF1S))._exit_reconcile_decision("MXF", -1)
check("空單同向足量(空1 vs 空1) → send 1", d == "send" and q == 1)
d, q = _engine_with(_b([]))._exit_reconcile_decision("MXF", -1)
check("空單、券商空手 → absorb", d == "absorb" and q == 0)
d, q = _engine_with(_b(None))._exit_reconcile_decision("MXF", +1)
check("查詢失敗 → send(fail-open 保命)", d == "send" and q == 1)
d, q = _engine_with(_raising_broker())._exit_reconcile_decision("MXF", +1)
check("查詢例外 → send(fail-open 保命)", d == "send" and q == 1)
d, q = _engine_with(SimpleNamespace())._exit_reconcile_decision("MXF", +1)
check("無介面(MockBroker 類) → send(不影響 paper/mock)", d == "send" and q == 1)

# ───────── 4) _entry_reconcile_check:進場前對帳(2026-07-17 手動倉=等它結束) ─────────
print("[4] entry check — 外部手動倉擋進場/歸位放行")


def _engine_pm(broker, own_qty=0, own_side="long"):
    e = _engine_with(broker)
    if own_qty:
        pos = SimpleNamespace(is_flat=False, quantity=own_qty,
                              side=SimpleNamespace(value=own_side))
        pos.side = eng.Side.LONG if own_side == "long" else eng.Side.SHORT
    else:
        pos = SimpleNamespace(is_flat=True, quantity=0, side=None)
    e.position_manager = SimpleNamespace(positions={"MXF": pos})
    return e


ok, net, own = _engine_pm(_b(MXF1L))._entry_reconcile_check("MXF")
check("引擎 flat、券商多1(user 手動倉) → 擋進場", ok is False and net == 1 and own == 0)
ok, net, own = _engine_pm(_b([]))._entry_reconcile_check("MXF")
check("引擎 flat、券商空手(歸位) → 放行", ok is True)
ok, net, own = _engine_pm(_b(MXF1L), own_qty=1)._entry_reconcile_check("MXF")
check("引擎自持多1、券商多1(加碼場景) → 放行", ok is True and own == 1)
ok, net, own = _engine_pm(_b(MXF2L), own_qty=1)._entry_reconcile_check("MXF")
check("引擎多1、券商多2(user 又疊了手動倉) → 擋", ok is False and net == 2)
ok, net, own = _engine_pm(_b(None))._entry_reconcile_check("MXF")
check("查詢失敗 → 放行(fail-open)", ok is True and net is None)

print(f"\nALL PASS: {passed} checks")
