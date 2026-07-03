"""maxpain_v2 — 每日 PAPER runner(自包含、HTTP 資料、不吃 shioaji)。

規格:deployed_strategies/maxpain_v2/HANDOFF_maxpain_v2_paper.md(凍結 2026-06-08)。
Max Pain 做多+順勢加碼:每週週選 ~6 DTE(訊號日 t)用 t 盤後 TXO OI 算 Max Pain;
dist=(MaxPain−S)/S>0 → t+1 開盤進多單 S1;盤中 +1%(S1×1.01)加第2口;−2%(S1×0.98)全停;
否則抱到該週選結算(以日收盤近似)。只做多、最多 2 口、固定口、訊號用大台 TXO、執行載具小台 MXF(pv50)。

⚠️ 時序鐵律(做錯全錯):combo/MaxPain 只用「訊號日 t 當天盤後 OI」,t+1 才進場(無 look-ahead)。
⚠️ 凍結:−2% 停損(HANDOFF §1,非參考腳本的 −3%)、+1% 加碼。勿調參。
⚠️ paper 用日 OHLC 結算 = 無真實滑價/盤中序列;HANDOFF §6 預期值錨 OOS(Sharpe~0.5),非 in-sample 1.78。
⚠️ 資料源:選擇權 OI = TAIFEX 官網一手 optDailyMarketReport(各履約 c[13]未沖銷契約量);
   OHLC = FinMind TaiwanFuturesDaily(TX,只取 position 日盤)。2026-06-09 選擇權由 FinMind 改官網一手。
⚠️ 到期配對:用官網的 c[2]=契約到期日(YYYYMMDD)直接比對目標週選日 → 免 ed_of、無 F#(週五契約)
   代碼解析問題。OI 用官方總量(裁判:06-04 W2 算出 46200,對上 FinMind 46100、僅差一履約、更權威)。

用法:
  python scripts/maxpain_daily.py              # 每日 cron:補近期完成的組 + 寫待進場 signal
  python scripts/maxpain_daily.py --days 90    # 看更長窗(預設 45 交易日窗)
"""
# VPS 系統時鐘 UTC → date.today()/datetime.now() 一律 TST(2026-07-03 稽核:別依賴 crontab TZ 前綴)
import os as _os, time as _time_tz
_os.environ.setdefault('TZ', 'Asia/Taipei')
try:
    _time_tz.tzset()
except AttributeError:
    pass

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

PV = 50.0            # 小台 MXF(2026-06-09 由微台 pv10 改小台;價格仍用大台 TX 軌跡、口徑×5)
SCALE = 0.01         # +1% 加第2口
STOP = 0.02          # −2% 全停(凍結值,非參考腳本 −3%)
FINMIND = "https://api.finmindtrade.com/api/v4/data"
TAIFEX_OPT = "https://www.taifex.com.tw/cht/3/optDailyMarketReport"   # 選擇權每日行情(含各履約 OI)
FINMIND_TOKEN = os.getenv("FINMIND_TOKEN", "").strip()


def _nw(y, m, n):
    dd = date(y, m, 1)
    return dd + timedelta(days=(2 - dd.weekday()) % 7 + 7 * (n - 1))


def _ed_of(code):
    """老格式(≤2025、無到期日欄)用代碼解到期日:YYYYMM(月選3rd-Wed)/ YYYYMMW#(週選)。F# 等回 None。"""
    a = re.match(r"^(\d{4})(\d{2})$", code)
    b = re.match(r"^(\d{4})(\d{2})W(\d)$", code)
    if a:
        return _nw(int(a[1]), int(a[2]), 3)
    if b:
        return _nw(int(b[1]), int(b[2]), int(b[3]))
    return None


