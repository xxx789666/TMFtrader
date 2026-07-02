#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""maxpain_put — maxpain v2 + 保護性 put(−2%) 平行 paper tape(交接 2026-07-02)。

訊號/期貨腿 100% 沿用 maxpain_exec 引擎(凍結 v2),本腳本零引擎改動,只做兩件事:

--watch(早盤 cron 08:43 TST,僅相關日才登入):
  盯 data/maxpain_v2/whatif_pending.json(引擎真實進場當下建立、含 s1/ed/scaled 鏡像)——
  ① 偵測到進場 → 立即 snapshot 同週 TXO put(最近 ≤ E1×(1−DEPTH) 檔)記 bid/ask/mid,
     paper 成交=吃 ask(保守;無 ask 退 mid、無報價改掛低一檔,最多退 2 檔並記 note)。
  ② 偵測到 +1% 加碼鏡像(wf.scaled;注意=「無停損世界」的加碼,真倉停損後仍會鏡像→正確)
     → 再 snapshot 第 2 張 put(≤ E2×(1−DEPTH),高一檔)。
  非訊號日/已雙腿齊 → 不登入直接退出(零連線成本;相關日 ~2-4 天/月)。

--finalize(收盤後 cron 18:52 TST):
  結算日後讀 data/maxpain_v2/whatif.csv 該筆:
    fut_pnl = noSL 變體(無停損抱結算=put 版期貨腿)、v2_same_signal = noTP 變體(−2%停+抱結算=凍結 v2 口徑)。
  settle_S 先抓 TAIFEX 最後結算價(研究§9:不可用收盤當結算),抓不到退 noSL 出場 tick 價(note 標 proxy)。
  put payoff = max(K−S,0)−成本(現金結算不平倉)。寫一列 data/paper/maxpain_put/decisions.csv(交接§4 欄位)。

