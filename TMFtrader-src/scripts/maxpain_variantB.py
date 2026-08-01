# -*- coding: utf-8 -*-
"""maxpain 變體B tape(2026-07-23 user 定):出訊號「當日夜盤開盤(15:00)」進第 1 口(單口、無加碼),
結算出場同主版(exit_px 抄主 tape settle 價)。主版=次日 08:45 進 → A/B 純測「早一晚進場」的 edge。

資料源:TAIFEX futDailyMarketReport marketCode=1(盤後時段)TX 純月份量最大契約開盤價。
⚠️ 夜盤 queryDate=「結束日」(T 日 15:00 起的夜盤查 T 的次一日),try 候選日、抓不到 fail-closed 留 pending。
⚠️ 已知口徑警語:T+0 OI 訊號 ~15:10 才可得,夜盤開盤 15:00 有 ~10 分 look-ahead;此為紀錄用變體,
   若未來要轉真錢,必須改「15:15 後首個可成交價」重驗。
口徑同主版:TX 價位、每點 50 元(MXF 1 口)、無滑價。
cron:平日 19:05 TST(11:05 UTC,maxpain_daily 18:40 之後);冪等、重跑安全。
"""
import os, re, csv, json, urllib.request, urllib.parse
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
MAIN = "data/maxpain_v2/decisions.csv"
SIG = "data/maxpain_v2/next_signal.json"
OUT = "data/maxpain_v2/decisions_variantB.csv"
REPORT_DIR = "data/reports/maxpain"
PAGE = "https://www.taifex.com.tw/cht/3/futDailyMarketReport"
COLS = ["signal_t", "expiry_ed", "side", "entry_night_of", "entry_px",
        "exit_date", "exit_px", "pnl_pts", "pnl", "status", "note"]
START = "2026-07-22"        # 起算訊號日;不回填更早歷史(避免整條 tape 都是 hindsight)
PT_VALUE = 50               # MXF 1 口

