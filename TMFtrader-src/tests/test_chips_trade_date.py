"""chips_combo_daily.pick_trade_date regression(2026-09-29 使用者裁定:沒新資料時 trade_date 推到下一個交易日)。

背景:連假(09-25 休市、09-26/27 週末、09-28 教師節)FinMind/TAIFEX 沒新籌碼,舊規則把 trade_date 停在
「最後資料日 09-24 + 1 = 09-25」;09-29 開盤 chips_exec/wave_exec/wave_exec_c 算出過期 4 天 → 全部跳單+告警。
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import date, datetime

from scripts.chips_combo_daily import next_trading_day, pick_trade_date

HOL = {"2026-09-25", "2026-09-28", "2026-10-09", "2026-10-26"}


def test_next_trading_day_skips_weekend_and_holidays():
    assert next_trading_day(date(2026, 9, 24), HOL) == date(2026, 9, 29)   # 25 休 / 26-27 週末 / 28 休
    assert next_trading_day(date(2026, 9, 29), HOL) == date(2026, 9, 30)
    assert next_trading_day(date(2026, 10, 8), HOL) == date(2026, 10, 12)   # 10-09 休 + 週末
    assert next_trading_day(date(2026, 10, 2), HOL) == date(2026, 10, 5)    # 週五 → 週一


def test_holiday_run_advances_to_first_trading_day_after_break():
    # 09-28(教師節)18:30 跑,資料只到 09-24 → 應給 09-29(舊規則會給 09-25)
    assert pick_trade_date(date(2026, 9, 24), datetime(2026, 9, 28, 18, 30), HOL) == date(2026, 9, 29)
    # 09-25(休市週五)18:30 跑 → 也是 09-29
    assert pick_trade_date(date(2026, 9, 24), datetime(2026, 9, 25, 18, 30), HOL) == date(2026, 9, 29)


def test_normal_evening_run_gives_tomorrow():
    assert pick_trade_date(date(2026, 9, 29), datetime(2026, 9, 29, 18, 30), HOL) == date(2026, 9, 30)
    # 週五傍晚 → 下週一
    assert pick_trade_date(date(2026, 10, 2), datetime(2026, 10, 2, 18, 30), HOL) == date(2026, 10, 5)


def test_stale_feed_in_evening_still_dates_tomorrow():
    # 09-29 18:30 跑但 FinMind 慢、資料只到 09-28(其實 09-28 休市,所以是 09-24)→ 給 09-30,不是 09-29
    assert pick_trade_date(date(2026, 9, 24), datetime(2026, 9, 29, 18, 30), HOL) == date(2026, 9, 30)


def test_morning_rerun_before_open_keeps_today():
    # 開盤前 07:00 手動補跑,資料到昨天 → 給今天(不吃掉當天的單)
    assert pick_trade_date(date(2026, 9, 29), datetime(2026, 9, 30, 7, 0), HOL) == date(2026, 9, 30)


def test_afternoon_rerun_after_close_gives_next_day():
    # cron 15:10 補跑(收盤後),資料到昨天 → 今天已交易過 → 明天
    assert pick_trade_date(date(2026, 9, 29), datetime(2026, 9, 30, 15, 10), HOL) == date(2026, 10, 1)
