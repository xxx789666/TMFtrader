"""chips_combo_v1 — 每日盤後 PAPER runner(自包含、HTTP 資料、不吃 shioaji)。

規格:deployed_strategies/chips_combo/HANDOFF_chips_combo_v1.md(2026-06-08)。
籌碼日訊號:外資 flow(FinMind)+ 大戶 all_ratio(TAIFEX)→ 各取 60 日因果 z → 等權 combo。
combo>+0.5 LONG / <−0.5 SHORT / else FLAT;**T 日盤後算、T+1 交易**(絕不偷看)。
T+1 開盤進、收盤出、−2% 盤中停損、不過夜、固定 1 口微台、無止盈。

⚠️ 時序鐵律:用 date<=T 的資料算 combo[T] → 決定 T+1 的 side。結算 paper 單時,
   「今天」的 side 來自「昨天收盤後算的 combo」,用「今天」的 OHLC 結算(完全不偷看)。

用法:
  python scripts/chips_combo_daily.py                 # 每日模式(cron ~15:40):抓近期→算→結算今天→寫明天 signal
  python scripts/chips_combo_daily.py --backfill 2024-01-01 2026-06-06   # 種子+回放整段(建歷史+補記 paper 單)

口徑(對照 HANDOFF §5):PF~1.2 / RR1.17 / WR53.6% / ~10.7筆月。固定 1 口、point_value 10、−2% 停損。
"""
import argparse
import csv
import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DDIR = ROOT / "data" / "chips_combo"
HIST = DDIR / "history.csv"          # date,net_OI,flow,all_ratio,open,high,low,close
SIGNAL = DDIR / "next_signal.json"   # {"trade_date","side","combo","z_flow","z_lt"}
TAPE = DDIR / "decisions.csv"        # 決策帶 + paper 單(HANDOFF §6)

Z_WIN = 60
THR = 0.5
POINT_VALUE = 50.0   # 小台 MXF(2026-06-09 由微台 pv10 改小台;價格仍用大台 TX 軌跡、口徑×5)
LOTS = 1
STOP_PCT = 0.02

FINMIND = "https://api.finmindtrade.com/api/v4/data"
TAIFEX_LT = "https://www.taifex.com.tw/cht/3/largeTraderFutDown"
TAIFEX_FUT = "https://www.taifex.com.tw/cht/3/futContractsDateDown"   # 三大法人-區分各期貨契約
FINMIND_TOKEN = os.getenv("FINMIND_TOKEN", "").strip()


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def fetch_finmind(dataset, data_id, start, end):
    q = {"dataset": dataset, "data_id": data_id, "start_date": start, "end_date": end}
    if FINMIND_TOKEN:
        q["token"] = FINMIND_TOKEN
    raw = _get(f"{FINMIND}?{urllib.parse.urlencode(q)}")
    j = json.loads(raw.decode("utf-8"))
    if j.get("status") != 200 and "data" not in j:
        raise RuntimeError(f"FinMind {dataset}: {j.get('msg', j)}")
    return j.get("data", [])


def _foreign_netoi_finmind(start, end):
    """外資 net_OI 走 FinMind TaiwanFuturesInstitutionalInvestors(全史 2020+)。
    歷史回測用(TAIFEX 官網 CSV 端點只 serve 近期、老資料回 HTML 錯誤頁)。與官網同一個數。"""
    rows = fetch_finmind("TaiwanFuturesInstitutionalInvestors", "TX", start, end)
    out = {}
    for r in rows:
        if str(r.get("institutional_investors", "")).strip() != "外資":
            continue
        out[r["date"]] = float(r.get("long_open_interest_balance_volume", 0)) - \
            float(r.get("short_open_interest_balance_volume", 0))
    return out


