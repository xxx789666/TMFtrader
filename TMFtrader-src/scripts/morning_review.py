"""早盤覆盤 — 夜盤收盤後(cron Tue-Sat 06:00 TST)推 TG。
讀 3 支 live(owner-scoped data/live/<owner>/)+ aft_orb paper(data/paper/aft_orb/)的
performance daily JSON(昨天+今天、夜盤跨午夜),彙整每支:筆數/淨損益/勝率/最近成交/出窗旗標。
另讀 CSV-tape 型 paper 策略(chips_combo / maxpain_v2,HTTP 資料、自己的 cron):累積
PF/勝率 + 昨/今新成交 + 當前狀態(next_signal.json)。純讀取 + 推 TG。"""
import os, sys, json, csv
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
           ("chips_exec", "data/paper/chips_exec", "paper")]   # 2026-06-09 取代 aft_orb:chips 真 tick paper
today = datetime.now().strftime("%Y-%m-%d")
yday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

# 各策略的「合法進場時段」(時:分),用於出窗檢查
WIN = {"night_v3": ((15, 0), (5, 0)), "chips_exec": ((8, 45), (13, 45)),
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

# ── CSV-tape 型 paper 策略(HTTP 資料、不過 engine、自己的 cron)──
def _fnum(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None

def csv_section(name, tape, status, date_col):
    """回 lines:累積 PF/勝率 + 昨/今新成交 + 當前狀態。"""
    rows = []
    if os.path.exists(tape):
        try:
            with open(tape, encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        except Exception:
            rows = []
    if not rows and not os.path.exists(status):
        return [f"\n[{name}/paper-csv] 無資料"]
    out = []
    pnls = [v for v in (_fnum(r.get("pnl")) for r in rows) if v is not None]
    if pnls:
        n = len(pnls); wins = [x for x in pnls if x > 0]
        gp = sum(wins); gl = -sum(x for x in pnls if x < 0)
        pf = (gp / gl) if gl > 0 else float("inf")
        out.append(f"\n[{name}/paper-csv] 累積{n}筆 淨{sum(pnls):+.0f}元 勝{len(wins)}/{n} PF{pf:.2f}")
    else:
        out.append(f"\n[{name}/paper-csv] 累積0筆")
    fresh = [r for r in rows if str(r.get(date_col, ""))[:10] in (yday, today)]
    for r in fresh[-3:]:
        out.append(f"  新成交 {str(r.get(date_col,''))[:10]} {r.get('side','long')} "
                   f"{_fnum(r.get('pnl')) or 0:+.0f} {str(r.get('exit_reason',''))[:16]}")
    if not fresh and rows:
        r = rows[-1]
        out.append(f"  (昨/今無新成交;最近 {str(r.get(date_col,''))[:10]} "
                   f"{r.get('side','long')} {_fnum(r.get('pnl')) or 0:+.0f})")
    if os.path.exists(status):
        try:
            st = json.load(open(status, encoding="utf-8"))
            out.append(f"  狀態: {fmt_status(st)}")
        except Exception:
            pass
    return out

def fmt_status(st):
    s = st.get("state")
    if s == "open":      # maxpain 持倉中
        return f"持倉中 多 S1={st.get('S1')} 停={st.get('stop_at')} 抱到{st.get('ed')}結算"
    if s == "signal_fired":
        return f"訊號出 dist{st.get('dist',0):+.3f}、明開盤進多第1口(目標{st.get('ed')})"
    if s == "flat":
        return "無持倉(等下個訊號日)"
    if "side" in st:     # chips_combo 待進場
        side = st.get("side") or "flat"
        return f"待進 {st.get('trade_date','')} {side} combo{st.get('combo',0):+.2f}"
    return json.dumps(st, ensure_ascii=False)[:60]

lines = [f"📋 [覆盤] {today} 早 — live + chips_exec paper(真tick) + CSV(chips-OHLC參考/maxpain)"]
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

lines += csv_section("chips_combo", "data/chips_combo/decisions.csv",
                     "data/chips_combo/next_signal.json", "trade_date")
lines += csv_section("maxpain_v2", "data/maxpain_v2/decisions.csv",
                     "data/maxpain_v2/next_signal.json", "exit_date")

msg = "\n".join(lines)
print(msg)
tg(msg)
