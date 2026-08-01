# -*- coding: utf-8 -*-
"""回歸測試:2026-07-10 颱風假熔斷洗版事故。

事故:颱風臨時休市,live 引擎 24h 常駐仍連著 Shioaji;休市無 session、券商每~2分斷閒置連線,
circuit_breaker.on_connection_lost 不分時段一律 EMERGENCY_STOP + 推 TG → 多引擎各噴、洗版。
修法:非交易 session(休市/盤間空檔)的閒置斷線=預期,不緊急停機、不推 TG;
      交易 session 內真斷線仍要緊急停機(保留安全)。跨午夜 00:00-05:00 屬前一交易日夜盤。
"""
from datetime import datetime

import risk.circuit_breaker as cbmod
from risk.circuit_breaker import CircuitBreaker, CircuitState


def _freeze(monkeypatch, when):
    """凍結 circuit_breaker 模組的 datetime.now() 到指定時間。"""
    class _DT(datetime):
        @classmethod
        def now(cls, tz=None):
            return when
    monkeypatch.setattr(cbmod, "datetime", _DT)


# ---- 非交易 session:斷線應被忽略(不緊急停機、不推 TG)----

def test_conn_lost_suppressed_weekend(monkeypatch):
    _freeze(monkeypatch, datetime(2026, 7, 11, 10, 0))   # 週六
    cb = CircuitBreaker()
    cb.on_connection_lost()
    assert cb.state == CircuitState.ACTIVE, "週末閒置斷線不該緊急停機"


def test_conn_lost_suppressed_typhoon_holiday(monkeypatch):
    _freeze(monkeypatch, datetime(2026, 7, 10, 9, 50))   # 2026-07-10 颱風假(在 market_holidays.txt)日盤時段
    cb = CircuitBreaker()
    cb.on_connection_lost()
    assert cb.state == CircuitState.ACTIVE, "颱風休市日閒置斷線不該緊急停機"


def test_conn_lost_suppressed_between_sessions(monkeypatch):
    _freeze(monkeypatch, datetime(2026, 7, 9, 6, 30))    # 週四 06:30(夜盤05:00已收、日盤08:45未開)
    cb = CircuitBreaker()
    cb.on_connection_lost()
    assert cb.state == CircuitState.ACTIVE, "盤間空檔閒置斷線不該緊急停機"


# ---- 交易 session:真斷線仍要緊急停機(保留安全)----

def test_conn_lost_fires_in_day_session(monkeypatch):
    _freeze(monkeypatch, datetime(2026, 7, 9, 10, 0))    # 週四日盤
    cb = CircuitBreaker()
    cb.on_connection_lost()
    assert cb.state == CircuitState.EMERGENCY_STOP, "交易日日盤真斷線必須緊急停機"


def test_conn_lost_fires_in_night_cross_midnight(monkeypatch):
    _freeze(monkeypatch, datetime(2026, 7, 10, 2, 0))    # 7/10 02:00 = 7/9 夜盤(7/9 是交易日、非休市)
    cb = CircuitBreaker()
    cb.on_connection_lost()
    assert cb.state == CircuitState.EMERGENCY_STOP, "7/9 夜盤(延伸到7/10凌晨)真斷線必須緊急停機"


# ---- _in_trading_session 直接單元測試 ----

def test_in_trading_session_boundaries():
    cb = CircuitBreaker()
    assert cb._in_trading_session(datetime(2026, 7, 9, 9, 0)) is True      # 週四日盤
    assert cb._in_trading_session(datetime(2026, 7, 9, 16, 0)) is True     # 週四夜盤(傍晚)
    assert cb._in_trading_session(datetime(2026, 7, 10, 2, 0)) is True     # 7/9 夜盤跨午夜(7/9非休市)
    assert cb._in_trading_session(datetime(2026, 7, 9, 6, 30)) is False    # 盤間空檔
    assert cb._in_trading_session(datetime(2026, 7, 11, 10, 0)) is False   # 週六
    assert cb._in_trading_session(datetime(2026, 7, 10, 9, 50)) is False   # 颱風假日盤時段