def _finmind(params):
    q = dict(params)
    if FINMIND_TOKEN:
        q["token"] = FINMIND_TOKEN
    req = urllib.request.Request(f"{FINMIND}?{urllib.parse.urlencode(q)}", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_opt_oi(date_iso):
    """TAIFEX 官網 optDailyMarketReport 單日 TXO 各履約 OI。回 [(expiry_yyyymmdd, strike, cp, oi)]。
    **格式自動偵測**(TAIFEX 2026 改版):
    - 16 cell(2026+):c[2]=契約到期日YYYYMMDD c[3]=履約 c[4]=Call/Put c[13]=未沖銷OI。到期=c[2]。
    - 15 cell(≤2025):c[1]=代碼 c[2]=履約 c[3]=Call/Put c[12]=未沖銷OI。到期=_ed_of(c[1])(無到期日欄)。
    兩者統一回 expiry=YYYYMMDD 字串,供 max_pain 用日期配對。"""
    body = urllib.parse.urlencode({"queryType": "2", "marketCode": "1", "commodity_id": "TXO",
                                   "queryDate": date_iso.replace("-", "/"), "MarketCode": "1",
                                   "commodity_idt": "TXO", "button": "送出查詢"}).encode()
    req = urllib.request.Request(TAIFEX_OPT, data=body, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        t = r.read().decode("utf-8", "replace")
    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", t, re.S):
        c = [re.sub(r"<[^>]+>", "", x).strip() for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S)]
        if not c or c[0] != "TXO":
            continue
        if len(c) >= 16:                                  # 新格式
            exp, si, cpi, oii = c[2], 3, 4, 13
        elif len(c) == 15:                                # 老格式
            ed = _ed_of(c[1])
            if ed is None:                                # F#/不可解 → 丟
                continue
            exp, si, cpi, oii = ed.strftime("%Y%m%d"), 2, 3, 12
        else:
            continue
        try:
            strike = int(c[si])
            oi = float(c[oii].replace(",", ""))
        except (ValueError, IndexError):
            continue
        if oi <= 0:
            continue
        out.append((exp, strike, c[cpi], oi))
    return out


def fetch_tx_ohlc(start, end):
    """每交易日的日盤(position)OHLC。進場 t+1 開盤、結算抱日盤收盤 → 一律用 position 場。
    ⚠️ 不可用 after_market(夜盤,15:00→05:00):與日盤背離大時會記錯 session(chips_combo 06-08
    踩過,空單 −736 被夜盤誤記成 +2881)。同時排除價差合約(contract_date 含 '/')、取最大量近月。"""
    rows = _finmind({"dataset": "TaiwanFuturesDaily", "data_id": "TX",
                     "start_date": start, "end_date": end}).get("data", [])
    out = {}
    for r in rows:
        if r.get("trading_session") != "position":      # 只取日盤
            continue
        if "/" in str(r.get("contract_date", "")):       # 排除價差(calendar spread)合約
            continue
        try:
            o, h, l, c, vol = float(r["open"]), float(r["max"]), float(r["min"]), float(r["close"]), float(r.get("volume", 0))
        except (KeyError, ValueError, TypeError):
            continue
        if o <= 0:
            continue
        d = r["date"]
        if d not in out or vol > out[d]["vol"]:           # 同日取最大量(近月)
            out[d] = {"open": o, "high": h, "low": l, "close": c, "vol": vol}
    return {d: {k: v[k] for k in ("open", "high", "low", "close")} for d, v in out.items()}


def max_pain(oi_rows, target_ed):
    """只用「契約到期日 == target_ed」的列算 Max Pain(c[2] 明確到期日配對)。回 maxpain_K(不足回 None)。"""
    target = target_ed.strftime("%Y%m%d")
    coi, poi = {}, {}
    for exp, strike, cp, oi in oi_rows:
        if exp != target:               # 非目標到期 → 丟
            continue
        if cp == "Call":
            coi[strike] = coi.get(strike, 0) + oi
        elif cp == "Put":
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
                        "added", "S2", "exit_reason", "exit_date", "exit_px", "pnl_pts", "pnl", "lots", "note",
                        "ret5d", "crash5d"])
        w.writerow(rec)


