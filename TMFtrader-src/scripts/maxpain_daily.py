"""maxpain_v2 — 每日 PAPER runner(自包含、HTTP 資料、不吃 shioaji)。

規格:deployed_strategies/maxpain_v2/HANDOFF_maxpain_v2_paper.md(凍結 2026-06-08)。
Max Pain 做多+順勢加碼:每週週選 ~6 DTE(訊號日 t)用 t 盤後 TXO OI 算 Max Pain;
dist=(MaxPain−S)/S>0 → t+1 開盤進多單 S1;盤中 +1%(S1×1.01)加第2口;−2%(S1×0.98)全停;
否則抱到該週選結算(以日收盤近似)。只做多、最多 2 口、固定口、訊號用大台 TXO、執行載具微台 TMF。

⚠️ 時序鐵律(做錯全錯):combo/MaxPain 只用「訊號日 t 當天盤後 OI」,t+1 才進場(無 look-ahead)。
⚠️ 凍結:−2% 停損(HANDOFF §1,非參考腳本的 −3%)、+1% 加碼。勿調參。
⚠️ paper 用日 OHLC 結算 = 無真實滑價/盤中序列;HANDOFF §6 預期值錨 OOS(Sharpe~0.5),非 in-sample 1.78。
⚠️ 資料源:全 FinMind。OHLC=TaiwanFuturesDaily(TX);選擇權 OI=TaiwanOptionDaily(TXO,
   只取 trading_session=='position' 日盤;after_market 場 OI 為 0)。
⚠️ 到期代碼:ed_of 解 contract_date 的 YYYYMM(月選3rd-Wed)/ YYYYMMW#(週選);F# 是「週五」
   日選/週選(經 TAIFEX 到期日欄證實 F1=次週五),本策略目標一律是週三週選 → F# 該丟、與 lab 一致。

用法:
  python scripts/maxpain_daily.py              # 每日 cron:補近期完成的組 + 寫待進場 signal
  python scripts/maxpain_daily.py --days 90    # 看更長窗(預設 45 交易日窗)
"""
import argparse
import csv
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DDIR = ROOT / "data" / "maxpain_v2"
SIGCACHE = DDIR / "signals.json"     # {signal_date: {ed, maxpain, close, dist}}
NEXT = DDIR / "next_signal.json"
TAPE = DDIR / "decisions.csv"

PV = 10.0            # 微台 TMF
SCALE = 0.01         # +1% 加第2口
STOP = 0.02          # −2% 全停(凍結值,非參考腳本 −3%)
FINMIND = "https://api.finmindtrade.com/api/v4/data"
FINMIND_TOKEN = os.getenv("FINMIND_TOKEN", "").strip()


def nw(y, m, n):
    dd = date(y, m, 1)
    return dd + timedelta(days=(2 - dd.weekday()) % 7 + 7 * (n - 1))


def ed_of(code):
    a = re.match(r"^(\d{4})(\d{2})$", code)
    b = re.match(r"^(\d{4})(\d{2})W(\d)$", code)
    if a:
        return nw(int(a[1]), int(a[2]), 3)
    if b:
        return nw(int(b[1]), int(b[2]), int(b[3]))
    return None   # F# 等:解不了


