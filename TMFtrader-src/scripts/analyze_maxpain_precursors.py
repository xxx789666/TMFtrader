"""maxpain_v2 歷史回測 盈虧單前兆分析 — signal 前 5 個交易日的價格/籌碼是否有跡可循。

對 133 筆歷史 setups(2020-2025 + 2026)逐筆計算 signal_t(訊號日收盤、無前視)當下可得的:
  價格面: 前5日累積報酬 ret5、訊號日當日報酬 ret1、前5日上漲天數 updays、前5日波動 vol5、
          距20日高點回撤 dd20
  籌碼面: 外資TX淨未平倉 流量(日Δ) flow1、前5日累積流量 flow5、流量60日z分數 z_flow、
          淨未平倉水位60日z z_netoi
  原生:   dist(maxpain 距離,tape 內建)

資料: 日OHLC + 外資淨OI 都走 FinMind(同 chips_combo_daily.py 端點、只取日盤 position)、
     逐年抓+本機 JSON cache(data/maxpain_precursor_cache/)、重跑不再打 API。

輸出: 盈/虧組各特徵 mean/median 對照、各特徵三分位 bucket 的 WR/平均PnL/PF、
     逐筆特徵 CSV(D:\\vps自動化交易每日籌碼分析報告\\歷史回測\\maxpain_precursors.csv)。

用法: python scripts/analyze_maxpain_precursors.py
"""
import json
import math
import os
import statistics
import sys
import urllib.parse
import urllib.request

import pandas as pd

TAPE_DIR = r"D:\vps自動化交易每日籌碼分析報告\歷史回測"
TAPES = [os.path.join(TAPE_DIR, "maxpain_2020_2025.csv"),
         os.path.join(TAPE_DIR, "maxpain_2026.csv")]
OUT_CSV = os.path.join(TAPE_DIR, "maxpain_precursors.csv")
CACHE = os.path.join(os.path.dirname(__file__), "..", "data", "maxpain_precursor_cache")
FINMIND = "https://api.finmindtrade.com/api/v4/data"
FINMIND_TOKEN = os.getenv("FINMIND_TOKEN", "").strip()
YEARS = list(range(2019, 2027))   # 2019 起抓給最早 setup 留 warm-up


