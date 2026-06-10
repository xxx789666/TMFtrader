"""settlement_v2 — 週選結算日 Tue/Wed 籌碼 edge,每日盤後 PAPER runner(自包含、HTTP 資料、不吃 shioaji)。

凍結規則(策略卡 strategy_settlement_v2.md / 交接手冊 handoff_settlement_v2_live_2026_06_10.md):
- 每個週選結算日(週三),用結算前一交易日(週二)~15:00 公布的籌碼決定方向:
  · 特徵A d_put_oi = (週二總PutOI − 前日總PutOI) / (週二總PutOI + 1),當週到期 expiry 聚合
  · 特徵B fx_dnet  = 外資TX期貨淨OI(週二) − (前一交易日);外資 = FinMind TX 當日 OI 最大法人(=外資)
- zsum = z(d_put_oi) + z(fx_dnet),expanding-z(只用過去、暖身載 309 筆歷史);|zsum|≥0.7 才下單,方向=sign。
- 週三 08:45 開盤進 1 口 MXF、13:45 收盤平、不過夜、無止損。cost 來回 4 點。約 2.6 筆/月。

⚠️ 紙上結算用日盤(position)OHLC:entry=週三 open、exit=週三 close(研究端用 5m 的 08:45/13:45,
   日OHLC 為 paper 近似,差異極小)。先 paper forward、與研究 169 筆 tape 對帳,再議真單。

用法:
  python scripts/settlement_v2_daily.py                 # 自動:鎖定最近(或本週)結算週三、算訊號、可結算就結算
  python scripts/settlement_v2_daily.py --wed 2026-06-10  # 指定結算週三
  python scripts/settlement_v2_daily.py --verify          # 用 feature_history 最後一筆對帳(驗 live 算法一致)
"""
import argparse
import csv
import json
import os
import re
import statistics
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DDIR = ROOT / "data" / "settlement_v2"
FEAT = DDIR / "feature_history.csv"          # settle_wed,signal_tue,d_put_oi,fx_dnet,tue_close(暖身+滾動)
TAPE = DDIR / "decisions.csv"                # 逐筆 paper 單(對齊研究 settlement_v2_2020_2025.csv)
NEXT = DDIR / "next_signal.json"
REPORT_DIR = ROOT / "data" / "reports" / "settlement_v2"

ZTHR = 0.7
COST_PTS = 4.0
PV = 50.0                                    # 小台 MXF
WARMUP = 40

FINMIND = "https://api.finmindtrade.com/api/v4/data"
TAIFEX_OPT = "https://www.taifex.com.tw/cht/3/optDailyMarketReport"
FINMIND_TOKEN = os.getenv("FINMIND_TOKEN", "").strip()


def _tg(msg):
    """推 TG(優雅降級:拿不到 core.notify 就只 print)。"""
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from core.notify import tg
        tg(msg)
    except Exception:
        pass
    print("[TG] " + msg)


