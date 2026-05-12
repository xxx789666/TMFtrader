"""Hermes Agent 失控防禦 —— 外部監控 + 緊急斷路。

設計目的：即使 Hermes 自己（skill 內部）沒能停下來，這支腳本也會在週度 cron 完成
後幾分鐘執行一次，掃 Hermes 的 output 與 jobs 紀錄，看是否有 runaway 跡象：
    - 單次任務工具呼叫數 > MAX_TOOL_CALLS
    - 單次任務耗時 > MAX_MINUTES
    - 單次任務 token 用量 > MAX_TOKENS
    - 最近 1 小時內請求數 > BURST_LIMIT

任一觸發即：
    1) 寫 ~/.hermes/kill_switch 阻斷下一次 cron 執行（skill 第 0 步會讀）
    2) 推 Telegram 警告（沿用 .env 內 TG_BOT_TOKEN + TG_CHAT_ID）
    3) 在 stdout 印 JSON 摘要、exit code != 0 讓系統 cron 留下異常記錄

系統 cron（不是 Hermes cron）排：
    20 1 * * 6  python3 -m review.runaway_guard --hermes-home=~/.hermes \\
                       --job-name=tmf-weekly-review

# 注意：實際的 Hermes output / jobs.json 格式要等 Phase 3 安裝完才能 100% 確認。
# 本檔提供 skeleton + 寬鬆的 fallback 解析，先能跑、跑完再對齊真實格式。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable
from urllib import parse as urlparse
from urllib import request as urlreq

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

# ----- 預算門檻（可由環境變數覆寫，方便調參而不動 code）-----
MAX_TOOL_CALLS = int(os.environ.get("GUARD_MAX_TOOL_CALLS", "12"))
MAX_MINUTES = int(os.environ.get("GUARD_MAX_MINUTES", "10"))
MAX_TOKENS = int(os.environ.get("GUARD_MAX_TOKENS", "50000"))
BURST_LIMIT_1H = int(os.environ.get("GUARD_BURST_LIMIT_1H", "30"))


# ---------- 工具 ----------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _send_telegram(text: str) -> bool:
    """沿用既有 EA 的 TG 設定（TG_BOT_TOKEN + TG_CHAT_ID）。"""
    token = os.environ.get("TG_BOT_TOKEN") or os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TG_CHAT_ID") or os.environ.get("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return False
    body = urlparse.urlencode(
        {"chat_id": chat, "text": text, "parse_mode": "Markdown"}
    ).encode()
    try:
        with urlreq.urlopen(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=body,
            timeout=10,
        ) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def _write_kill_switch(home: Path, reason: str) -> Path:
    f = home / "kill_switch"
    f.write_text(f"{_now_iso()} {reason}\n", encoding="utf-8")
    return f


# ---------- 解析 ----------


def _latest_output_file(home: Path, job_name: str) -> Path | None:
    """找最近一次 cron output。Hermes 預設放 ~/.hermes/cron/output/<job_id>/<ts>.md。
    job_id 不確定（可能是 hash），所以我們找所有子資料夾中最新的 *.md。
    """
    base = home / "cron" / "output"
    if not base.exists():
        return None
    candidates = sorted(base.rglob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not candidates:
        return None
    # 若 job_name 出現在路徑或檔頭，優先取對應的
    for p in candidates:
        if job_name in str(p):
            return p
        try:
            head = p.read_text(encoding="utf-8", errors="ignore")[:2000]
            if job_name in head:
                return p
        except OSError:
            continue
    return candidates[0]  # fallback 最新一支


# 寬鬆計數 —— 不假設 Hermes 用哪種精確格式
_TOOL_CALL_PATTERNS = [
    r"\btool_call\b",
    r"\bexecute_code\b",
    r"\bload_daily\b",
    r"\bload_week\b",
    r"^\s*▶\s*tool",
    r"calling tool:",
]

_TOKEN_PATTERNS = [
    r"total_tokens[:=]\s*(\d+)",
    r"tokens_total[:=]\s*(\d+)",
    r"usage.*?total[^\d]+(\d+)",
]

_DURATION_PATTERNS = [
    r"duration[:=]\s*(\d+(?:\.\d+)?)\s*(ms|s|m|min|sec)",
    r"elapsed[:=]\s*(\d+(?:\.\d+)?)\s*(ms|s|m|min|sec)",
]


def _count_matches(text: str, patterns: Iterable[str]) -> int:
    n = 0
    for pat in patterns:
        n += len(re.findall(pat, text, flags=re.IGNORECASE | re.MULTILINE))
    return n


def _extract_int_max(text: str, patterns: Iterable[str]) -> int:
    vals: list[int] = []
    for pat in patterns:
        for m in re.finditer(pat, text, flags=re.IGNORECASE | re.DOTALL):
            try:
                vals.append(int(m.group(1)))
            except (ValueError, IndexError):
                pass
    return max(vals) if vals else 0


def _extract_minutes_max(text: str, patterns: Iterable[str]) -> float:
    vals: list[float] = []
    for pat in patterns:
        for m in re.finditer(pat, text, flags=re.IGNORECASE):
            try:
                val = float(m.group(1))
                unit = m.group(2).lower()
                if unit == "ms":
                    val /= 60_000
                elif unit in ("s", "sec"):
                    val /= 60
                elif unit in ("m", "min"):
                    pass
                vals.append(val)
            except (ValueError, IndexError):
                pass
    return max(vals) if vals else 0.0


def _count_recent_outputs(home: Path, hours: int) -> int:
    base = home / "cron" / "output"
    if not base.exists():
        return 0
    cutoff = datetime.now().timestamp() - hours * 3600
    return sum(1 for p in base.rglob("*.md") if p.stat().st_mtime >= cutoff)


# ---------- 主流程 ----------


def analyze(home: Path, job_name: str) -> dict:
    out_file = _latest_output_file(home, job_name)
    if out_file is None:
        return {
            "ok": True,
            "reason": "no_output_yet",
            "tool_calls": 0,
            "tokens": 0,
            "minutes": 0,
            "burst_1h": 0,
        }
    text = out_file.read_text(encoding="utf-8", errors="ignore")
    return {
        "ok": True,
        "file": str(out_file),
        "tool_calls": _count_matches(text, _TOOL_CALL_PATTERNS),
        "tokens": _extract_int_max(text, _TOKEN_PATTERNS),
        "minutes": _extract_minutes_max(text, _DURATION_PATTERNS),
        "burst_1h": _count_recent_outputs(home, hours=1),
    }


def evaluate(metrics: dict) -> list[str]:
    """回傳所有違反的規則描述（空 list = 全部 OK）。"""
    breaches: list[str] = []
    if metrics["tool_calls"] > MAX_TOOL_CALLS:
        breaches.append(f"tool_calls={metrics['tool_calls']} > {MAX_TOOL_CALLS}")
    if metrics["tokens"] > MAX_TOKENS:
        breaches.append(f"tokens={metrics['tokens']} > {MAX_TOKENS}")
    if metrics["minutes"] > MAX_MINUTES:
        breaches.append(f"minutes={metrics['minutes']:.1f} > {MAX_MINUTES}")
    if metrics["burst_1h"] > BURST_LIMIT_1H:
        breaches.append(f"burst_1h={metrics['burst_1h']} > {BURST_LIMIT_1H}")
    return breaches


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Hermes runaway guard")
    p.add_argument(
        "--hermes-home",
        default=os.path.expanduser("~/.hermes"),
        help="Hermes data root（預設 ~/.hermes）",
    )
    p.add_argument("--job-name", default="tmf-weekly-review")
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="只分析、不寫 kill_switch、不推 TG",
    )
    args = p.parse_args(argv)

    home = Path(args.hermes_home).expanduser()
    if not home.exists():
        print(json.dumps({"status": "skip", "reason": "hermes_home_missing", "path": str(home)}))
        return 0

    metrics = analyze(home, args.job_name)
    breaches = evaluate(metrics)
    result = {
        "status": "ok" if not breaches else "trip",
        "checked_at": _now_iso(),
        "metrics": metrics,
        "breaches": breaches,
        "thresholds": {
            "MAX_TOOL_CALLS": MAX_TOOL_CALLS,
            "MAX_MINUTES": MAX_MINUTES,
            "MAX_TOKENS": MAX_TOKENS,
            "BURST_LIMIT_1H": BURST_LIMIT_1H,
        },
    }

    if breaches and not args.dry_run:
        ks = _write_kill_switch(home, "; ".join(breaches))
        result["kill_switch"] = str(ks)
        msg = (
            f"🛑 *TMF Hermes runaway 偵測*\n"
            f"job: `{args.job_name}`\n"
            f"violations:\n"
            + "\n".join(f"• {b}" for b in breaches)
            + f"\nkill_switch 已寫入：`{ks}`\n"
            f"清除：`rm ~/.hermes/kill_switch`"
        )
        result["telegram_sent"] = _send_telegram(msg)

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not breaches else 3


if __name__ == "__main__":
    sys.exit(main())
