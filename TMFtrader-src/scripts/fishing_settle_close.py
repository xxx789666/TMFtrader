# -*- coding: utf-8 -*-
"""魅影結算平腿器(2026-08-02,A案三件套之三;cron 13:26 TST 平日)。

背景:requote live 成交後即退場,選擇權腿 13:30 現金結算消失,但 MXF 對沖腿不會自動平
→ 沒人平就變裸方向倉。本器讀外部部位登記簿(external_positions.json),
今日到期的組合 → 13:26 市價反向平掉 MXF 腿 → TG 落帳 → 從登記簿移除
(core 引擎的白名單同步縮小)。

無到期登記 → 不登入直接退出(零連線成本)。失敗 → 重試 1 次 → 🆘 TG(裸倉警報,人工平)。
"""
import os
import sys
import json
import time
import threading
import datetime as dt
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(__file__).resolve().parent.parent
EXT_POS_FILE = ROOT / "data" / "external_positions.json"
os.environ.setdefault("TZ", "Asia/Taipei")
try:
    time.tzset()
except Exception:
    pass


def log(m):
    print(f"[{dt.datetime.now():%F %T}] {m}", flush=True)


def tg(msg):
    try:
        import urllib.request
        hook = os.environ.get("DISCORD_WEBHOOK_FISHING", "")
        if not hook:
            return
        data = json.dumps({"username": "魅影結算平腿", "content": msg}).encode()
        urllib.request.urlopen(urllib.request.Request(
            hook, data=data, headers={"Content-Type": "application/json",
                                      "User-Agent": "Mozilla/5.0"}), timeout=10)
    except Exception:
        pass


def load_registry():
    try:
        return json.loads(EXT_POS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_registry(d):
    EXT_POS_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def main():
    today = dt.date.today().isoformat()
    reg = load_registry()
    due = [e for e in reg.get("fishing_requote", []) if str(e.get("expiry", "")) == today]
    if not due:
        log(f"無今日({today})到期登記倉 → 收工(未登入)")
        return

    log(f"今日到期 {len(due)} 筆:{[e['opt_code'] for e in due]} → 登入平腿")
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import shioaji as sj
    from shioaji import constant as sjc

    deal_evt = {}
    api = sj.Shioaji(simulation=False)
    api.login(api_key=os.environ["SHIOAJI_API_KEY"], secret_key=os.environ["SHIOAJI_SECRET_KEY"],
              contracts_timeout=30000)
    api.activate_ca(ca_path=os.environ["SHIOAJI_CA_PATH"], ca_passwd=os.environ["SHIOAJI_CA_PASSWORD"],
                    person_id=os.environ["SHIOAJI_PERSON_ID"])

    def cb(stat, msg):
        try:
            if "Deal" in str(stat):
                seq = msg.get("seqno", "")
                if seq in deal_evt:
                    evt, box = deal_evt[seq]
                    box.append(float(msg.get("price", 0) or 0))
                    evt.set()
        except Exception:
            pass
    api.set_order_callback(cb)

    try:
        for e in due:
            code = e["code"]
            # 反向平倉:登記 Sell(空) → Buy 回補;登記 Buy → Sell
            action = sjc.Action.Buy if e["direction"] == "Sell" else sjc.Action.Sell
            mxf = next((c for c in api.Contracts.Futures.MXF if c.code == code), None)
            if mxf is None:
                log(f"🆘 找不到合約 {code}!")
                tg(f"🆘 **結算平腿失敗:找不到合約 {code}** — 立即人工平 {e['direction']} 反向 x{e['qty']}!")
                continue
            ok = False
            for attempt in (1, 2):
                order = api.Order(price=0, quantity=int(e["qty"]), action=action,
                                  price_type=sjc.FuturesPriceType.MKT, order_type=sjc.OrderType.IOC,
                                  octype=sjc.FuturesOCType.Auto, account=api.futopt_account)
                tr = api.place_order(mxf, order)
                evt, box = threading.Event(), []
                deal_evt[tr.order.seqno] = (evt, box)
                got = evt.wait(3.0)
                deal_evt.pop(tr.order.seqno, None)
                if got and box:
                    px = box[0]
                    hedge_px = float(e.get("hedge_px", 0) or 0)
                    fut_pnl = (hedge_px - px) if e["direction"] == "Sell" else (px - hedge_px)
                    log(f"✅ 平腿 {code} {action} x{e['qty']} @{px}(期貨腿損益 {fut_pnl:+.0f} 點)")
                    tg(f"📗 **結算平腿完成** {e['opt_code']} 的 MXF 腿 @{px}\n"
                       f"期貨腿 {fut_pnl:+.0f} 點;選擇權腿由期交所 13:30 現金結算;"
                       f"組合鎖定值以 fills 帳為準")
                    ok = True
                    break
                log(f"⚠️ 平腿第 {attempt} 次無成交回報({code})")
                time.sleep(1.0)
            if ok:
                reg["fishing_requote"] = [x for x in reg.get("fishing_requote", [])
                                          if x.get("opt_code") != e["opt_code"]]
                save_registry(reg)
            else:
                tg(f"🆘 **結算平腿失敗={code} 裸倉!** {e['opt_code']} 的對沖腿({e['direction']} x{e['qty']})"
                   f"未能回補 — **立即人工市價平倉!**(登記簿保留,人工平完後手動移除該筆)")
    finally:
        try:
            api.logout()
        except Exception:
            pass
    log("收工")


if __name__ == "__main__":
    main()