def fetch_night_open(end_iso):
    """盤後時段(marketCode=1)TX 量最大純月份契約開盤價;queryDate=夜盤結束日。回 float 或 None。"""
    body = urllib.parse.urlencode({"queryType": "2", "marketCode": "1", "commodity_id": "TX",
                                   "queryDate": end_iso.replace("-", "/"),
                                   "MarketCode": "1", "commodity_idt": "TX"}).encode()
    try:
        req = urllib.request.Request(PAGE, data=body, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            t = r.read().decode("utf-8", "replace")
        if end_iso.replace("-", "/") not in t:
            return None
        hdr, idx, best = None, {}, None
        for m in re.finditer(r"<tr[^>]*>(.*?)</tr>", t, re.S | re.I):
            c = [re.sub(r"<[^>]+>", "", x).strip().replace(",", "")
                 for x in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", m.group(1), re.S | re.I)]
            if not c:
                continue
            if hdr is None and any("開盤" in x for x in c):
                hdr = [re.sub(r"[\s*]", "", x) for x in c]
                for name, key in (("開盤價", "o"), ("成交量", "v")):
                    for i, x in enumerate(hdr):
                        if name in x:
                            idx[key] = i
                            break
                if "o" not in idx:
                    return None                      # 表頭改版 → fail-closed
                continue
            if hdr is None or c[0] != "TX" or len(c) != len(hdr):
                continue
            if not re.fullmatch(r"\d{6}", c[1].strip()):    # 只留純月份,排除價差/週契約
                continue
            try:
                o = float(c[idx["o"]])
                v = float(c[idx["v"]] or 0) if "v" in idx else 0.0
            except (ValueError, TypeError):
                continue
            if o <= 0:
                continue
            if best is None or v > best[0]:
                best = (v, o)
        return best[1] if best else None
    except Exception as e:
        print(f"  [night_open] {end_iso} 失敗: {e}")
        return None

def night_open_for_signal(t_iso):
    """T 日 15:00 起的夜盤 → 結束日=次一(交易)日;try T+1..T+4。回 (open, 用的查詢日) 或 (None, None)。"""
    t = date.fromisoformat(t_iso)
    for k in range(1, 5):
        q = (t + timedelta(days=k)).isoformat()
        o = fetch_night_open(q)
        if o:
            return o, q
    return None, None

def main():
    rows = []
    if os.path.exists(OUT):
        rows = list(csv.DictReader(open(OUT, encoding="utf-8")))
    have = {r["signal_t"] for r in rows}

    main_rows = []
    if os.path.exists(MAIN):
        main_rows = list(csv.DictReader(open(MAIN, encoding="utf-8")))
    main_by_sig = {r["signal_t"]: r for r in main_rows}

    # 1) 收訊號:主 tape 既有列 + 當前 next_signal
    cands = [(r["signal_t"], r.get("expiry_ed", "")) for r in main_rows if r["signal_t"] >= START]
    try:
        sig = json.load(open(SIG, encoding="utf-8"))
        if sig.get("state") in ("signal_fired", "open") and str(sig.get("signal_t", "")) >= START:
            cands.append((sig["signal_t"], sig.get("ed", "")))
    except Exception:
        pass
    for t_iso, ed in cands:
        if t_iso not in have:
            rows.append(dict(signal_t=t_iso, expiry_ed=ed, side="long", entry_night_of=t_iso,
                             entry_px="", exit_date="", exit_px="", pnl_pts="", pnl="",
                             status="pending", note="夜盤開盤進1口(變體B)"))
            have.add(t_iso)
            print(f"  新訊號列 {t_iso} (ed {ed})")

    today = date.today().isoformat()
    for r in rows:
        # 2) 補 entry:訊號日夜盤已結束(今天>訊號日)才抓
        if not r["entry_px"] and r["signal_t"] < today:
            o, q = night_open_for_signal(r["signal_t"])
            if o:
                r["entry_px"] = f"{o:.0f}"
                r["status"] = "open"
                r["note"] += f";夜開來源queryDate={q}"
                print(f"  {r['signal_t']} 夜盤開盤={o:.0f} (查{q})")
            else:
                print(f"  {r['signal_t']} 夜開尚抓不到,保持 pending")
        # 3) 補 exit:主 tape 同訊號已結算 → 抄 exit_px
        if r["entry_px"] and not r["exit_px"]:
            mr = main_by_sig.get(r["signal_t"])
            if mr and mr.get("exit_px"):
                r["exit_date"] = mr["exit_date"]
                r["exit_px"] = mr["exit_px"]
                pts = float(mr["exit_px"]) - float(r["entry_px"])
                r["pnl_pts"] = f"{pts:.1f}"
                r["pnl"] = f"{pts * PT_VALUE:.0f}"
                r["status"] = "settled"
                print(f"  {r['signal_t']} 結算 {mr['exit_px']} → {pts:+.1f} 點")

    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        w.writerows({k: r.get(k, "") for k in COLS} for r in rows)
    print(f"變體B tape: {len(rows)} 列 → {OUT}")

    # 4) 報告 MD 加段(最新一份;冪等:已有標記就跳過)
    try:
        mds = sorted(f for f in os.listdir(REPORT_DIR) if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.md", f))
        if mds:
            p = os.path.join(REPORT_DIR, mds[-1])
            txt = open(p, encoding="utf-8").read()
            if "## 🌙 變體B" not in txt:
                st = [r for r in rows if r["status"] == "settled"]
                pn = [float(r["pnl"]) for r in st]
                lines = ["", "## 🌙 變體B(訊號日夜盤開盤進1口,單口無加碼)",
                         f"- 累積 {len(rows)} 訊號(結算 {len(st)}、在途/待補 {len(rows)-len(st)})"
                         + (f",結算淨 {sum(pn):+,.0f} 元" if pn else "")]
                for r in rows[-3:]:
                    lines.append(f"- {r['signal_t']}: 夜開 {r['entry_px'] or '待補'}"
                                 + (f" → {r['exit_date']} 出 {r['exit_px']} = {r['pnl']}元" if r["exit_px"]
                                    else f" ({r['status']})"))
                lines.append("- ⚠️ 口徑:T+0 OI 訊號~15:10 可得 vs 夜開 15:00,有~10分 look-ahead;紀錄用,轉真錢前改 15:15 後首價重驗")
                open(p, "a", encoding="utf-8").write("\n".join(lines) + "\n")
                print(f"報告已加段: {p}")
    except Exception as e:
        print(f"報告加段失敗(非致命): {e}")

if __name__ == "__main__":
    main()
