"""每日擷取當天交易的 session(日盤/夜盤)—— 趁 log 還沒輪替時落地。

解決:Shioaji 帳戶損益無盤中時間 + log 會輪替刪除 → 每天把當天每筆進場的
「日期 / 方向 / 進場價 / session」存進持久檔 data/trade_sessions.csv。
週報(weekly_session_report.py)再用帳戶逐筆 detail 的 entry_price 對應回 session。

進場來源:主 log 的 "[FILL] 成交: BUY/SELL N口 @ price"(live)+ "[PAPER] [TMF] BUY/SELL"(paper)。
cron(每日 06:10 + 21:20 UTC,日盤收後 / 夜盤收後各一次):
  10 6 * * 1-5  cd /home/xx/TMFtrader-src && .venv/bin/python scripts/daily_capture_sessions.py >> data/logs/session_capture.log 2>&1
  20 21 * * 0-4 cd /home/xx/TMFtrader-src && .venv/bin/python scripts/daily_capture_sessions.py >> data/logs/session_capture.log 2>&1
"""
import re, gzip, csv
from pathlib import Path
from datetime import datetime, time as dtime

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "data" / "logs"
SESS_CSV = ROOT / "data" / "trade_sessions.csv"
MAX_LOG_BYTES = 100 * 1024 * 1024

ANSI = re.compile(r"\x1b\[[0-9;]*m")
TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
RE_ENTRY = re.compile(r"\[FILL\] 成交: (BUY|SELL)\b.*?@ ([\d.]+)|\[PAPER\] \[TMF\] (BUY|SELL)\b.*?@ ([\d.]+)")
DAY_START, DAY_END = dtime(8, 45), dtime(13, 45)


def _session_of(dt: datetime) -> str:
    return "day" if DAY_START <= dt.time() < DAY_END else "night"


def parse_entries() -> list[dict]:
    files = sorted(list(LOG_DIR.glob("*trader_2026*.log")) + list(LOG_DIR.glob("*trader_2026*.log.gz")))
    out = []
    for fp in files:
        try:
            if fp.stat().st_size > MAX_LOG_BYTES:
                continue
            op = gzip.open if fp.suffix == ".gz" else open
            with op(fp, "rt", encoding="utf-8", errors="ignore") as f:
                for ln in f:
                    ln = ANSI.sub("", ln)
                    m = TS.match(ln)
                    if not m or "CLOSE" in ln or "平倉" in ln:
                        continue
                    me = RE_ENTRY.search(ln)
                    if not me:
                        continue
                    side = (me.group(1) or me.group(3) or "").upper()
                    price = me.group(2) or me.group(4)
                    if not (side and price):
                        continue
                    dt = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                    out.append({"date": dt.strftime("%Y%m%d"), "time": dt.strftime("%H:%M:%S"),
                                "side": side, "entry_price": f"{float(price):.1f}",
                                "session": _session_of(dt)})
        except Exception:
            continue
    return out


def main():
    entries = parse_entries()
    existing = {}
    if SESS_CSV.exists():
        with open(SESS_CSV, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                existing[(r["date"], r["side"], r["entry_price"])] = r
    added = 0
    for e in entries:
        k = (e["date"], e["side"], e["entry_price"])
        if k not in existing:
            existing[k] = e; added += 1
    rows = sorted(existing.values(), key=lambda r: (r["date"], r.get("time", "")))
    SESS_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(SESS_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "time", "side", "entry_price", "session"])
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in w.fieldnames})
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] trade_sessions: +{added} 新筆 / 共 {len(rows)} 筆")


if __name__ == "__main__":
    main()
