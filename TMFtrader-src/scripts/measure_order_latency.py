# -*- coding: utf-8 -*-
"""實測下單/撤單延遲(2026-07-23,釣魚真錢門檻項目1)。只能在 VPS 跑(帳號 IP 鎖)。

方法:挑當月 TXO 深價外 put(F-3000 附近、ask>=1.0),掛限價買 0.1(遠低於任何買盤,
實務不可能成交;萬一成交成本=5 元/口)→ 量測:
  place_ret  = place_order() 同步返回(API 伺服器來回)
  place_ack  = 交易所回報 callback(op New, op_code 00)
  cancel_ret / cancel_ack = 撤單同款
重掛迴圈延遲 ≈ cancel_ack + place_ack。10 輪取分佈。
安全:qty=1、價 0.1 寫死、上限 10 張、結束撤光+驗無殘單、finally logout(防連線 leak);
需 env CONFIRM_LIVE_ORDER=YES 才跑。
"""
import os, sys, time, statistics, threading
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.stdout.reconfigure(encoding="utf-8")
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

if os.environ.get("CONFIRM_LIVE_ORDER") != "YES":
    sys.exit("需 CONFIRM_LIVE_ORDER=YES(真錢帳號下測試單)")

import shioaji as sj

ROUNDS = 10
PRICE = 0.1
api = sj.Shioaji(simulation=False)
ack = {"evt": threading.Event(), "want": None, "t": None}

def order_cb(stat, msg):
    try:
        if "Order" not in str(stat):
            return
        op = msg.get("operation", {})
        seq = msg.get("order", {}).get("seqno", "")
        if seq == ack["want"] and op.get("op_code") == "00":
            ack["t"] = time.perf_counter()
            ack["evt"].set()
    except Exception:
        pass

try:
    api.login(api_key=os.environ["SHIOAJI_API_KEY"], secret_key=os.environ["SHIOAJI_SECRET_KEY"],
              contracts_timeout=30000)
    api.activate_ca(ca_path=os.environ["SHIOAJI_CA_PATH"],
                    ca_passwd=os.environ["SHIOAJI_CA_PASSWORD"],
                    person_id=os.environ["SHIOAJI_PERSON_ID"])
    api.set_order_callback(order_cb)
    print("登入+CA OK")

    # 挑當月 TXO 深價外 put:F-3000 附近、有報價
    fsnap = api.snapshots([api.Contracts.Futures.TXF.TXFR1])[0]
    F = float(fsnap.close)
    months = sorted({c.delivery_month for c in api.Contracts.Options.TXO})
    mon = months[0]
    puts = [c for c in api.Contracts.Options.TXO
            if c.delivery_month == mon and str(c.option_right).endswith("Put")
            and c.strike_price <= F - 2500 and c.strike_price >= F - 4000]
    puts.sort(key=lambda c: -c.strike_price)
    target = None
    for c in puts[:6]:
        s = api.snapshots([c])[0]
        if float(s.sell_price or 0) >= 1.0:
            target = c
            print(f"標的 {c.code} K{c.strike_price:.0f} ask={s.sell_price} (F={F:.0f})")
            break
    if target is None:
        sys.exit("找不到 ask>=1.0 的深價外 put,收工(不硬掛)")

    pr, pa, cr, ca_ = [], [], [], []
    for i in range(ROUNDS):
        ack["evt"].clear(); ack["want"] = None; ack["t"] = None
        order = api.Order(price=PRICE, quantity=1, action=sj.constant.Action.Buy,
                          price_type=sj.constant.FuturesPriceType.LMT,
                          order_type=sj.constant.OrderType.ROD,
                          octype=sj.constant.FuturesOCType.New,
                          account=api.futopt_account)
        t0 = time.perf_counter()
        trade = api.place_order(target, order)
        t1 = time.perf_counter()
        ack["want"] = trade.order.seqno
        got = ack["evt"].wait(10)
        t_ack = (ack["t"] - t0) if got and ack["t"] else None
        pr.append(t1 - t0)
        if t_ack: pa.append(t_ack)

        ack["evt"].clear(); ack["t"] = None
        t2 = time.perf_counter()
        api.cancel_order(trade)
        t3 = time.perf_counter()
        got = ack["evt"].wait(10)
        t_cack = (ack["t"] - t2) if got and ack["t"] else None
        cr.append(t3 - t2)
        if t_cack: ca_.append(t_cack)
        print(f"  R{i+1}: place ret {1000*(t1-t0):.0f}ms ack {('%.0f' % (1000*t_ack)) if t_ack else 'NA'}ms"
              f" | cancel ret {1000*(t3-t2):.0f}ms ack {('%.0f' % (1000*t_cack)) if t_cack else 'NA'}ms")
        time.sleep(1.5)

    def st(v):
        return f"中位 {statistics.median(v)*1000:.0f}ms / p90 {sorted(v)[max(0,int(len(v)*0.9)-1)]*1000:.0f}ms / 最大 {max(v)*1000:.0f}ms" if v else "無樣本"
    print(f"\nplace 同步返回: {st(pr)}")
    print(f"place 交易所ack: {st(pa)}")
    print(f"cancel 同步返回: {st(cr)}")
    print(f"cancel 交易所ack: {st(ca_)}")
    if pa and ca_:
        loop = statistics.median(ca_) + statistics.median(pa)
        print(f"\n★ 重掛迴圈(cancel_ack+place_ack)中位 ≈ {loop*1000:.0f}ms")

    # 收尾:確保無殘單
    time.sleep(1)
    api.update_status(api.futopt_account)
    residual = [t for t in api.list_trades()
                if t.contract.code == target.code and str(t.status.status) not in
                ("Status.Cancelled", "Status.Failed", "Status.Filled")]
    for t in residual:
        api.cancel_order(t)
        print(f"補撤殘單 {t.order.seqno}")
    print(f"殘單檢查:{len(residual)} 張已補撤" if residual else "殘單檢查:乾淨")
finally:
    try:
        api.logout()
        print("logout OK")
    except Exception:
        pass
