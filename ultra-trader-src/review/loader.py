"""讀當日 / 當週交易 JSON + Python 端先算好的統計（不丟給 LLM 算數）。

資料根目錄 (DATA_ROOT) 決定順序：
  1. env var TMF_DATA_ROOT —— 用於指向 live paper 資料夾（不在本 repo 內）
  2. fallback 到 ../data —— 本 repo 內的快照，供開發/測試用
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import date as date_cls, datetime, timedelta
from pathlib import Path
from typing import Literal


def _resolve_data_root() -> Path:
    """優先用 env var；否則 fallback 到 repo 內 data/。"""
    env = os.environ.get("TMF_DATA_ROOT")
    if env:
        p = Path(env).expanduser()
        if p.exists():
            return p
        # env var 給了但不存在 —— 視為硬錯，避免悄悄讀錯資料
        raise FileNotFoundError(
            f"TMF_DATA_ROOT={env} 不存在；請確認路徑或 unset 此環境變數"
        )
    return Path(__file__).resolve().parent.parent / "data"


DATA_ROOT = _resolve_data_root()
DAILY_DIR = DATA_ROOT / "performance" / "daily"
RISK_DAY = DATA_ROOT / "risk_state.json"
RISK_NIGHT = DATA_ROOT / "risk_state_night.json"

Session = Literal["day", "night"]


@dataclass
class DailyStats:
    date: str
    session: Session
    trading_mode: str
    n_trades: int
    n_wins: int
    n_losses: int
    win_rate: float
    gross_profit: float
    gross_loss: float
    net_pnl: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    avg_mfe: float
    avg_mae: float
    avg_bars_held: float
    sides: dict[str, int]
    trades: list[dict] = field(default_factory=list)
    signals: list[dict] = field(default_factory=list)
    risk_state: dict | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _daily_file(date: str, session: Session) -> Path:
    suffix = "_live_night.json" if session == "night" else "_live.json"
    return DAILY_DIR / f"{date}{suffix}"


def _safe_div(a: float, b: float) -> float:
    return a / b if b else 0.0


def load_daily(date: str, session: Session = "day") -> DailyStats:
    """讀當日 JSON 並計算統計。找不到檔案會丟 FileNotFoundError。"""
    path = _daily_file(date, session)
    if not path.exists():
        raise FileNotFoundError(f"找不到當日交易檔: {path}")

    raw = json.loads(path.read_text(encoding="utf-8"))
    trades: list[dict] = raw.get("trades", [])
    signals: list[dict] = raw.get("paper_signals", [])

    # 2026-05-23: 加 paper filter、預設不過濾、env var EXCLUDE_PAPER_TRADES=true 才開
    # trade.reason 開頭 [PAPER] 視為 paper、過濾；[LIVE] 或無前綴視為 live、保留
    import os as _os
    if _os.environ.get("EXCLUDE_PAPER_TRADES", "").lower() in ("1", "true", "yes"):
        trades = [t for t in trades if not str(t.get("reason", "")).startswith("[PAPER]")]
        signals = [s for s in signals if not str(s.get("reason", "")).startswith("[PAPER]")]

    wins = [t for t in trades if (t.get("net_pnl") or t.get("pnl") or 0) > 0]
    losses = [t for t in trades if (t.get("net_pnl") or t.get("pnl") or 0) < 0]

    gross_profit = sum((t.get("net_pnl") or t.get("pnl") or 0) for t in wins)
    gross_loss = abs(sum((t.get("net_pnl") or t.get("pnl") or 0) for t in losses))
    net_pnl = sum((t.get("net_pnl") or t.get("pnl") or 0) for t in trades)

    sides: dict[str, int] = {}
    for t in trades:
        sides[t.get("side", "?")] = sides.get(t.get("side", "?"), 0) + 1

    risk_path = RISK_NIGHT if session == "night" else RISK_DAY
    risk_state = (
        json.loads(risk_path.read_text(encoding="utf-8")) if risk_path.exists() else None
    )

    return DailyStats(
        date=raw.get("date", date),
        session=session,
        trading_mode=raw.get("trading_mode", "unknown"),
        n_trades=len(trades),
        n_wins=len(wins),
        n_losses=len(losses),
        win_rate=_safe_div(len(wins), len(trades)),
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        net_pnl=net_pnl,
        profit_factor=_safe_div(gross_profit, gross_loss) if gross_loss else float("inf") if gross_profit else 0.0,
        avg_win=_safe_div(gross_profit, len(wins)),
        avg_loss=_safe_div(-gross_loss, len(losses)),
        avg_mfe=_safe_div(sum(t.get("max_favorable", 0) for t in trades), len(trades)),
        avg_mae=_safe_div(sum(t.get("max_adverse", 0) for t in trades), len(trades)),
        avg_bars_held=_safe_div(sum(t.get("bars_held", 0) for t in trades), len(trades)),
        sides=sides,
        trades=trades,
        signals=signals,
        risk_state=risk_state,
    )


# ---------- 週度聚合 ----------


@dataclass
class DayRollup:
    """一個交易日（含日盤 + 夜盤）合併後的精簡視圖。"""

    date: str
    weekday: str  # Mon..Fri
    n_trades: int
    net_pnl: float
    sessions_available: list[Session]  # ["day"] / ["night"] / ["day","night"]


@dataclass
class WeeklyStats:
    week_start: str  # Monday (YYYY-MM-DD)
    week_end: str    # Friday (YYYY-MM-DD)
    n_trading_days: int               # 有資料的日數
    n_days_with_trades: int           # 有實際交易的日數
    n_trades: int
    n_wins: int
    n_losses: int
    win_rate: float
    gross_profit: float
    gross_loss: float
    net_pnl: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    avg_mfe: float
    avg_mae: float
    avg_bars_held: float
    sides: dict[str, int]
    best_day: dict | None             # {"date":"...", "net_pnl": ...}
    worst_day: dict | None
    max_consec_losing_days: int
    daily_rollup: list[DayRollup] = field(default_factory=list)
    sessions: list[dict] = field(default_factory=list)  # 每個 (date, session) 的 DailyStats（compact）

    def to_dict(self) -> dict:
        return asdict(self)


def _week_bounds(week_ending: str) -> tuple[date_cls, date_cls]:
    """給定週內任一天，回傳該週的週一與週五。"""
    d = datetime.strptime(week_ending, "%Y-%m-%d").date()
    monday = d - timedelta(days=d.weekday())  # weekday(): Mon=0..Sun=6
    friday = monday + timedelta(days=4)
    return monday, friday


def load_week(week_ending: str) -> WeeklyStats:
    """讀某週 Mon-Fri 的日盤 + 夜盤資料，聚合為週度統計。

    week_ending 可以是該週任一天的 'YYYY-MM-DD'，會自動對齊到該週的週一週五。
    缺檔的日子會被跳過（不算 trading day），不視為錯誤。
    """
    monday, friday = _week_bounds(week_ending)

    sessions_data: list[DailyStats] = []
    rollups: list[DayRollup] = []

    cur = monday
    while cur <= friday:
        date_s = cur.isoformat()
        weekday = cur.strftime("%a")
        day_pnl = 0.0
        day_trades = 0
        sessions_avail: list[Session] = []

        for sess in ("day", "night"):
            try:
                ds = load_daily(date_s, sess)  # type: ignore[arg-type]
            except FileNotFoundError:
                continue
            sessions_data.append(ds)
            sessions_avail.append(sess)  # type: ignore[arg-type]
            day_pnl += ds.net_pnl
            day_trades += ds.n_trades

        if sessions_avail:
            rollups.append(
                DayRollup(
                    date=date_s,
                    weekday=weekday,
                    n_trades=day_trades,
                    net_pnl=day_pnl,
                    sessions_available=sessions_avail,
                )
            )
        cur += timedelta(days=1)

    # 跨 session 聚合所有 trades
    all_trades: list[dict] = []
    for ds in sessions_data:
        all_trades.extend(ds.trades)

    wins = [t for t in all_trades if (t.get("net_pnl") or t.get("pnl") or 0) > 0]
    losses = [t for t in all_trades if (t.get("net_pnl") or t.get("pnl") or 0) < 0]
    gross_profit = sum((t.get("net_pnl") or t.get("pnl") or 0) for t in wins)
    gross_loss = abs(sum((t.get("net_pnl") or t.get("pnl") or 0) for t in losses))
    net_pnl = sum((t.get("net_pnl") or t.get("pnl") or 0) for t in all_trades)

    sides: dict[str, int] = {}
    for t in all_trades:
        sides[t.get("side", "?")] = sides.get(t.get("side", "?"), 0) + 1

    days_with_trades = [r for r in rollups if r.n_trades > 0]
    best = max(rollups, key=lambda r: r.net_pnl) if rollups else None
    worst = min(rollups, key=lambda r: r.net_pnl) if rollups else None

    # 連續虧損日（只計有交易的日子）
    max_streak = streak = 0
    for r in rollups:
        if r.n_trades == 0:
            continue
        if r.net_pnl < 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0

    return WeeklyStats(
        week_start=monday.isoformat(),
        week_end=friday.isoformat(),
        n_trading_days=len(rollups),
        n_days_with_trades=len(days_with_trades),
        n_trades=len(all_trades),
        n_wins=len(wins),
        n_losses=len(losses),
        win_rate=_safe_div(len(wins), len(all_trades)),
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        net_pnl=net_pnl,
        profit_factor=(
            _safe_div(gross_profit, gross_loss)
            if gross_loss
            else float("inf") if gross_profit else 0.0
        ),
        avg_win=_safe_div(gross_profit, len(wins)),
        avg_loss=_safe_div(-gross_loss, len(losses)),
        avg_mfe=_safe_div(sum(t.get("max_favorable", 0) for t in all_trades), len(all_trades)),
        avg_mae=_safe_div(sum(t.get("max_adverse", 0) for t in all_trades), len(all_trades)),
        avg_bars_held=_safe_div(sum(t.get("bars_held", 0) for t in all_trades), len(all_trades)),
        sides=sides,
        best_day={"date": best.date, "net_pnl": best.net_pnl} if best else None,
        worst_day={"date": worst.date, "net_pnl": worst.net_pnl} if worst else None,
        max_consec_losing_days=max_streak,
        daily_rollup=rollups,
        sessions=[
            {
                "date": ds.date,
                "session": ds.session,
                "n_trades": ds.n_trades,
                "net_pnl": ds.net_pnl,
                "win_rate": ds.win_rate,
            }
            for ds in sessions_data
        ],
    )
