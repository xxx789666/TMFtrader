"""非交易日守門前移 regression(2026-09-27 週日事故)。

背景:常駐 exec 引擎(wave_exec / wave_exec_c / chips_exec)週五收盤後不停,週末 WallClock
仍推進,策略 _entry_decision 在 08:45-09:30 照常跑 → 對著休市日的 next_signal.json 推
「訊號疑似過期」+「空手」六則 TG。_is_trading_day 原本只擋 _execute_entry(下單),擋不到
策略層在下單前就發的告警。

修法:TradingEngine._non_trading_day_skip(instrument, hook) 放在 check_entry_tick / on_kbar
兩個進場 hook 之前;非交易日策略不被呼叫。這裡驗:
  1. _is_trading_day 對 週日 / 假日(market_holidays.txt 內 2026-09-28)/ 平日 / 週六凌晨夜盤 的判定
  2. _non_trading_day_skip 在非交易日回 True 且同日同 hook 只 log 一次;交易日回 False 不留痕
不啟動引擎、不連券商:用 __new__ 繞過 __init__,只測這兩個純函式。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime

from core.engine import TradingEngine

HOLIDAYS = Path(__file__).parent.parent / "scripts" / "market_holidays.txt"


def _bare_engine():
    e = TradingEngine.__new__(TradingEngine)     # 不跑 __init__(不連券商、不讀 .env)
    return e


def test_is_trading_day_calendar():
    e = _bare_engine()
    assert e._is_trading_day(datetime(2026, 9, 27, 9, 30)) is False        # 週日(事故當天)
    assert e._is_trading_day(datetime(2026, 9, 29, 9, 30)) is True         # 週二平日
    assert e._is_trading_day(datetime(2026, 10, 1, 9, 30)) is True         # 週四平日
    if "2026-09-28" in HOLIDAYS.read_text(encoding="utf-8"):
        assert e._is_trading_day(datetime(2026, 9, 28, 9, 30)) is False    # 教師節休市(清單內)
    # 夜盤跨午夜:週五(平日)夜盤延到週六 05:00 歸週五 → 合法
    assert e._is_trading_day(datetime(2026, 10, 3, 5, 0)) is True          # 10-02 週五夜盤尾
    assert e._is_trading_day(datetime(2026, 10, 3, 9, 0)) is False         # 週六白天


def test_non_trading_day_skip_blocks_and_logs_once(monkeypatch):
    e = _bare_engine()
    monkeypatch.setattr(e, "_is_trading_day", lambda now=None: False)
    calls = []
    import core.engine as eng
    monkeypatch.setattr(eng.logger, "info", lambda msg, *a, **k: calls.append(str(msg)))
    assert e._non_trading_day_skip("MXF", "check_entry_tick") is True
    assert e._non_trading_day_skip("MXF", "check_entry_tick") is True
    assert e._non_trading_day_skip("MXF", "check_entry_tick") is True
    assert len([c for c in calls if "NonTradingDay" in c]) == 1              # 同日同 hook 只一行
    assert e._non_trading_day_skip("MXF", "on_kbar") is True
    assert len([c for c in calls if "NonTradingDay" in c]) == 2              # 不同 hook 各一行


def test_non_trading_day_skip_passthrough_on_trading_day(monkeypatch):
    e = _bare_engine()
    monkeypatch.setattr(e, "_is_trading_day", lambda now=None: True)
    import core.engine as eng
    calls = []
    monkeypatch.setattr(eng.logger, "info", lambda msg, *a, **k: calls.append(str(msg)))
    assert e._non_trading_day_skip("MXF", "check_entry_tick") is False
    assert calls == []
    assert not hasattr(e, "_ntd_logged")