def _ret5d(tds, ohlc, t_iso):
    """訊號日收盤 vs 前5交易日收盤(進場前動能)。≤−3% = 「暴跌後訊號」旗標。
    🪦 濾網已被 2015-19 真 OOS 否決(剔掉組反而 +18,297/PF1.34、MCPT p=0.60、倒U不重現、
    plateau 全翻號;2020+ regime 品第五例,verdict 2026-06-13)。僅長期雙欄記錄、無操作含義;
    forward 50+ 筆若 crash 組顯著爛再回拋 lab。教訓:過 in-sample MCPT ≠ 跨 regime。"""
    try:
        i = tds.index(t_iso)
    except ValueError:
        return None
    if i < 5 or tds[i - 5] not in ohlc or t_iso not in ohlc:
        return None
    return ohlc[t_iso]["close"] / ohlc[tds[i - 5]]["close"] - 1


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


def eval_signal(t_iso, ed, ohlc, cache):
    """確保 cache[t_iso] 已算(必要時抓 TAIFEX OI 算 Max Pain),回 sig dict 或 None(抓/算失敗)。
    供 EARLY67 依序評估候選訊號日(週三 DTE7 → 週四 DTE6)、各自算 dist 後取第一個 dist>0。"""
    if t_iso in cache:
        return cache[t_iso]
    try:
        oi = fetch_opt_oi(t_iso)
        time.sleep(0.3)
        mp = max_pain(oi, ed)
    except Exception as e:
        print(f"  訊號日 {t_iso} 抓/算失敗: {e}")
        return None
    if mp is None:
        print(f"  訊號日 {t_iso} 目標到期 {ed} 無足夠履約 OI → 跳過")
        cache[t_iso] = {"ed": ed.isoformat(), "skip": True}; save_cache(cache)
        return cache[t_iso]
    S = ohlc[t_iso]["close"]
    cache[t_iso] = {"ed": ed.isoformat(), "maxpain": mp, "close": S, "dist": round((mp - S) / S, 4)}
    save_cache(cache)
    return cache[t_iso]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=45, help="回看交易日窗(預設45)")
    ap.add_argument("--start", help="歷史回測窗起 YYYY-MM-DD(與 --end 並用)")
    ap.add_argument("--end", help="歷史回測窗迄 YYYY-MM-DD(預設 today)")
    args = ap.parse_args()
    if args.start and args.end:                          # 歷史回測模式
        ohlc = fetch_tx_ohlc(args.start, args.end)
    else:                                                # 每日模式(從 today 回看)
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
        # 訊號日政策 = EARLY67(2026-06-15;由短暫的「嚴格 DTE6」改回,忠實回測判決:真 TAIFEX OI+逐根5m
        # 顯示 DTE7 在 2022 空頭 +97k、DTE6 −117k,Sharpe 1.14>0.74、maxDD 半;雙窗 EARLY67 淨利最高):
        #   候選訊號日依序 = 週三(DTE7)→ 週四(DTE6);各自算 dist,取「第一個 dist>0」鎖定。
        #   - 週三(DTE7,結算當日 EOD,舊週選已於當日早盤轉倉、新週籌碼已往新 maxpain 靠)= 有效、較挑;
        #   - 週四(DTE6,結算後第一個全日)= 週三沒訊號時的後備;
        #   - **排除週二(DTE8,結算前籌碼未轉倉、maxpain 不成熟,ed=6/17 實測週二 43300 vs 結算後 44250/44300)**。
        #   進場 = t+1:週三訊號→週四進、週四訊號→週五進。每 ed 一筆,週三進到就不再評估週四。
        # live 增量 cron:週三晚先評估週三→dist>0 即鎖(prior_ts 沿用);≤0 則週四晚評估週四。batch 模式
        # 一次看全:同一迴圈先試週三再試週四,兩模式行為一致。在途 ed=6/17 沿用已鎖 6/10(=EARLY67 正解,週四進)。
        prior_ts = sorted(k for k, v in cache.items()
                          if isinstance(v, dict) and v.get("ed") == ed.isoformat()
                          and not v.get("skip") and v.get("dist", -1) > 0)
        if prior_ts:
            cand_days = [date.fromisoformat(prior_ts[0])]      # 已鎖 dist>0 → 沿用最早那個(保護在途)
        else:
            cand_days = []
            for dte in (7, 6):                                 # 週三(DTE7)優先、週四(DTE6)次之
                m = [x for x in tds_d if (ed - x).days == dte]
                if m:
                    cand_days.append(m[0])
            if not cand_days:
                continue          # 週三/週四都尚未到(週三晚前)或逢假日 → 此 ed 本次不評估
        t = None
        for cd in cand_days:                                   # 依序評估,取第一個 dist>0
            sig = eval_signal(cd.isoformat(), ed, ohlc, cache)
            if sig and not sig.get("skip") and sig.get("dist", -1) > 0:
                t = cd
                break
        if t is None:
            continue          # 候選訊號日都 dist≤0/skip → 不做多
        t_iso = t.isoformat()
        sig = cache[t_iso]

        fut = [x for x in tds_d if x > t]
        t1 = fut[0] if fut else None         # 進場日(t 之後首個交易日)
        fu = [x for x in tds_d if x >= ed]
        edx = fu[0] if fu else None          # 結算交易日(首個 >= ed)

        rv = _ret5d(tds, ohlc, t_iso)
        crash = bool(rv is not None and rv <= -0.03)
        if t1 is None:
            # 訊號剛出、進場日(明天開盤)尚未到 → 待進場
            status = {"state": "signal_fired", "as_of": last_td.isoformat(), "signal_t": t_iso,
                      "ed": sig["ed"], "side": "long", "dist": sig["dist"], "maxpain": sig["maxpain"],
                      "S_close": sig["close"], "ret5d": (round(rv, 4) if rv is not None else None),
                      "crash5d": crash, "note": "明日開盤進多單第1口"}
            continue
        if edx is None:
            # 已進場(t1<=last_td)但結算日(ed)仍在未來 → 持倉中
            # 補持倉細節(供日報):從進場日到今天的 OHLC 推「是否已加碼(口數)/已觸停/未實現損益」。
            S1 = ohlc[t1.isoformat()]["open"]
            scale_lvl, stop_lvl = S1 * (1 + SCALE), S1 * (1 - STOP)
            hold_so_far = [x.isoformat() for x in tds_d if t1 <= x <= last_td]
            added = any(ohlc[d]["high"] >= scale_lvl for d in hold_so_far if d in ohlc)
            stop_hit = any(ohlc[d]["low"] <= stop_lvl for d in hold_so_far if d in ohlc)
            # ── 中途已觸 −2% 停損 → 當日全平、即時記 tape(策略「中途跌破停損線全平」),不等結算 ──
            # (修 2026-06-27:原本算出 stop_hit 卻仍掛「持倉中」抱到結算、MTM 用現價誤導 −131k、
            #  且與真 tick 引擎已停損 divergence。停損價才是實際出場。)
            if stop_hit:
                hsf = [d for d in hold_so_far if d in ohlc]
                r = settle(S1, hsf, ohlc, "")            # edx="" 不在 hold → 必因 stop 出場
                if t_iso not in recorded:
                    append_tape([t_iso, ed.isoformat(), sig["maxpain"], sig["close"], sig["dist"], t1.isoformat(),
                                 S1, r["added"], r["S2"], r["reason"], r["exit_d"],
                                 r["exit_px"], r["pts"], r["pnl"], r["lots"], "paper、日OHLC結算、無實滑價",
                                 (round(rv, 4) if rv is not None else ""), (1 if crash else 0)])
                    recorded.add(t_iso); new_rec += 1
                    print(f"  記錄(中途停損) 訊號{t_iso}→進{t1.isoformat()} {r['reason']} {r['pnl']:+.0f}元 (lots{r['lots']}) @{r['exit_d']}")
                status = {"state": "stopped", "as_of": last_td.isoformat(), "signal_t": t_iso,
                          "entry_t1": t1.isoformat(), "S1": S1, "stop_at": round(stop_lvl, 1),
                          "ed": sig["ed"], "side": "long", "dist": sig["dist"], "maxpain": sig["maxpain"],
                          "lots": r["lots"], "added": r["added"],
                          "exit_d": r["exit_d"], "exit_px": r["exit_px"], "realized_pnl": r["pnl"],
                          "note": f"已觸 −2% 停損、{r['exit_d']} 當日全平(不抱到結算);realized {r['pnl']:+.0f}元"}
                continue
            lots = 2 if added else 1
            last_close = ohlc[last_td.isoformat()]["close"] if last_td.isoformat() in ohlc else S1
            unreal = round(((last_close - S1) + ((last_close - scale_lvl) if added else 0.0)) * PV, 0)
            status = {"state": "open", "as_of": last_td.isoformat(), "signal_t": t_iso,
                      "entry_t1": t1.isoformat(), "S1": S1, "scale_at": round(scale_lvl, 1),
                      "stop_at": round(stop_lvl, 1), "ed": sig["ed"], "side": "long",
                      "dist": sig["dist"], "maxpain": sig["maxpain"],
                      "lots": lots, "added": added, "S2": (round(scale_lvl, 1) if added else None),
                      "avg_cost": round((S1 + scale_lvl) / 2, 1) if added else round(S1, 1),
                      "last_close": last_close, "unreal_pnl": unreal, "stop_hit": stop_hit,
                      "days_held": len(hold_so_far),
                      "ret5d": (round(rv, 4) if rv is not None else None), "crash5d": crash,
                      "note": f"持倉中、目標抱到 {sig['ed']} 結算"}
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
                     r["exit_px"], r["pts"], r["pnl"], r["lots"], "paper、日OHLC結算、無實滑價",
                     (round(rv, 4) if rv is not None else ""), (1 if crash else 0)])
        recorded.add(t_iso)
        new_rec += 1
        print(f"  記錄 訊號{t_iso}→進{t1.isoformat()} dist{sig['dist']:+.3f} {r['reason']} {r['pnl']:+.0f}元 (lots{r['lots']})"
              + (f" ℹ️暴跌後訊號(純記錄;前5日{rv*100:+.1f}%)" if crash else ""))

    NEXT.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"本次新記錄 {new_rec} 筆 | 狀態={status['state']}"
          + (f"(訊號{status.get('signal_t')} dist{status.get('dist'):+.3f})" if status['state'] != 'flat' else ""))
    _summarize_tape()


def _summarize_tape():
    if not TAPE.exists():
        return
    pnl, pnl_ex = [], []
    with open(TAPE, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                v = float(r["pnl"])
            except (KeyError, ValueError):
                continue
            pnl.append(v)
            if str(r.get("crash5d", "")).strip() not in ("1", "True", "true"):
                pnl_ex.append(v)

    def _line(p):
        n = len(p); w = [x for x in p if x > 0]; gl = -sum(x for x in p if x < 0)
        pf = (sum(w) / gl) if gl > 0 else float("inf")
        return f"{n}筆 淨{sum(p):+,.0f}元 勝率{len(w)/n*100:.0f}% PF{pf:.2f}" if n else "0筆"
    if not pnl:
        return
    print(f"  [tape] {_line(pnl)}(paper、日OHLC無滑價、含 2026 melt-up,前推錨 OOS Sharpe~0.5)")
    if len(pnl_ex) != len(pnl):
        print(f"  [tape|剔暴跌後訊號(純記錄;OOS已否決濾網)] {_line(pnl_ex)}")


if __name__ == "__main__":
    main()