def fetch_finmind(dataset, data_id, start, end):
    q = {"dataset": dataset, "data_id": data_id, "start_date": start, "end_date": end}
    if FINMIND_TOKEN:
        q["token"] = FINMIND_TOKEN
    req = urllib.request.Request(f"{FINMIND}?{urllib.parse.urlencode(q)}",
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        j = json.loads(r.read().decode("utf-8"))
    if j.get("status") != 200 and "data" not in j:
        raise RuntimeError(f"FinMind {dataset}: {j.get('msg', j)}")
    return j.get("data", [])


def cached_year(dataset, year):
    os.makedirs(CACHE, exist_ok=True)
    fp = os.path.join(CACHE, f"{dataset}_{year}.json")
    if os.path.exists(fp):
        with open(fp, encoding="utf-8") as f:
            return json.load(f)
    rows = fetch_finmind(dataset, "TX", f"{year}-01-01", f"{year}-12-31")
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(rows, f)
    print(f"  fetched {dataset} {year}: {len(rows)} rows", file=sys.stderr)
    return rows


def load_close():
    """TX 近月日盤收盤價 {date: close}(只 position、排價差、同日取最大量)。"""
    best = {}
    for y in YEARS:
        for r in cached_year("TaiwanFuturesDaily", y):
            if r.get("trading_session") != "position":
                continue
            if "/" in str(r.get("contract_date", "")):
                continue
            try:
                c, vol = float(r["close"]), float(r.get("volume", 0))
            except (KeyError, ValueError, TypeError):
                continue
            if c <= 0:
                continue
            d = r["date"]
            if d not in best or vol > best[d][1]:
                best[d] = (c, vol)
    return {d: cv[0] for d, cv in best.items()}


def load_foreign_netoi():
    """外資 TX 淨未平倉 {date: 多OI−空OI}(口數)。"""
    out = {}
    for y in YEARS:
        for r in cached_year("TaiwanFuturesInstitutionalInvestors", y):
            if str(r.get("institutional_investors", "")).strip() != "外資":
                continue
            out[r["date"]] = float(r.get("long_open_interest_balance_volume", 0)) - \
                float(r.get("short_open_interest_balance_volume", 0))
    return out


def zscore(series, win=60):
    """series 末值對前 win 值(含末值)的 z。資料不足回 None。"""
    s = series[-win:]
    if len(s) < 20:
        return None
    mu = statistics.fmean(s)
    sd = statistics.pstdev(s)
    return (s[-1] - mu) / sd if sd > 1e-9 else 0.0


def features_at(d, dates, close, netoi_dates, netoi):
    """signal_t=d 收盤當下可得特徵。dates/netoi_dates 為已排序鍵列表。"""
    if d not in close:
        return None
    i = dates.index(d)
    if i < 21:
        return None
    cs = [close[x] for x in dates[: i + 1]]
    rets = [cs[k] / cs[k - 1] - 1 for k in range(1, len(cs))]
    r5 = rets[-5:]
    f = {
        "ret1": r5[-1] * 100,
        "ret5": (cs[-1] / cs[-6] - 1) * 100,
        "updays5": sum(1 for r in r5 if r > 0),
        "vol5": statistics.pstdev(r5) * 100,
        "dd20": (cs[-1] / max(cs[-21:]) - 1) * 100,
    }
    # 籌碼: 用 <= d 的最後一個籌碼日(籌碼 T 日盤後公佈、signal 18:40 跑時可得)
    j = None
    for k in range(len(netoi_dates) - 1, -1, -1):
        if netoi_dates[k] <= d:
            j = k
            break
    if j is None or j < 6:
        f.update({"flow1": None, "flow5": None, "z_flow": None, "z_netoi": None})
        return f
    lv = [netoi[x] for x in netoi_dates[: j + 1]]
    fl = [lv[k] - lv[k - 1] for k in range(1, len(lv))]
    f["flow1"] = fl[-1]
    f["flow5"] = sum(fl[-5:])
    f["z_flow"] = zscore(fl)
    f["z_netoi"] = zscore(lv)
    return f


def fmt(v):
    return "   n/a" if v is None else f"{v:+8.2f}"


def bucket_report(rows, key, label):
    vals = [(r[key], r["pnl"]) for r in rows if r.get(key) is not None]
    if len(vals) < 12:
        return
    vals.sort(key=lambda x: x[0])
    n = len(vals)
    cuts = [vals[: n // 3], vals[n // 3: 2 * n // 3], vals[2 * n // 3:]]
    names = ["低", "中", "高"]
    print(f"\n  {label} ({key}) 三分位:")
    for nm, grp in zip(names, cuts):
        pnls = [p for _, p in grp]
        w = [p for p in pnls if p > 0]
        gl = -sum(p for p in pnls if p < 0)
        pf = (sum(w) / gl) if gl > 0 else float("inf")
        rng = f"[{grp[0][0]:+.2f} ~ {grp[-1][0]:+.2f}]"
        print(f"    {nm} {rng:>22}: {len(pnls):>3}筆 WR {len(w)/len(pnls)*100:4.0f}% "
              f"avg {statistics.fmean(pnls):>+9,.0f} PF {pf:4.2f} 淨 {sum(pnls):>+10,.0f}")


def main():
    tape = pd.concat([pd.read_csv(t) for t in TAPES], ignore_index=True)
    tape = tape.sort_values("signal_t").reset_index(drop=True)
    close = load_close()
    netoi = load_foreign_netoi()
    dates = sorted(close)
    netoi_dates = sorted(netoi)
    print(f"setups={len(tape)}  日K={len(dates)}天({dates[0]}~{dates[-1]})  "
          f"外資OI={len(netoi_dates)}天({netoi_dates[0]}~{netoi_dates[-1]})")

    rows, skipped = [], 0
    for _, r in tape.iterrows():
        d = str(r["signal_t"])
        f = features_at(d, dates, close, netoi_dates, netoi)
        if f is None:
            skipped += 1
            continue
        f.update({"signal_t": d, "dist": float(r["dist"]) * 100,
                  "pnl": float(r["pnl"]), "exit_reason": r["exit_reason"],
                  "win": float(r["pnl"]) > 0})
        rows.append(f)
    print(f"有效 {len(rows)} 筆(略過 {skipped} 筆: 無價格史/暖身不足)")

    wins = [r for r in rows if r["win"]]
    loss = [r for r in rows if not r["win"]]
    print(f"\n盈利 {len(wins)} 筆(avg {statistics.fmean(r['pnl'] for r in wins):+,.0f})  "
          f"虧損 {len(loss)} 筆(avg {statistics.fmean(r['pnl'] for r in loss):+,.0f})")

    feats = [("ret5", "前5日累積漲跌%"), ("ret1", "訊號日漲跌%"), ("updays5", "前5日上漲天數"),
             ("vol5", "前5日日報酬波動%"), ("dd20", "距20日高回撤%"),
             ("flow1", "外資淨OI日變化(口)"), ("flow5", "外資淨OI 5日變化(口)"),
             ("z_flow", "外資流量60日z"), ("z_netoi", "外資OI水位60日z"),
             ("dist", "maxpain距離%")]
    print(f"\n{'特徵':<18}{'盈mean':>10}{'虧mean':>10}{'盈med':>10}{'虧med':>10}")
    for k, lab in feats:
        wv = [r[k] for r in wins if r.get(k) is not None]
        lv = [r[k] for r in loss if r.get(k) is not None]
        if not wv or not lv:
            continue
        print(f"{lab:<18}{fmt(statistics.fmean(wv)):>10}{fmt(statistics.fmean(lv)):>10}"
              f"{fmt(statistics.median(wv)):>10}{fmt(statistics.median(lv)):>10}")

    for k, lab in feats:
        bucket_report(rows, k, lab)

    pd.DataFrame(rows).to_csv(OUT_CSV, index=False, encoding="utf-8-sig")
    print(f"\n逐筆特徵 → {OUT_CSV}")


if __name__ == "__main__":
    main()
