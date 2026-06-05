"""早盤覆盤 — 夜盤收盤後(cron Tue-Sat 06:00 TST)推 TG。
讀 3 支 live(owner-scoped data/live/<owner>/)+ aft_orb paper(data/paper/aft_orb/)的
performance daily JSON(昨天+今天、夜盤跨午夜),彙整每支:筆數/淨損益/勝率/最近成交/出窗旗標。
純讀取 + 推 TG。"""
import os, sys, json
from datetime import datetime, timedelta
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT); sys.path.insert(0, ROOT)
try:
    from core.notify import tg
except Exception:
    def tg(m): pass

TARGETS = [("breakout_v7", "data/live/breakout_v7", "live"),
           ("day_orb", "data/live/day_orb", "live"),
           ("night_v3", "data/live/night_v3", "live"),
           ("aft_orb", "data/paper/aft_orb", "paper")]
today = datetime.now().strftime("%Y-%m-%d")
yday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

# 各策略的「合法進場時段」(時:分),用於出窗檢查
WIN = {"night_v3": ((15, 0), (5, 0)), "aft_orb": ((15, 0), (23, 30)),
       "breakout_v7": None, "day_orb": ((8, 45), (13, 45))}

def in_window(hhmm_str, win):
    if not win or not hhmm_str or len(hhmm_str) < 16:
        return True
    try:
        t = hhmm_str[11:16]; h, m = int(t[:2]), int(t[3:5]); cur = h * 60 + m
    except Exception:
        return True
    (a, b), (c, d) = win[0], win[1]
    lo, hi = a * 60 + b, c * 60 + d
    return (cur >= lo or cur <= hi) if lo > hi else (lo <= cur <= hi)

def load(base):
    trades = []
    for dte in (yday, today):
        for suf in ("", "_live"):
            p = f"{base}/performance/daily/{dte}{suf}.json"
            if os.path.exists(p):
                try:
                    for t in json.load(open(p, encoding="utf-8")).get("trades", []):
                        if t not in trades:
                            trades.append(t)
                except Exception:
                    pass
    return trades

lines = [f"📋 [覆盤] {today} 早 — 昨夜 live x3 + aft_orb paper"]
for name, base, mode in TARGETS:
    trades = load(base)
    if not trades:
        lines.append(f"\n[{name}/{mode}] 無成交")
        continue
    pnl = sum(t.get("net_pnl", t.get("pnl", 0)) for t in trades)
    wins = sum(1 for t in trades if t.get("net_pnl", t.get("pnl", 0)) > 0)
    lines.append(f"\n[{name}/{mode}] {len(trades)}筆 淨{pnl:+.0f}元 勝{wins}/{len(trades)}")
    for t in trades[-3:]:
        et = t.get("entry_time", "")[11:16]; xt = t.get("exit_time", "")[11:16]
        lines.append(f"  {et}→{xt} {t.get('side','')} {t.get('net_pnl', t.get('pnl',0)):+.0f} {str(t.get('reason',''))[:20]}")
    bad = [t for t in trades if not in_window(t.get("entry_time", ""), WIN.get(name))]
    if bad:
        lines.append(f"  ⚠️ {len(bad)} 筆疑「出窗進場」(進場時間不在 {name} 交易時段)")

msg = "\n".join(lines)
print(msg)
tg(msg)