鐵則:凍結 v2 tape(whatif.csv/引擎 tape)只讀不寫;本線是平行帳,不是取代。
"""
import argparse
import csv
import json
import os
import re
import sys
import time as _time
from datetime import date, datetime, time as dtime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("TZ", "Asia/Taipei")
try:
    _time.tzset()                                   # VPS 系統時鐘 UTC → 本腳本一律 TST
except AttributeError:
    pass                                            # Windows 本機測試無 tzset

SIG = ROOT / "data" / "maxpain_v2" / "next_signal.json"
WFP = ROOT / "data" / "maxpain_v2" / "whatif_pending.json"
WFT = ROOT / "data" / "maxpain_v2" / "whatif.csv"
OUTD = ROOT / "data" / "paper" / "maxpain_put"
PEND = OUTD / "put_pending.json"
TAPE = OUTD / "decisions.csv"
HOLIDAYS = ROOT / "scripts" / "market_holidays.txt"

DEPTH = float(os.environ.get("MAXPAIN_PUT_DEPTH", 0.02))   # put 深度(使用者 2026-07-02 定案 −2%)
PV = 50.0                                                  # TXO/MXF 都是 50 元/點 → 1:1
POLL_S = 3.0
ENTRY_GIVEUP = dtime(9, 35)     # 引擎進場窗 08:45-09:30;09:35 還沒進=今天沒單
WATCH_END = dtime(13, 46)       # 加碼可整段持有期觸發 → 持倉未加碼日盯到收盤
MAX_STRIKE_FALLBACK = 2         # 目標檔無報價 → 往低一檔改掛(交接§5-2),最多退 2 檔

COLS = ["signal_t", "entry_t1", "E_fill", "K_put", "put_bid", "put_ask", "put_fill",
        "added", "E2", "K_put2", "put2_fill", "expiry_ed", "settle_S",
        "fut_pnl_pts", "put_pnl_pts", "total_pts", "total_ntd", "v2_same_signal_ntd", "note"]


def log(msg):
    print(f"[{datetime.now():%F %T}] {msg}", flush=True)


def tg(msg):
    """TG 全事件覆蓋(斷線/進場/出場都推;失敗不擋主流程)。"""
    tok = os.environ.get("TG_BOT_TOKEN", "").strip()
    cid = os.environ.get("TG_CHAT_ID", "").strip()
    if not tok or not cid:
        return
    try:
        import requests
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": cid, "text": f"[maxpain_put] {msg}"}, timeout=15)
    except Exception as e:
        log(f"TG 推送失敗(不擋流程): {e}")


def is_holiday(d: date) -> bool:
    try:
        days = {ln.strip() for ln in HOLIDAYS.read_text(encoding="utf-8").splitlines()
                if ln.strip() and not ln.lstrip().startswith("#")}
        return d.isoformat() in days
    except OSError:
        return False


def jload(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def jsave(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------- watch(早盤) ----------------
def _need_watch(today: date):
    """回傳 (need, reason)。不需要就不登入(零連線成本)。"""
    pend = jload(PEND)
    if pend and not pend.get("added"):
        try:
            if date.fromisoformat(pend["ed"]) >= today:
                return True, f"持倉未加碼(訊號 {pend['signal_t']}),盯 +1% 鏡像"
        except (KeyError, ValueError):
            pass
    if pend and pend.get("added"):
        return False, "雙腿已齊,等結算 finalize"
    if jload(WFP):                                  # 引擎已進場但 put1 還沒記(watcher 晚到/前日漏)
        return True, "whatif_pending 在而 put 未記 → 補記進場 put"
    sig = jload(SIG) or {}
    if sig.get("state") == "signal_fired" and sig.get("side") == "long":
        try:
            d_sig = date.fromisoformat(sig["signal_t"])
            if today > d_sig and (today - d_sig).days <= 4:
                return True, f"訊號日 {sig['signal_t']} → 今天可能進場"
        except (KeyError, ValueError):
            pass
    return False, "非訊號日/無持倉"


def _login():
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import shioaji as sj
    api = sj.Shioaji(simulation=True)
    api.login(api_key=os.environ["SHIOAJI_API_KEY"], secret_key=os.environ["SHIOAJI_SECRET_KEY"],
              contracts_timeout=30000)
    return api


def _puts_for_ed(api, ed_iso: str) -> dict:
    """該結算週三到期的所有 TXO 系週選/月選 put:{strike: contract}。
    以 delivery_date 比對(涵蓋 TXO 月選與 TX1/TX2/TX4/TX5 週選;結算週=月選本身)。"""
    import shioaji as sj
    target = date.fromisoformat(ed_iso).strftime("%Y/%m/%d")
    out = {}
    opt_root = api.Contracts.Options
    try:
        cats = list(opt_root)
    except TypeError:
        cats = [getattr(opt_root, n) for n in dir(opt_root) if not n.startswith("_")]
    for cat in cats:
        try:
            members = list(cat)
        except TypeError:
            continue
        for c in members:
            if getattr(c, "delivery_date", "") != target:
                continue
            if getattr(c, "option_right", None) != sj.constant.OptionRight.Put:
                continue
            try:
                out[float(c.strike_price)] = c
            except (TypeError, ValueError):
                continue
    return out


def _snap_put(api, puts: dict, ref_px: float, tag: str):
    """挑最近 ≤ ref×(1−DEPTH) 的 put 檔,snapshot bid/ask。無報價往低一檔退(≤2 檔)。
    回傳 dict(K, bid, ask, fill, note) 或 None。"""
    target = ref_px * (1 - DEPTH)
    ks = sorted([k for k in puts if k <= target], reverse=True)
    if not ks:
        return None
    note = ""
    for i, k in enumerate(ks[:1 + MAX_STRIKE_FALLBACK]):
        try:
            s = api.snapshots([puts[k]])[0]
            bid = float(getattr(s, "buy_price", 0) or 0)
            ask = float(getattr(s, "sell_price", 0) or 0)
        except Exception as e:
            log(f"{tag} snapshot {k} 失敗: {e}")
            bid = ask = 0.0
        if ask > 0 or bid > 0:
            if i > 0:
                note = f"目標檔無報價退{i}檔;"
            spread = (ask - bid) if (ask > 0 and bid > 0) else None
            if spread is not None and spread > 15:
                note += f"spread{spread:.0f}點>15;"
            fill = ask if ask > 0 else round((bid + ask) / 2.0, 1)
            if ask <= 0:
                note += "無ask用mid;"
            return dict(K=k, bid=bid, ask=ask, fill=fill, note=note)
    return None


def watch():
    today = date.today()
    if is_holiday(today):
        log("市場休市 → 跳過"); return
    need, why = _need_watch(today)
    log(f"watch 判定: need={need} ({why})")
    if not need:
        return
    try:
        api = _login()
    except Exception as e:
        log(f"登入失敗: {e}"); tg(f"⚠️ watch 登入失敗: {e}"); return
    try:
        puts_cache = {}                             # ed -> {strike: contract}
        while datetime.now().time() < WATCH_END:
            pend = jload(PEND)
            wf = jload(WFP)
            now_t = datetime.now().time()
            if pend and pend.get("added"):
                log("雙腿已齊 → 收工"); break
            if not pend and not wf:
                if now_t > ENTRY_GIVEUP:
                    log("09:35 無進場(引擎窗已過) → 收工"); break
                _time.sleep(POLL_S); continue
            # ① 進場 put(第 1 張)
            if not pend and wf:
                e1 = float(wf.get("s1") or 0)
                ed = wf.get("ed") or ""
                if e1 <= 0 or not ed:
                    _time.sleep(POLL_S); continue
                if ed not in puts_cache:
                    puts_cache[ed] = _puts_for_ed(api, ed)
                    log(f"週選 put 鏈(ed={ed}): {len(puts_cache[ed])} 檔")
                r = _snap_put(api, puts_cache[ed], e1, "put1")
                if r is None:
                    log(f"E1={e1:.0f} 找不到可報價的 put 檔(≤{e1*(1-DEPTH):.0f}),{POLL_S}s 後重試")
                    tg(f"⚠️ put1 無可用報價(E1={e1:.0f}),重試中")
                    _time.sleep(10); continue
                pend = dict(signal_t=wf.get("signal_t"), ed=ed,
                            entry_t1=wf.get("entry_t"), E_fill=e1,
                            K_put=r["K"], put_bid=r["bid"], put_ask=r["ask"], put_fill=r["fill"],
                            put1_quote_t=datetime.now().isoformat(timespec="seconds"),
                            added=0, E2="", K_put2="", put2_bid="", put2_ask="", put2_fill="",
                            note=r["note"])
                jsave(PEND, pend)
                log(f"put1 記錄: K={r['K']:.0f} bid={r['bid']} ask={r['ask']} fill={r['fill']} {r['note']}")
                tg(f"🛡️ 進場 put1: E1={e1:.0f} → 買 {r['K']:.0f}P@{r['fill']}(bid {r['bid']}/ask {r['ask']}) "
                   f"ed={ed} {r['note']}")
                continue
            # ② 加碼 put(第 2 張;鏡像=無停損世界的 +1%)
            if pend and not pend.get("added") and wf and wf.get("scaled"):
                e2 = float(wf.get("s1") or 0) * (1 + 0.01)
                ed = pend["ed"]
                if ed not in puts_cache:
                    puts_cache[ed] = _puts_for_ed(api, ed)
                r = _snap_put(api, puts_cache[ed], e2, "put2")
                if r is None:
                    log(f"E2={e2:.0f} 找不到可報價的 put 檔,10s 後重試")
                    _time.sleep(10); continue
                pend.update(added=1, E2=round(e2, 1), K_put2=r["K"],
                            put2_bid=r["bid"], put2_ask=r["ask"], put2_fill=r["fill"],
                            put2_quote_t=datetime.now().isoformat(timespec="seconds"),
                            note=(pend.get("note", "") + r["note"]))
                jsave(PEND, pend)
                log(f"put2 記錄: K={r['K']:.0f} fill={r['fill']} {r['note']}")
                tg(f"🛡️ 加碼 put2: E2≈{e2:.0f} → 買 {r['K']:.0f}P@{r['fill']}(bid {r['bid']}/ask {r['ask']})")
                continue
            _time.sleep(POLL_S)
    finally:
        try:
            api.logout()
        except Exception:
            pass
    log("watch 結束")


# ---------------- finalize(收盤後) ----------------
def _fetch_taifex_fsp(ed_iso: str):
    """TAIFEX 台指選擇權最後結算價(研究§9:結算≠收盤)。抓不到回 None(fallback 用 tick proxy)。"""
    try:
        import requests
        ed = date.fromisoformat(ed_iso)
        url = "https://www.taifex.com.tw/cht/5/optIndxFSP"
        html = requests.get(url, timeout=30).text
        # 表格列 = 日期TD | 契約月份TD(如 202607W1) | TXO結算價TD(align=right,第一格) | 電選 | 金選。
        # 同一結算日 TXO 週選/月選共用同一最後結算價 → 取該日第一個非 '-' 的 TXO 格即可。
        key = ed.strftime("%Y/%m/%d")
        i = html.find(key)
        while i != -1:
            seg = html[i:i + 600]
            m = re.search(r"align=right>\s*([\d,]+(?:\.\d+)?)\s*<", seg)
            if m:
                return float(m.group(1).replace(",", ""))
            i = html.find(key, i + 1)
    except Exception as e:
        log(f"TAIFEX FSP 抓取失敗(用 proxy): {e}")
    return None


def finalize():
    today = date.today()
    pend = jload(PEND)
    if not pend:
        log("無 pending → 無事"); return
    try:
        ed = date.fromisoformat(pend["ed"])
    except (KeyError, ValueError):
        log(f"pending ed 壞: {pend}"); return
    if today < ed:
        log(f"未到結算日({ed}) → 等"); return
    # 期貨腿:whatif.csv 的 noSL(無停損抱結算)/noTP(凍結 v2 口徑)
    row = None
    try:
        with open(WFT, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                if r.get("signal_t") == pend.get("signal_t"):
                    row = r                          # 同訊號取最後一列
    except OSError:
        pass
    if not row or not str(row.get("noSL_pnl", "")).strip():
        log("whatif.csv 尚無該筆 noSL 結果(引擎變體未全出場?) → 明天再試"); return
    fut_pnl_pts = float(row["noSL_pnl"]) / PV
    v2_ntd = float(row["noTP_pnl"]) if str(row.get("noTP_pnl", "")).strip() else ""
    # 結算價:TAIFEX 最後結算價優先、退 noSL 出場 tick(13:30 proxy)
    settle_S = _fetch_taifex_fsp(pend["ed"])
    note = pend.get("note", "")
    if settle_S is None:
        settle_S = float(row["noSL_px"])
        note += "settle=tick_proxy(13:30);"
    else:
        note += "settle=TAIFEX_FSP;"
    # put 腿:現金結算(不平倉)
    put_pnl = max(float(pend["K_put"]) - settle_S, 0.0) - float(pend["put_fill"])
    if pend.get("added"):
        put_pnl += max(float(pend["K_put2"]) - settle_S, 0.0) - float(pend["put2_fill"])
    total_pts = fut_pnl_pts + put_pnl
    out = dict(signal_t=pend["signal_t"], entry_t1=pend["entry_t1"], E_fill=pend["E_fill"],
               K_put=pend["K_put"], put_bid=pend["put_bid"], put_ask=pend["put_ask"],
               put_fill=pend["put_fill"], added=pend.get("added", 0),
               E2=pend.get("E2", ""), K_put2=pend.get("K_put2", ""), put2_fill=pend.get("put2_fill", ""),
               expiry_ed=pend["ed"], settle_S=round(settle_S, 1),
               fut_pnl_pts=round(fut_pnl_pts, 1), put_pnl_pts=round(put_pnl, 1),
               total_pts=round(total_pts, 1), total_ntd=round(total_pts * PV),
               v2_same_signal_ntd=v2_ntd, note=note)
    TAPE.parent.mkdir(parents=True, exist_ok=True)
    new = not TAPE.exists()
    with open(TAPE, "a", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        if new:
            w.writeheader()
        w.writerow(out)
    try:
        PEND.unlink()
    except OSError:
        pass
    diff = (total_pts * PV - float(v2_ntd)) if v2_ntd != "" else float("nan")
    log(f"結算落帳: fut(noSL) {fut_pnl_pts:+.1f}pts + put {put_pnl:+.1f}pts = {total_pts:+.1f}pts "
        f"(NT${total_pts*PV:+,.0f}) | v2同訊號 NT${v2_ntd} 差 {diff:+,.0f}")
    tg(f"📗 結算 {pend['signal_t']}: put版 {total_pts:+.1f}點(NT${total_pts*PV:+,.0f}) "
       f"vs v2 NT${v2_ntd}(差 {diff:+,.0f}) settle={settle_S:.0f} {note}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--finalize", action="store_true", help="收盤後結算落帳(預設=早盤 watch)")
    a = ap.parse_args()
    if a.finalize:
        finalize()
    else:
        watch()
