"""
log_quota.py — 永豐 Shioaji API 用量自動監測

每次跑會：
1. 短暫 login（fetch_contract=False、不抓 contracts、最省）
2. 呼叫 api.usage() 取得即時流量數字
3. logout
4. 寫一行到 data/quota_history.csv
5. 若 remaining_bytes < 100 MB、推 TG 警示
6. stdout 印一行摘要

預期成本：每次跑 ~1 MB（純 login session）、不會影響策略運作。

用法：
  python3 scripts/log_quota.py            # 標準模式（只警示）
  python3 scripts/log_quota.py --verbose  # 每次都推 TG（debug 用）

cron 安排（4 次/日）：
  5  1  * * 1-5  09:05 TST 開盤後 baseline
  0  6  * * 1-5  14:00 TST 日盤收盤後、夜盤前
  0  14 * * 1-5  22:00 TST 夜盤開盤 + ORB session 啟動
  30 20 * * 0-4  04:30 TST 強平前最後快照
"""

import csv
import os
import sys
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")

LOG_CSV = ROOT / "data" / "quota_history.csv"
WARN_THRESHOLD_MB = 100  # 剩餘 < 100 MB 推 TG


def tg_send(msg: str) -> None:
    """推 TG、失敗靜默。"""
    import json
    import urllib.request

    token = os.environ.get("TG_BOT_TOKEN", "")
    chat = os.environ.get("TG_CHAT_ID", "")
    if not token or not chat:
        return
    try:
        req = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=json.dumps({"chat_id": chat, "text": msg}).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=6)
    except Exception:
        pass


def main() -> int:
    verbose = "--verbose" in sys.argv

    import shioaji as sj
    api = sj.Shioaji(simulation=False)
    api.login(
        api_key=os.environ["SHIOAJI_API_KEY"],
        secret_key=os.environ["SHIOAJI_SECRET_KEY"],
        receive_window=300000,
        fetch_contract=False,
    )
    u = api.usage()
    api.logout()

    now = datetime.now()
    bytes_mb = round(u.bytes / 1024 / 1024, 1)
    limit_mb = round(u.limit_bytes / 1024 / 1024)
    remaining_mb = round(u.remaining_bytes / 1024 / 1024, 1)

    row = {
        "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
        "bytes": u.bytes,
        "limit_bytes": u.limit_bytes,
        "remaining_bytes": u.remaining_bytes,
        "connections": u.connections,
        "bytes_mb": bytes_mb,
        "limit_mb": limit_mb,
        "remaining_mb": remaining_mb,
    }

    LOG_CSV.parent.mkdir(parents=True, exist_ok=True)
    new_file = not LOG_CSV.exists()
    with LOG_CSV.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=row.keys())
        if new_file:
            w.writeheader()
        w.writerow(row)

    summary = (
        f"[{row['timestamp']}] "
        f"bytes={bytes_mb}MB / {limit_mb}MB "
        f"remaining={remaining_mb}MB "
        f"connections={u.connections}"
    )
    print(summary)

    # 警示推 TG（剩餘不足）
    warn = u.remaining_bytes < WARN_THRESHOLD_MB * 1024 * 1024
    if warn:
        emoji = "🚨" if remaining_mb >= 0 else "🔥"
        tg_send(
            f"{emoji} [Quota] 剩餘 {remaining_mb} MB < {WARN_THRESHOLD_MB} MB\n"
            f"已用 {bytes_mb} / {limit_mb} MB\n"
            f"連線 {u.connections}\n"
            f"時間 {now.strftime('%H:%M:%S')}"
        )
    elif verbose:
        tg_send(
            f"📊 [Quota] {bytes_mb} / {limit_mb} MB（剩 {remaining_mb} MB）\n"
            f"連線 {u.connections}\n"
            f"時間 {now.strftime('%H:%M:%S')}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