def _finmind(params):
    q = dict(params)
    if FINMIND_TOKEN:
        q["token"] = FINMIND_TOKEN
    req = urllib.request.Request(f"{FINMIND}?{urllib.parse.urlencode(q)}", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_opt_oi(date_iso):
    """FinMind TaiwanOptionDaily 單日。只取 position 日盤、OI>0。回 [(contract_date, strike, call_put, oi)]。"""
    j = _finmind({"dataset": "TaiwanOptionDaily", "data_id": "TXO",
                  "start_date": date_iso, "end_date": date_iso})
    out = []
    for r in j.get("data", []):
        if r.get("trading_session") != "position":
            continue
        oi = r.get("open_interest") or 0
        if oi <= 0:
            continue
        try:
            strike = int(float(r["strike_price"]))
        except (KeyError, ValueError, TypeError):
            continue
        out.append((str(r["contract_date"]), strike, str(r["call_put"]).lower(), float(oi)))
    return out


def fetch_tx_ohlc(start, end):
    rows = _finmind({"dataset": "TaiwanFuturesDaily", "data_id": "TX",
                     "start_date": start, "end_date": end}).get("data", [])
    out = {}
    for r in rows:
        try:
            o, h, l, c, vol = float(r["open"]), float(r["max"]), float(r["min"]), float(r["close"]), float(r.get("volume", 0))
        except (KeyError, ValueError, TypeError):
            continue
        if o <= 0:
            continue
        d = r["date"]
        if d not in out or vol > out[d]["vol"]:
            out[d] = {"open": o, "high": h, "low": l, "close": c, "vol": vol}
    return {d: {k: v[k] for k in ("open", "high", "low", "close")} for d, v in out.items()}


def max_pain(oi_rows, target_ed):
    """只用 ed_of(contract_date)==target_ed 的列算 Max Pain。回 maxpain_K(不足回 None)。"""
    coi, poi = {}, {}
    for code, strike, cp, oi in oi_rows:
        ed = ed_of(code)
        if ed is None or ed != target_ed:   # F# / 非目標到期 → 丟
            continue
        if cp == "call":
            coi[strike] = coi.get(strike, 0) + oi
        elif cp == "put":
            poi[strike] = poi.get(strike, 0) + oi
    ks = sorted(set(coi) | set(poi))
    if len(ks) < 5:
        return None
    best, bp = None, 1e18
    for K in ks:
        pain = sum(coi.get(k, 0) * max(0, K - k) for k in coi) + sum(poi.get(k, 0) * max(0, k - K) for k in poi)
        if pain < bp:
            bp, best = pain, K
    return best


def load_cache():
    return json.loads(SIGCACHE.read_text(encoding="utf-8")) if SIGCACHE.exists() else {}


def save_cache(c):
    DDIR.mkdir(parents=True, exist_ok=True)
    SIGCACHE.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")


def append_tape(rec):
    DDIR.mkdir(parents=True, exist_ok=True)
    new = not TAPE.exists()
    with open(TAPE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["signal_t", "expiry_ed", "maxpain", "S_close", "dist", "entry_t1", "S1",
                        "added", "S2", "exit_reason", "exit_date", "exit_px", "pnl_pts", "pnl", "lots", "note"])
        w.writerow(rec)


def settle(S1, hold_days, ohlc, edx):
    """hold_days: 進場日..edx 的交易日 list。回 dict。"""
    lvl, stp = S1 * (1 + SCALE), S1 * (1 - STOP)
    added = False
    exit_px = reason = exit_d = None
    for d in hold_days:
        bar = ohlc[d]
        if bar["high"] >= lvl:
            added = True
        if bar["low"] <= stp:
            exit_px, reason, exit_d = stp, "stop", d
            break
        if d == edx:
            exit_px, reason, exit_d = bar["close"], "settle", d
            break
    if exit_px is None:
        exit_px, reason, exit_d = ohlc[hold_days[-1]]["close"], "settle", hold_days[-1]
    S2 = lvl if added else None
    pts = (exit_px - S1) + ((exit_px - S2) if added else 0.0)
    return dict(added=added, S2=S2, exit_px=exit_px, reason=reason, exit_d=exit_d,
                pts=round(pts, 1), pnl=round(pts * PV, 0), lots=2 if added else 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=45, help="回看交易日窗(預設45)")
    args = ap.parse_args()
    today = date.today()
    ohlc = fetch_tx_ohlc((today - timedelta(days=args.days * 2)).isoformat(), today.isoformat())
    tds = sorted(ohlc)   # 交易日(ISO str)
    if len(tds) < 10:
        print("OHLC 不足"); return
    tds_d = [date.fromisoformat(x) for x in tds]
    cache = load_cache()
    recorded = set()
    if TAPE.exists():
        with open(TAPE, encoding="utf-8") as f:
            recorded = {r["signal_t"] for r in csv.DictReader(f)}

    # 候選到期 = 窗內所有週三(週選/月選);只處理「edx 在交易日內」的
    exps = []
    d0, d1 = tds_d[0], tds_d[-1] + timedelta(days=10)
    d = d0
    while d <= d1:
        if d.weekday() == 2:   # 週三
            exps.append(d)
        d += timedelta(days=1)

    last_td = tds_d[-1]
    new_rec = 0
    status = {"state": "flat", "as_of": last_td.isoformat()}
    for ed in exps:
        # 訊號日 t = DTE 在 [5,8] 最接近 6
        cands = [(x, (ed - x).days) for x in tds_d if 5 <= (ed - x).days <= 8]
        if not cands:
            continue
        t = min(cands, key=lambda z: abs(z[1] - 6))[0]
        t_iso = t.isoformat()
        # 算/取 Max Pain(快取)
        if t_iso not in cache:
            try:
                oi = fetch_opt_oi(t_iso)
                time.sleep(0.3)
                mp = max_pain(oi, ed)
            except Exception as e:
                print(f"  訊號日 {t_iso} 抓/算失敗: {e}"); continue
            if mp is None:
                print(f"  訊號日 {t_iso} 目標到期 {ed} 無足夠履約 OI → 跳過"); cache[t_iso] = {"ed": ed.isoformat(), "skip": True}; save_cache(cache); continue
            S = ohlc[t_iso]["close"]
            cache[t_iso] = {"ed": ed.isoformat(), "maxpain": mp, "close": S, "dist": round((mp - S) / S, 4)}
            save_cache(cache)
        sig = cache[t_iso]
        if sig.get("skip") or sig.get("dist", -1) <= 0:
            continue   # dist<=0 不做多

        fut = [x for x in tds_d if x > t]
        t1 = fut[0] if fut else None         # 進場日(t 之後首個交易日)
        fu = [x for x in tds_d if x >= ed]
        edx = fu[0] if fu else None          # 結算交易日(首個 >= ed)

        if t1 is None:
            # 訊號剛出、進場日(明天開盤)尚未到 → 待進場
            status = {"state": "signal_fired", "as_of": last_td.isoformat(), "signal_t": t_iso,
                      "ed": sig["ed"], "side": "long", "dist": sig["dist"], "maxpain": sig["maxpain"],
                      "S_close": sig["close"], "note": "明日開盤進多單第1口"}
            continue
        if edx is None:
            # 已進場(t1<=last_td)但結算日(ed)仍在未來 → 持倉中
            S1 = ohlc[t1.isoformat()]["open"]
            status = {"state": "open", "as_of": last_td.isoformat(), "signal_t": t_iso,
                      "entry_t1": t1.isoformat(), "S1": S1, "scale_at": round(S1 * (1 + SCALE), 1),
                      "stop_at": round(S1 * (1 - STOP), 1), "ed": sig["ed"], "side": "long",
                      "dist": sig["dist"], "maxpain": sig["maxpain"], "note": f"持倉中、目標抱到 {sig['ed']} 結算"}
            continue
        if t1 > edx:
            continue                          # 進場已晚於結算(極端),跳過
        # edx 在窗內 → 已結算 → 記錄(若未記)
        if t_iso in recorded:
            continue
        hold = [x for x in tds_d if t1 <= x <= edx]
        r = settle(ohlc[t1.isoformat()]["open"], [x.isoformat() for x in hold], ohlc, edx.isoformat())
        append_tape([t_iso, ed.isoformat(), sig["maxpain"], sig["close"], sig["dist"], t1.isoformat(),
                     ohlc[t1.isoformat()]["open"], r["added"], r["S2"], r["reason"], r["exit_d"],
                     r["exit_px"], r["pts"], r["pnl"], r["lots"], "paper、日OHLC結算、無實滑價"])
        recorded.add(t_iso)
        new_rec += 1
        print(f"  記錄 訊號{t_iso}→進{t1.isoformat()} dist{sig['dist']:+.3f} {r['reason']} {r['pnl']:+.0f}元 (lots{r['lots']})")

    NEXT.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"本次新記錄 {new_rec} 筆 | 狀態={status['state']}"
          + (f"(訊號{status.get('signal_t')} dist{status.get('dist'):+.3f})" if status['state'] != 'flat' else ""))
    _summarize_tape()


def _summarize_tape():
    if not TAPE.exists():
        return
    pnl = []
    with open(TAPE, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                pnl.append(float(r["pnl"]))
            except (KeyError, ValueError):
                pass
    if not pnl:
        return
    n = len(pnl)
    wins = [x for x in pnl if x > 0]
    gp = sum(wins)
    gl = -sum(x for x in pnl if x < 0)
    pf = (gp / gl) if gl > 0 else float("inf")
    print(f"  [tape] {n}筆 淨{sum(pnl):+,.0f}元 勝率{len(wins)/n*100:.0f}% PF{pf:.2f}"
          f"(注意:paper、日OHLC無滑價、樣本含 2026-H1 melt-up regime,前推預期錨 OOS Sharpe~0.5)")


if __name__ == "__main__":
    main()
