"""
Diagnose: 查永豐期貨帳號當下實際持倉 + 今日所有委託 metadata
找 22:45 / 22:50 那兩筆 TMF Buy 的 fingerprint(custom_field、subaccount...)
讀 only、不下單、不註冊 callback、避免干擾現有 start.py + night_orb.py 兩個 session。
"""
import os, sys, json
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import shioaji as sj

api = sj.Shioaji(simulation=False)
api.login(
    api_key=os.environ["SHIOAJI_API_KEY"],
    secret_key=os.environ["SHIOAJI_SECRET_KEY"],
    receive_window=300000,
    fetch_contract=False,
)
print(f"Login OK. account_id={api.futopt_account.account_id} signed={api.futopt_account.signed}")

# 1) 現在實際持倉
print("\n=== list_positions(futopt_account) ===")
positions = api.list_positions(api.futopt_account)
for p in positions:
    print(f"  {p}")
if not positions:
    print("  (no positions)")

# 2) 今日所有委託 + 成交紀錄
print("\n=== update_status + list_trades ===")
api.update_status(api.futopt_account)
trades = api.list_trades()
print(f"total trades today: {len(trades)}")
for t in trades:
    o = t.order
    s = t.status
    c = t.contract
    print(f"\n--- trade ---")
    print(f"  contract: code={c.code} delivery={getattr(c,'delivery_month','')}")
    print(f"  order: id={o.id} seqno={getattr(o,'seqno','')} ordno={getattr(o,'ordno','')}")
    print(f"         action={o.action} qty={o.quantity} price={o.price}")
    print(f"         price_type={getattr(o,'price_type','')} order_type={getattr(o,'order_type','')}")
    print(f"         octype={getattr(o,'octype','')} subaccount={repr(getattr(o,'subaccount',''))}")
    print(f"         custom_field={repr(getattr(o,'custom_field',''))}")
    print(f"  status: state={s.status} order_qty={s.order_quantity} deal_qty={s.deal_quantity}")
    print(f"          cancel_qty={s.cancel_quantity} modified_price={s.modified_price}")
    print(f"          web_id={getattr(s,'web_id','')} exchange_ts={getattr(s,'exchange_ts',0)}")
    for d in (s.deals or []):
        print(f"          deal: price={d.price} qty={d.quantity} ts={d.ts}")

# 3) 完整原始 order/deal event 紀錄（含所有 metadata）
print("\n=== order_deal_records (raw events) ===")
records = api.order_deal_records()
for state, ev in records:
    # 只列 TMF/MXF 相關
    code = ""
    try:
        if isinstance(ev, dict):
            if "contract" in ev and isinstance(ev["contract"], dict):
                code = ev["contract"].get("code", "")
            elif "code" in ev:
                code = ev.get("code", "")
    except Exception:
        pass
    if code in ("TMF", "MXF"):
        print(f"\n[{state}] code={code}")
        print(json.dumps(ev, default=str, ensure_ascii=False, indent=2))

api.logout()
print("\nDone")
