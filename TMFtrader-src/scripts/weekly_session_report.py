"""每週 live 實單「日盤 vs 夜盤」績效報表 —— 帳戶級(與券商對帳一致)。

資料源:Shioaji `list_profit_loss_detail`(逐筆),帳戶淨額 = pnl - tax - fee。
  - 短登入 fetch_contract=False(~1MB、不影響策略),量極小。
日/夜分類:帳戶損益無盤中時間 → 用 data/trade_sessions.csv(由 daily_capture_sessions.py
  趁 log 新時擷取的「日期+方向+進場價→session」)以 entry_price 對應。
持久化:data/session_edge_account.csv(累積去重)+ data/session_edge_account.json(給 Hermes 週度覆盤)。
TG 推播。

cron(每週日 00:00 UTC = 週日 08:00 TST):
  0 0 * * 0 cd /home/xx/TMFtrader-src && .venv/bin/python scripts/weekly_session_report.py >> data/logs/weekly_session.log 2>&1
"""
import os, sys, csv, json
from pathlib import Path
from datetime import datetime, timedelta

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

CUM_CSV = ROOT / "data" / "session_edge_account.csv"
OUT_JSON = ROOT / "data" / "session_edge_account.json"
SESS_CSV = ROOT / "data" / "trade_sessions.csv"
LOOKBACK_DAYS = 30


def load_sessions() -> list[dict]:
    if not SESS_CSV.exists():
        return []
    with open(SESS_CSV, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def classify(date: str, side: str, entry_price: float, sessions: list[dict]) -> str:
    """以 entry_price(±2)+方向+日期窗(±3天)對應 trade_sessions 的 session。"""
    d = datetime.strptime(date, "%Y%m%d").date()
    best, bestgap = None, 99
    for s in sessions:
        if s["side"].upper() != side.upper():
            continue
        try:
            sd = datetime.strptime(s["date"], "%Y%m%d").date()
            if not (timedelta(0) <= (d - sd) <= timedelta(days=3)):
                continue
            if abs(float(s["entry_price"]) - entry_price) <= 2.0:
                gap = (d - sd).days
                if gap < bestgap:
                    best, bestgap = s["session"], gap
        except Exception:
            continue
    if best:
        return best
    return "night" if d.weekday() == 0 else "unknown"  # 週一入帳多為上週五夜盤


def fetch_detail(days: int = LOOKBACK_DAYS) -> list[dict]:
    import shioaji as sj
    api = sj.Shioaji(simulation=False)
    api.login(api_key=os.environ["SHIOAJI_API_KEY"], secret_key=os.environ["SHIOAJI_SECRET_KEY"],
              receive_window=300000, fetch_contract=False)
    try:
        acct = api.futopt_account
        today = datetime.now().date()
        begin = (today - timedelta(days=days)).isoformat()
        pnls = api.list_profit_loss(acct, begin, today.isoformat())  # 需先呼叫以填 cache
        recs = []
        for p in pnls:
            pid = getattr(p, "id", None)
            try:
                dets = api.list_profit_loss_detail(acct, pid)
            except Exception:
                dets = []
            for dd in dets:
                d = dd.__dict__ if hasattr(dd, "__dict__") else dict(dd)
                direction = d.get("direction")
                direction = direction.value if hasattr(direction, "value") else str(direction)
                gross = float(d.get("pnl", 0)); fee = float(d.get("fee", 0)); tax = float(d.get("tax", 0))
                recs.append({
                    "date": str(d.get("date", "")), "code": d.get("code", ""),
                    "quantity": int(d.get("quantity", 0)), "direction": direction,
                    "entry_price": float(d.get("entry_price", 0)), "cover_price": float(d.get("cover_price", 0)),
                    "gross": gross, "fee": fee, "tax": tax, "net": gross - fee - tax,
                })
        return recs
    finally:
        api.logout()


def merge_cumulative(recs: list[dict]) -> list[dict]:
    cum = {}
    if CUM_CSV.exists():
        with open(CUM_CSV, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                cum[(r["date"], r["direction"], r["entry_price"], r["net"])] = r
    for r in recs:
        cum[(r["date"], r["direction"], str(r["entry_price"]), str(r["net"]))] = {k: str(v) for k, v in r.items()}
    rows = sorted(cum.values(), key=lambda r: r["date"])
    CUM_CSV.parent.mkdir(parents=True, exist_ok=True)
    with open(CUM_CSV, "w", newline="", encoding="utf-8") as f:
        fn = ["date", "code", "quantity", "direction", "entry_price", "cover_price", "gross", "fee", "tax", "net", "session"]
        w = csv.DictWriter(f, fieldnames=fn); w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fn})
    return rows


def _stats(rows):
    nets = [float(r["net"]) for r in rows]
    n = len(nets)
    if n == 0:
        return "0 筆"
    wins = [x for x in nets if x > 0]
    gp, gl = sum(wins), abs(sum(x for x in nets if x <= 0)) or 1
    return f"{n} 筆 | WR {len(wins)/n*100:.0f}% | PF {gp/gl:.2f} | 淨 {sum(nets):+,.0f}"


def main():
    sessions = load_sessions()
    recs = fetch_detail()
    for r in recs:
        r["session"] = classify(r["date"], r["direction"], r["entry_price"], sessions)
    allrows = merge_cumulative(recs)
    for r in allrows:
        if not r.get("session") or r["session"] == "unknown":
            r["session"] = classify(r["date"], r["direction"], float(r["entry_price"] or 0), sessions)

    day = [r for r in allrows if r["session"] == "day"]
    night = [r for r in allrows if r["session"] == "night"]
    unk = [r for r in allrows if r["session"] not in ("day", "night")]

    OUT_JSON.write_text(json.dumps({
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "day": _stats(day), "night": _stats(night),
        "total_net": sum(float(r["net"]) for r in allrows), "n": len(allrows),
        "records": allrows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "[週報] BreakoutTrend 日盤 vs 夜盤(帳戶實際對帳,net=毛-稅-費)",
        f"  期間: {allrows[0]['date'] if allrows else '-'} ~ {allrows[-1]['date'] if allrows else '-'}  (逐筆 {len(allrows)})",
        f"  日盤: {_stats(day)}",
        f"  夜盤: {_stats(night)}",
    ]
    if unk:
        lines.append(f"  未分類: {_stats(unk)}(待 daily_capture 補 session)")
    lines.append(f"  全部: {_stats(allrows)}")
    msg = "\n".join(lines)
    print(msg)
    try:
        from core.notify import tg
        tg(msg)
    except Exception as e:
        print(f"(TG 未送出: {e})")


if __name__ == "__main__":
    main()
