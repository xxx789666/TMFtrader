"""Hermes Agent 用的 JSON 介面（透過 execute_code 呼叫）。

範例：
    python -m review.tools_for_hermes load_daily --date=2026-05-12 --session=day
    python -m review.tools_for_hermes load_week --week_ending=2026-05-15
    python -m review.tools_for_hermes load_week --week_ending=today

輸出固定為 stdout 一個 JSON 物件，方便 agent 直接解析。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date as date_cls

from .loader import load_daily, load_week, load_session_edge


def _sanitize(obj):
    """把 inf / -inf / nan 換成 None，輸出符合嚴格 JSON。"""
    if isinstance(obj, float):
        if math.isinf(obj) or math.isnan(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return obj


def _resolve_date(s: str) -> str:
    return date_cls.today().isoformat() if s == "today" else s


def _cmd_load_daily(args: argparse.Namespace) -> dict:
    stats = load_daily(_resolve_date(args.date), args.session)
    out = stats.to_dict()
    if args.compact:
        out.pop("trades", None)
        out.pop("signals", None)
    return out


def _cmd_load_week(args: argparse.Namespace) -> dict:
    stats = load_week(_resolve_date(args.week_ending))
    out = stats.to_dict()
    if args.compact:
        out.pop("sessions", None)
    return out


def _cmd_load_session_edge(args: argparse.Namespace) -> dict:
    return load_session_edge()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Hermes Agent 用的資料介面")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_daily = sub.add_parser("load_daily", help="讀當日交易 + Python 端統計")
    p_daily.add_argument("--date", default="today")
    p_daily.add_argument("--session", choices=["day", "night"], default="day")
    p_daily.add_argument(
        "--compact",
        action="store_true",
        help="省略 trades / signals 原始陣列，只回統計",
    )
    p_daily.set_defaults(func=_cmd_load_daily)

    p_week = sub.add_parser("load_week", help="讀某週 Mon-Fri 的日盤+夜盤聚合統計")
    p_week.add_argument("--week_ending", default="today", help="該週任一天（會對齊到 Mon-Fri）")
    p_week.add_argument(
        "--compact",
        action="store_true",
        help="省略 sessions 細節，只回週度聚合 + daily_rollup",
    )
    p_week.set_defaults(func=_cmd_load_week)

    p_edge = sub.add_parser("load_session_edge", help="讀帳戶級日盤vs夜盤累積 edge（與券商對帳一致）")
    p_edge.set_defaults(func=_cmd_load_session_edge)

    args = p.parse_args(argv)

    try:
        result = _sanitize(args.func(args))
        sys.stdout.write(json.dumps(result, ensure_ascii=False, default=str, indent=2, allow_nan=False))
        sys.stdout.write("\n")
        return 0
    except FileNotFoundError as e:
        sys.stdout.write(json.dumps({"error": "not_found", "detail": str(e)}, ensure_ascii=False))
        sys.stdout.write("\n")
        return 2
    except Exception as e:  # noqa: BLE001
        sys.stdout.write(json.dumps({"error": type(e).__name__, "detail": str(e)}, ensure_ascii=False))
        sys.stdout.write("\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