# ── 資料抓取(重用 maxpain_daily / chips_combo_daily 的同款邏輯,自包含)──
def _finmind(params):
    q = dict(params)
    if FINMIND_TOKEN:
        q["token"] = FINMIND_TOKEN
    req = urllib.request.Request(f"{FINMIND}?{urllib.parse.urlencode(q)}", headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def _wed_contract_code(wed):
    """結算週三 → FinMind TXO contract_date 代碼。第3個週三=月選(YYYYMM);其餘=週選(YYYYMMWn)。
    n = 當月第幾個週三 = (day-1)//7 + 1。"""
    n = (wed.day - 1) // 7 + 1
    if n == 3:
        return f"{wed.year}{wed.month:02d}"          # 月選(3rd Wed)
    return f"{wed.year}{wed.month:02d}W{n}"           # 週選 W1/W2/W4/W5


def fetch_oi_by_strike(date_iso, wed):
    """FinMind TaiwanOptionDaily:回 (put_by, call_by) 各履約 OI {strike: oi}(position 場,一次抓拆 put/call)。
    用 FinMind 而非 TAIFEX HTML —— 後者欄位會漂(c[13] 非 OI、.5 小數證實抓到價格欄)。
    已對帳:position 場 Put OI 算出的 d_put_oi 與研究 feature_history 完全一致。"""
    code = _wed_contract_code(wed)
    rows = _finmind({"dataset": "TaiwanOptionDaily", "data_id": "TXO",
                     "start_date": date_iso, "end_date": date_iso}).get("data", [])
    put_by, call_by = {}, {}
    for r in rows:
        if str(r.get("contract_date")) != code:
            continue
        if r.get("trading_session") != "position":    # 只取日盤 OI(after_market 為 0)
            continue
        cp = str(r.get("call_put")).lower()
        try:
            k = float(r.get("strike_price", 0)); oi = float(r.get("open_interest", 0) or 0)
        except (TypeError, ValueError):
            continue
        if k <= 0:
            continue
        if cp == "put":
            put_by[k] = put_by.get(k, 0.0) + oi
        elif cp == "call":
            call_by[k] = call_by.get(k, 0.0) + oi
    return put_by, call_by


def fetch_put_oi_by_strike(date_iso, wed):
    return fetch_oi_by_strike(date_iso, wed)[0]


def fetch_put_oi_total(date_iso, wed):
    return sum(fetch_put_oi_by_strike(date_iso, wed).values())


def fetch_foreign_net(start, end):
    """外資 TX 期貨淨OI(多−空),FinMind TaiwanFuturesInstitutionalInvestors。回 {date_iso: net}(僅交易日)。
    名稱濾『外資』(handoff 證實 = 當日 OI 最大法人,1577 天 100% 一致)。"""
    rows = _finmind({"dataset": "TaiwanFuturesInstitutionalInvestors", "data_id": "TX",
                     "start_date": start, "end_date": end}).get("data", [])
    out = {}
    for r in rows:
        if str(r.get("institutional_investors", "")).strip() != "外資":
            continue
        out[r["date"]] = float(r.get("long_open_interest_balance_volume", 0)) - \
            float(r.get("short_open_interest_balance_volume", 0))
    return out


def fetch_tx_ohlc(start, end):
    """日盤(position)TX OHLC,排除價差合約、同日取最大量近月。回 {date_iso: {open,high,low,close}}。"""
    rows = _finmind({"dataset": "TaiwanFuturesDaily", "data_id": "TX",
                     "start_date": start, "end_date": end}).get("data", [])
    out = {}
    for r in rows:
        if r.get("trading_session") != "position" or "/" in str(r.get("contract_date", "")):
            continue
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


# ── 特徵 + 訊號 ──
def load_feat():
    rows = []
    if FEAT.exists():
        with open(FEAT, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    rows.append({"settle_wed": r["settle_wed"], "signal_tue": r["signal_tue"],
                                 "d_put_oi": float(r["d_put_oi"]), "fx_dnet": float(r["fx_dnet"]),
                                 "tue_close": float(r["tue_close"])})
                except (KeyError, ValueError):
                    continue
    return rows


def append_feat(settle_wed, signal_tue, d_put_oi, fx_dnet, tue_close):
    DDIR.mkdir(parents=True, exist_ok=True)
    new = not FEAT.exists()
    with open(FEAT, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["settle_wed", "signal_tue", "d_put_oi", "fx_dnet", "tue_close"])
        w.writerow([settle_wed, signal_tue, round(d_put_oi, 12), round(fx_dnet, 1), round(tue_close, 1)])


def _z(x, hist):
    if len(hist) < 2:
        return 0.0
    return (x - statistics.mean(hist)) / (statistics.stdev(hist) + 1e-9)


def compute_signal(wed_iso, past_feat, d_put_oi, fx_dnet):
    """expanding-z(只用 past_feat)→ zsum、方向、是否進場。"""
    hp = [r["d_put_oi"] for r in past_feat]
    hf = [r["fx_dnet"] for r in past_feat]
    z_put = _z(d_put_oi, hp)
    z_fx = _z(fx_dnet, hf)
    zsum = z_put + z_fx
    take = abs(zsum) >= ZTHR
    side = ("long" if zsum > 0 else "short") if take else "flat"
    return {"z_put": z_put, "z_fx": z_fx, "zsum": zsum, "take": take, "side": side,
            "warm": len(past_feat)}


# ── 日曆 ──
def trading_days_from(net_map):
    return sorted(net_map.keys())


def resolve_dates(wed_iso, net_map):
    """回 (signal_tue, prev_mon):結算週三前的兩個交易日(用外資資料的交易日序)。"""
    tds = [d for d in trading_days_from(net_map) if d < wed_iso]
    if len(tds) < 2:
        return None, None
    return tds[-1], tds[-2]


def next_settle_wed(today):
    d = today
    while d.weekday() != 2:
        d += timedelta(days=1)
    return d


def _settle_pending():
    """補結算上一筆 signal_fired(進場日 OHLC 當時還沒出 → 之後任何一次跑都用歷史 OHLC 補上、永遠正確)。
    防止 Wed cron 在收盤資料公布前跑就漏結算(之後 next_settle_wed 會跳走、那筆會永遠卡住)。"""
    if not NEXT.exists():
        return
    try:
        o = json.loads(NEXT.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    if o.get("state") != "signal_fired" or not o.get("take"):
        return
    try:
        wed = date.fromisoformat(o["settle_wed"])
    except (KeyError, ValueError):
        return
    ohlc = fetch_tx_ohlc((wed - timedelta(days=5)).isoformat(), (wed + timedelta(days=3)).isoformat())
    wbar = ohlc.get(o["settle_wed"])
    if not wbar:
        return                                          # 結算日 OHLC 還沒出 → 下次再補
    s = 1 if o["side"] == "long" else -1
    pnl_pts = s * (wbar["close"] - wbar["open"]) - COST_PTS
    settled = {"entry": wbar["open"], "exit": wbar["close"], "pnl_pts": round(pnl_pts, 1),
               "pnl": round(pnl_pts * PV, 0),
               "ret_pct": round((s * (wbar["close"] - wbar["open"]) / wbar["open"] - COST_PTS / wbar["open"]) * 100, 4)}
    _append_tape(o["settle_wed"], o.get("signal_tue", ""), {"zsum": o.get("zsum", 0), "side": o["side"]},
                 o.get("d_put_oi", 0), o.get("fx_dnet", 0), s, settled)
    o.update(settled); o["state"] = "settled"
    NEXT.write_text(json.dumps(o, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(o)
    print(f"[settlement_v2] 補結算 {o['settle_wed']} {o['side']} {settled['pnl']:+.0f}元")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wed", help="結算週三 YYYY-MM-DD(預設:今天起最近的週三)")
    ap.add_argument("--verify", action="store_true", help="用 feature_history 最後一筆對帳 live 算法")
    args = ap.parse_args()

    if args.verify:
        return verify()

    _settle_pending()                                   # 先補上一筆未結算的(用歷史 OHLC、永遠正確)

    wed = date.fromisoformat(args.wed) if args.wed else next_settle_wed(date.today())
    wed_iso = wed.isoformat()
    # 抓外資(交易日序)+ 解析週二/週一
    net = fetch_foreign_net((wed - timedelta(days=20)).isoformat(), wed_iso)
    tue, mon = resolve_dates(wed_iso, net)
    if not tue or not mon:
        print(f"[settlement_v2] {wed_iso}: 交易日不足/外資資料缺 → 不進場")
        NEXT.write_text(json.dumps({"state": "no_data", "settle_wed": wed_iso}, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    fx_dnet = net[tue] - net[mon]

    # OI(週二、週一,當週到期,逐履約 put + call)
    put_tue_by, call_tue_by = fetch_oi_by_strike(tue, wed)
    time.sleep(0.3)
    put_mon_by, call_mon_by = fetch_oi_by_strike(mon, wed)
    put_tue = sum(put_tue_by.values())
    put_mon = sum(put_mon_by.values())
    if put_tue <= 0:
        print(f"[settlement_v2] {wed_iso}: 週二({tue}) Put OI 抓不到 → 不進場")
        NEXT.write_text(json.dumps({"state": "no_data", "settle_wed": wed_iso, "signal_tue": tue}, ensure_ascii=False, indent=2), encoding="utf-8")
        return
    d_put_oi = (put_tue - put_mon) / (put_tue + 1)

    ohlc = fetch_tx_ohlc((wed - timedelta(days=20)).isoformat(), wed_iso)
    tue_close = ohlc.get(tue, {}).get("close", 0.0)

    # 賣權牆(支撐)變化:逐履約 ΔPut OI(週二−週一),挑變厚 top(供報告看「厚在哪個價位區間」)
    dP = {k: put_tue_by.get(k, 0.0) - put_mon_by.get(k, 0.0) for k in set(put_tue_by) | set(put_mon_by)}
    thick = sorted([(k, v) for k, v in dP.items() if v > 0], key=lambda x: -x[1])[:6]
    thin = sorted([(k, v) for k, v in dP.items() if v < 0], key=lambda x: x[1])[:3]

    def _wall(by, spot):
        """主牆 = 現價 ±10% 內最大 OI 履約(近價、可操作;避免遠價外靜態大單誤導)。band 內無則退全體。"""
        band = {k: v for k, v in by.items() if spot and abs(k - spot) <= spot * 0.10} or by
        return max(band, key=band.get) if band else 0.0
    put_wall = _wall(put_tue_by, tue_close)                                  # 近價最大 Put OI=主支撐牆
    # 買權牆(阻力)變化:逐履約 ΔCall OI;主阻力牆 = 近價最大 Call OI 履約
    dC = {k: call_tue_by.get(k, 0.0) - call_mon_by.get(k, 0.0) for k in set(call_tue_by) | set(call_mon_by)}
    call_thick = sorted([(k, v) for k, v in dC.items() if v > 0], key=lambda x: -x[1])[:6]
    call_wall = _wall(call_tue_by, tue_close)

    feat = load_feat()
    past = [r for r in feat if r["settle_wed"] < wed_iso]   # 只用過去
    sig = compute_signal(wed_iso, past, d_put_oi, fx_dnet)

    # 滾動 append 特徵(同一 settle_wed 不重複)→ 首次計算該結算日才推「訊號」TG
    if not any(r["settle_wed"] == wed_iso for r in feat):
        append_feat(wed_iso, tue, d_put_oi, fx_dnet, tue_close)
        if sig["take"]:
            _tg(f"[settlement_v2] 訊號 | 結算{wed_iso} 做{'多▲' if sig['side']=='long' else '空▼'} "
                f"zsum{sig['zsum']:+.2f}(d_put_oi{d_put_oi:+.3f}/fx{fx_dnet:+.0f}) "
                f"→ 週三開盤進1口MXF、收盤平、無止損")
        else:
            _tg(f"[settlement_v2] 結算{wed_iso} 空手(|zsum|{abs(sig['zsum']):.2f}<{ZTHR})")

    # 結算(若週三日盤 OHLC 已有)
    settled = None
    wbar = ohlc.get(wed_iso)
    if sig["take"] and wbar:
        s = 1 if sig["side"] == "long" else -1
        pnl_pts = s * (wbar["close"] - wbar["open"]) - COST_PTS
        settled = {"entry": wbar["open"], "exit": wbar["close"], "pnl_pts": round(pnl_pts, 1),
                   "pnl": round(pnl_pts * PV, 0),
                   "ret_pct": round((s * (wbar["close"] - wbar["open"]) / wbar["open"] - COST_PTS / wbar["open"]) * 100, 4)}
        _append_tape(wed_iso, tue, sig, d_put_oi, fx_dnet, s, settled)

    state = ("settled" if settled else ("signal_fired" if sig["take"] else "flat"))
    out = {"state": state, "settle_wed": wed_iso, "signal_tue": tue, "prev": mon,
           "d_put_oi": round(d_put_oi, 6), "fx_dnet": round(fx_dnet, 1),
           "z_put": round(sig["z_put"], 3), "z_fx": round(sig["z_fx"], 3), "zsum": round(sig["zsum"], 3),
           "side": sig["side"], "take": sig["take"], "tue_close": tue_close, "warm": sig["warm"],
           "put_tue": round(put_tue), "put_mon": round(put_mon),
           "put_wall": put_wall, "call_wall": call_wall,
           "thick": [[k, round(v)] for k, v in thick], "thin": [[k, round(v)] for k, v in thin],
           "call_thick": [[k, round(v)] for k, v in call_thick]}
    if settled:
        out.update(settled)
    NEXT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(out)
    print(f"[settlement_v2] {wed_iso} 訊號=tue {tue} d_put_oi={d_put_oi:+.4f} fx_dnet={fx_dnet:+.0f} "
          f"zsum={sig['zsum']:+.3f} → {sig['side']}" + (f" | 結算 {settled['pnl']:+.0f}元" if settled else ""))


def _append_tape(wed_iso, tue, sig, d_put_oi, fx_dnet, s, settled):
    DDIR.mkdir(parents=True, exist_ok=True)
    if TAPE.exists():
        with open(TAPE, encoding="utf-8") as f:
            if any(r.get("trade_date") == wed_iso for r in csv.DictReader(f)):
                return
    new = not TAPE.exists()
    with open(TAPE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["trade_date", "signal_date", "zsum", "d_put_oi", "fx_dnet", "sig", "side",
                        "entry", "exit", "pnl_pts", "pnl", "ret_pct", "year", "note"])
        w.writerow([wed_iso, tue, round(sig["zsum"], 4), round(d_put_oi, 6), round(fx_dnet, 1), s,
                    sig["side"], settled["entry"], settled["exit"], settled["pnl_pts"], settled["pnl"],
                    settled["ret_pct"], wed_iso[:4],
                    f"paper|v2|z|>={ZTHR}、Wed日OHLC開進收出、無止損、cost{COST_PTS}pt、MXF pv50"])
    # 實際寫入(非重複)才推「結算」TG
    ts = _tape_summary()
    cum = f" | 累積{ts[0]}筆 淨{ts[1]:+.0f} PF{ts[3]:.2f}" if ts else ""
    _tg(f"[settlement_v2] 結算 {wed_iso} 做{'多' if sig['side']=='long' else '空'} "
        f"進{settled['entry']:.0f}→出{settled['exit']:.0f} {settled['pnl']:+.0f}元"
        f"({settled['ret_pct']:+.2f}%) zsum{sig.get('zsum',0):+.2f}{cum}")


def _tape_summary():
    if not TAPE.exists():
        return None
    pnls = []
    with open(TAPE, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                pnls.append(float(r["pnl"]))
            except (KeyError, ValueError):
                pass
    if not pnls:
        return None
    n = len(pnls); wins = [x for x in pnls if x > 0]; gl = -sum(x for x in pnls if x < 0)
    pf = (sum(wins) / gl) if gl > 0 else float("inf")
    return n, sum(pnls), len(wins), pf


def write_report(o):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    wed = o["settle_wed"]
    verdict = {"long": "偏多 → 週三開盤做多 ▲", "short": "偏空 → 週三開盤做空 ▼",
               "flat": f"訊號不足(|zsum|={abs(o['zsum']):.2f}<{ZTHR})→ 空手"}[o["side"]]
    L = [f"---", f"title: settlement_v2 結算籌碼日報 {wed}", f"date: {wed}",
         f"tags: [籌碼, 每日報告, settlement_v2, 選擇權結算]", "---",
         f"# settlement_v2 — 週選結算日 / {wed}", "",
         "## 📌 今日結論", f"- **{verdict}**",
         f"- 訊號日(週二)= {o['signal_tue']};結算進出 = 週三 {wed} 08:45開→13:45收、1口 MXF、無止損", ""]
    if o.get("state") == "settled":
        L.append(f"- 本筆結算:進 {o.get('entry')} → 出 {o.get('exit')}、**{o.get('pnl'):+.0f} 元**({o.get('pnl_pts'):+.1f}pt)")
        L.append("")
    L += ["## 📊 籌碼明細(週二 vs 前一交易日)",
          "| 指標 | 值 | 說明 |", "|---|--:|---|",
          f"| d_put_oi | {o['d_put_oi']:+.4f} | 當週到期 TXO 總 Put OI 變化率(賣權牆變厚=正) |",
          f"| 總 Put OI | {o.get('put_mon',0):,.0f} → {o.get('put_tue',0):,.0f} | 前日→週二(淨 {o.get('put_tue',0)-o.get('put_mon',0):+,.0f} 口) |",
          f"| fx_dnet | {o['fx_dnet']:+.0f} 口 | 外資 TX 期貨淨OI 當日變化(加多=正) |",
          f"| 週二收盤 | {o.get('tue_close',0):.0f} | 大台 TX |", "",
          "## 🎯 訊號分數(expanding-z)",
          "| 因子 | z | |", "|---|--:|---|",
          f"| z(d_put_oi) | {o['z_put']:+.2f} | 賣權牆變化的標準分數 |",
          f"| z(fx_dnet) | {o['z_fx']:+.2f} | 外資加減碼的標準分數 |",
          f"| **zsum** | **{o['zsum']:+.3f}** | z_put+z_fx;**\\|zsum\\|≥{ZTHR} 才下單**、方向=sign |",
          f"| 暖身樣本 | {o['warm']} 筆 | expanding 用的過去筆數(≥40 即足) |", ""]
    spot = o.get("tue_close", 0) or 0
    thick = o.get("thick", [])
    if thick:
        L += [f"## 🧱 賣權牆變厚在哪個價位(ΔPut OI 增加 top;現價 ~{spot:.0f})",
              "| 履約價 | ΔPut OI(口) | 相對現價 |", "|---:|---:|---|"]
        for k, v in thick:
            rel = ("下方" if k < spot else "上方") + (f" {abs(k - spot) / spot * 100:.1f}%" if spot else "")
            L.append(f"| {k:.0f} | +{v:,.0f} | {rel} |")
        ks = [k for k, _ in thick]
        below = [k for k in ks if k < spot]
        L += ["", f"- **賣權牆變厚區間 ≈ {min(ks):.0f}–{max(ks):.0f}**;主支撐牆(週二最大 Put OI)= **{o.get('put_wall',0):.0f}**。",
              f"- 解讀:賣權牆增厚{'多集中在現價下方 → 下檔支撐增強' if len(below) >= len(ks) - 1 else '橫跨現價上下'}。", ""]
        if o.get("thin"):
            L += ["- 賣權變薄(top):" + "、".join(f"{k:.0f}({v:+,.0f})" for k, v in o["thin"]), ""]
    # 買權牆(阻力)
    call_thick = o.get("call_thick", [])
    if call_thick:
        L += [f"## 🧱 買權牆變厚在哪個價位(ΔCall OI 增加 top;現價 ~{spot:.0f})",
              "| 履約價 | ΔCall OI(口) | 相對現價 |", "|---:|---:|---|"]
        for k, v in call_thick:
            rel = ("上方" if k > spot else "下方") + (f" {abs(k - spot) / spot * 100:.1f}%" if spot else "")
            L.append(f"| {k:.0f} | +{v:,.0f} | {rel} |")
        cks = [k for k, _ in call_thick]
        L += ["", f"- **買權牆變厚區間 ≈ {min(cks):.0f}–{max(cks):.0f}**;主阻力牆(週二最大 Call OI)= **{o.get('call_wall',0):.0f}**。", ""]
    # 現價 vs 兩牆(進場位置參考;今天虧損正是 long 開在離阻力近、撞牆回洗)
    pw = o.get("put_wall", 0); cw = o.get("call_wall", 0)
    if pw and cw and spot:
        L += ["## 🧭 現價 vs 兩牆(進場位置參考)",
              f"- 主支撐(賣權牆)**{pw:.0f}** ←{(spot-pw)/spot*100:+.1f}%— 現價 **{spot:.0f}** —{(cw-spot)/spot*100:+.1f}%→ 主阻力(買權牆)**{cw:.0f}**",
              "- 做多較有利:現價靠近**賣權牆(支撐)**;做空較有利:現價靠近**買權牆(阻力)**。",
              "- ⚠️ 此為**參考資訊** —— 目前 v2 進場只看 zsum、**未**用離牆距離(那是 lab 待 OOS 驗的改法)。", ""]
    ts = _tape_summary()
    if ts:
        n, net, w, pf = ts
        L += ["## 累積 paper tape(小台 pv50、1口、無止損、cost 4pt)",
              f"- {n} 筆 淨 {net:+.0f} 元 勝 {w}/{n} PF {pf:.2f}", ""]
    L += ["## 📖 名詞解釋",
          "- **d_put_oi**:當週到期賣權(Put)總未平倉量,週二相對前日的變化率。賣權牆變厚(正)常代表下檔支撐/偏多訊號之一。",
          "- **fx_dnet**:外資台指期(TX)淨未平倉(多−空)當日變化。正=外資加多。",
          "- **zsum**:兩個特徵各自做 expanding 標準分數後相加;只有 |zsum|≥0.7(兩訊號強烈同向)才進場,方向取正負號。",
          "- **無止損**:研究實測固定/移動止損都傷績效(結算日盤中洗動),edge 在開盤→收盤整段。",
          "", "---", "相關: [[strategy_settlement_v2]] · [[handoff_settlement_v2_live_2026_06_10]]"]
    (REPORT_DIR / f"{wed}.md").write_text("\n".join(L), encoding="utf-8")


def verify():
    """用 feature_history 最後一筆:重抓 live 算 d_put_oi/fx_dnet,對比存檔值,驗證算法一致。"""
    feat = load_feat()
    if not feat:
        print("無 feature_history 可驗"); return
    last = feat[-1]
    wed = date.fromisoformat(last["settle_wed"])
    net = fetch_foreign_net((wed - timedelta(days=20)).isoformat(), wed.isoformat())
    tue, mon = resolve_dates(wed.isoformat(), net)
    fx_live = net[tue] - net[mon] if (tue and mon) else None
    put_tue = fetch_put_oi_total(tue, wed); time.sleep(0.3); put_mon = fetch_put_oi_total(mon, wed)
    dpo_live = (put_tue - put_mon) / (put_tue + 1) if put_tue > 0 else None
    print(f"=== 對帳 {last['settle_wed']}(signal_tue 存={last['signal_tue']} / live={tue}) ===")
    print(f"d_put_oi  存={last['d_put_oi']:+.4f}  live={dpo_live if dpo_live is None else round(dpo_live,4):}  "
          f"{'' if dpo_live is None else ('✓' if abs(dpo_live-last['d_put_oi'])<0.02 else '✗ 差異大')}")
    print(f"fx_dnet   存={last['fx_dnet']:+.0f}  live={fx_live if fx_live is None else round(fx_live,0)}  "
          f"{'' if fx_live is None else ('✓' if abs(fx_live-last['fx_dnet'])<500 else '✗ 差異大')}")


if __name__ == "__main__":
    main()