def fetch_foreign_netoi(start, end):
    """三大法人期貨「外資及陸資」TX net_OI[date] = 多方未平倉 − 空方未平倉。
    forward 預設:TAIFEX 官網 futContractsDateDown(一手、BIG5 CSV)。start/end 'YYYY-MM-DD'。
    欄位(表頭):0日期 2身份別 9多方未平倉口數 11空方未平倉口數 13多空未平倉淨額。
    ⚠️ 歷史回測設 CHIPS_FOREIGN_SRC=finmind → 改走 FinMind(官網 CSV 端點不 serve 老資料)。"""
    if os.getenv("CHIPS_FOREIGN_SRC", "").lower() == "finmind":
        return _foreign_netoi_finmind(start, end)
    body = urllib.parse.urlencode({"queryStartDate": start.replace("-", "/"),
                                   "queryEndDate": end.replace("-", "/"),
                                   "commodityId": "TXF"}).encode()
    req = urllib.request.Request(TAIFEX_FUT, data=body, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
    out = {}
    for row in csv.reader(io.StringIO(raw.decode("big5", "replace"))):
        if len(row) < 14 or "外資" not in row[2]:        # 「外資及陸資」
            continue
        d = row[0].strip().replace("/", "-")
        try:
            out[d] = float(row[9].replace(",", "")) - float(row[11].replace(",", ""))
        except ValueError:
            continue
    return out


def fetch_tx_ohlc(start, end):
    """TX 期貨日 OHLC(近月日盤,FinMind TaiwanFuturesDaily)。回 {date:{open,high,low,close}}。
    ⚠️ 必須只取 trading_session=='position' 日盤:chips 是日盤策略(開盤進、收盤出),
    after_market(夜盤 15:00→05:00)與日盤背離大時會記錯 session(06-08 空單 −736 被夜盤誤記成
    +2881)。另排除價差合約(contract_date 含 '/')、同日取最大量近月。"""
    rows = fetch_finmind("TaiwanFuturesDaily", "TX", start, end)
    out = {}
    for r in rows:
        if r.get("trading_session") != "position":        # 只取日盤
            continue
        if "/" in str(r.get("contract_date", "")):         # 排除價差(calendar spread)合約
            continue
        d = r["date"]
        try:
            o, h, l, c = float(r["open"]), float(r["max"]), float(r["min"]), float(r["close"])
            vol = float(r.get("volume", 0))
        except (KeyError, ValueError, TypeError):
            continue
        if o <= 0 or h <= 0:
            continue
        prev = out.get(d)
        if prev is None or vol > prev["vol"]:              # 同日取最大量(近月)
            out[d] = {"open": o, "high": h, "low": l, "close": c, "vol": vol}
    return {d: {k: v[k] for k in ("open", "high", "low", "close")} for d, v in out.items()}


def fetch_taifex_largetrader(start, end):
    """TAIFEX 大戶 all_ratio[date]=(t10b−t10s)/oi(TX/999999/flag0)。start/end 'YYYY/MM/DD'。"""
    body = urllib.parse.urlencode({"queryStartDate": start, "queryEndDate": end}).encode()
    req = urllib.request.Request(TAIFEX_LT, data=body, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        raw = r.read()
    out = {}
    for row in csv.reader(io.StringIO(raw.decode("big5", "replace"))):
        if len(row) < 10 or row[1].strip() != "TX" or row[3].strip() != "999999" or row[4].strip() != "0":
            continue
        d = row[0].strip().replace("/", "-")
        try:
            t10b, t10s, oi = int(row[7]), int(row[8]), int(row[9])
        except ValueError:
            continue
        if oi > 0:
            out[d] = (t10b - t10s) / oi
    return out


def _mean_std(xs):
    n = len(xs)
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1) if n > 1 else 0.0
    return m, var ** 0.5


def load_history():
    rows = []
    if HIST.exists():
        with open(HIST, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows.append(r)
    return rows


def build_history(start, end):
    """抓 [start,end] 的三源,合併出每交易日一列,寫 history.csv(date,net_OI,flow,all_ratio,OHLC)。"""
    s_iso, e_iso = start.isoformat(), end.isoformat()
    print(f"抓資料 {s_iso} ~ {e_iso} ...")
    foreign = fetch_foreign_netoi(s_iso, e_iso); time.sleep(0.3)
    ohlc = fetch_tx_ohlc(s_iso, e_iso); time.sleep(0.3)
    lt = {}
    cur = start.replace(day=1)
    while cur <= end:
        nxt = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
        last = nxt - timedelta(days=1)
        lt.update(fetch_taifex_largetrader(cur.strftime("%Y/%m/%d"),
                                           min(last, end).strftime("%Y/%m/%d")))
        time.sleep(0.3)
        cur = nxt
    # 合併:date 同時有 外資 + 大戶 + OHLC 才算完整交易日
    dates = sorted(set(foreign) & set(lt) & set(ohlc))
    rows, prev_netoi = [], None
    for d in dates:
        net = foreign[d]
        flow = (net - prev_netoi) if prev_netoi is not None else 0.0
        prev_netoi = net
        o = ohlc[d]
        rows.append(dict(date=d, net_OI=net, flow=flow, all_ratio=lt[d],
                         open=o["open"], high=o["high"], low=o["low"], close=o["close"]))
    DDIR.mkdir(parents=True, exist_ok=True)
    with open(HIST, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["date", "net_OI", "flow", "all_ratio", "open", "high", "low", "close"])
        w.writeheader(); w.writerows(rows)
    print(f"history: {len(rows)} 交易日 | {rows[0]['date'] if rows else '-'} ~ {rows[-1]['date'] if rows else '-'}")
    return rows


def combo_at(rows, i):
    """用 rows[i−59..i] 算 combo[i](因果、不含未來)。回 (combo,z_flow,z_lt) 或 None(暖身不足)。"""
    if i < Z_WIN - 1:
        return None
    win = rows[i - Z_WIN + 1: i + 1]
    flows = [float(r["flow"]) for r in win]
    ratios = [float(r["all_ratio"]) for r in win]
    mf, sf = _mean_std(flows)
    mr, sr = _mean_std(ratios)
    if sf == 0 or sr == 0:
        return None
    zf = (float(rows[i]["flow"]) - mf) / sf
    zl = (float(rows[i]["all_ratio"]) - mr) / sr
    return (zf + zl) / 2, zf, zl


def side_of(combo):
    return "long" if combo > THR else ("short" if combo < -THR else "flat")


def settle_trade(side, bar):
    """T+1 日:side(來自前一日 combo)+ 當日 OHLC → entry開/exit收或−2%停損。回 dict 或 None(flat)。"""
    if side == "flat":
        return None
    o, h, l, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
    entry = o
    if side == "long":
        stop = entry * (1 - STOP_PCT)
        if l <= stop:
            exit_px, reason = stop, "−2%停損"
        else:
            exit_px, reason = c, "收盤平倉"
        pnl_pts = exit_px - entry
    else:
        stop = entry * (1 + STOP_PCT)
        if h >= stop:
            exit_px, reason = stop, "−2%停損"
        else:
            exit_px, reason = c, "收盤平倉"
        pnl_pts = entry - exit_px
    pnl = pnl_pts * POINT_VALUE * LOTS
    return dict(side=side, entry=entry, exit=exit_px, reason=reason,
                pnl_pts=round(pnl_pts, 1), pnl=round(pnl, 0), ret_pct=round(pnl_pts / entry * 100, 3))


def append_tape(rec):
    DDIR.mkdir(parents=True, exist_ok=True)
    new = not TAPE.exists()
    with open(TAPE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["trade_date", "signal_date", "combo", "z_flow", "z_lt", "side",
                        "entry", "exit", "exit_reason", "pnl_pts", "pnl", "ret_pct",
                        "slippage_note"])
        w.writerow(rec)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backfill", nargs=2, metavar=("START", "END"),
                    help="種子+回放:抓整段、建歷史、逐日補記 paper 單")
    args = ap.parse_args()

    if args.backfill:
        s, e = (date.fromisoformat(x) for x in args.backfill)
        rows = build_history(s, e)
        # 逐日回放:combo[i] 決定 i+1 的 side,用 rows[i+1] 結算
        n = 0
        if TAPE.exists():
            TAPE.unlink()
        for i in range(len(rows) - 1):
            cb = combo_at(rows, i)
            if cb is None:
                continue
            combo, zf, zl = cb
            side = side_of(combo)
            tr = settle_trade(side, rows[i + 1])
            if tr is None:
                continue
            append_tape([rows[i + 1]["date"], rows[i]["date"], round(combo, 3), round(zf, 3), round(zl, 3),
                         tr["side"], tr["entry"], tr["exit"], tr["reason"], tr["pnl_pts"], tr["pnl"],
                         tr["ret_pct"], "backfill(OHLC、無實滑價)"])
            n += 1
        # 摘要
        import statistics
        pnls = []
        if TAPE.exists():
            with open(TAPE, encoding="utf-8") as f:
                pnls = [float(r["pnl"]) for r in csv.DictReader(f)]
        if pnls:
            wins = [p for p in pnls if p > 0]
            pf = sum(wins) / (abs(sum(p for p in pnls if p <= 0)) or 1e-9)
            print(f"\n回放 {len(pnls)} 筆 | 勝率 {len(wins)/len(pnls)*100:.1f}% | PF {pf:.2f} | 淨 {sum(pnls):+,.0f} | ~{len(pnls)/(len(rows)/21):.1f} 筆/月")
        return

    # ── 每日模式(cron):抓近 ~130 日 → 補結算「所有未記錄的交易日」(漏跑也能補)→ 寫最新 signal ──
    # 只用三源都完整的交易日(build_history 已過濾)→ 即使當日大戶報表還沒出,也只會處理到前一完整日、
    # 隔天自動補上,絕不用半套資料。rows[i] 的交易 side 來自 combo[i-1](因果、不偷看)。
    today = date.today()
    rows = build_history(today - timedelta(days=130), today)
    if len(rows) < Z_WIN + 1:
        print(f"暖身不足({len(rows)}<{Z_WIN+1}),先 --backfill 種子歷史"); return
    recorded = set()
    if TAPE.exists():
        with open(TAPE, encoding="utf-8") as f:
            recorded = {r["trade_date"] for r in csv.DictReader(f)}
    new_n = 0
    for i in range(Z_WIN, len(rows)):
        d = rows[i]["date"]
        if d in recorded:
            continue
        cb = combo_at(rows, i - 1)
        if cb is None:
            continue
        combo, zf, zl = cb
        side = side_of(combo)
        tr = settle_trade(side, rows[i])
        if tr is None:
            continue   # FLAT 日不記(避免 tape 灌水;下次仍會略過、無害)
        append_tape([d, rows[i - 1]["date"], round(combo, 3), round(zf, 3), round(zl, 3),
                     tr["side"], tr["entry"], tr["exit"], tr["reason"], tr["pnl_pts"], tr["pnl"],
                     tr["ret_pct"], "OHLC、無實滑價(paper)"])
        new_n += 1
        print(f"  記錄 {d}: {tr['side']} {tr['pnl']:+.0f}元 ({tr['reason']})")
    # 最新 signal(最新完整日的 combo → 下一交易日 side)
    cb = combo_at(rows, len(rows) - 1)
    if cb:
        combo, zf, zl = cb
        side = side_of(combo)
        nxt = (date.fromisoformat(rows[-1]["date"]) + timedelta(days=1)).isoformat()
        SIGNAL.write_text(json.dumps(dict(trade_date=nxt, side=side, combo=round(combo, 3),
                                          z_flow=round(zf, 3), z_lt=round(zl, 3)), ensure_ascii=False, indent=2),
                          encoding="utf-8")
        print(f"本次新記錄 {new_n} 筆 | 下一訊號({nxt}):{side} (combo {combo:+.2f})")


if __name__ == "__main__":
    main()
