"""平掉多單 + 反手開空"""
import sys, os, time
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import shioaji as sj

api = sj.Shioaji(simulation=False)
accounts = api.login(
    api_key=os.environ["SHIOAJI_API_KEY"],
    secret_key=os.environ["SHIOAJI_SECRET_KEY"],
    receive_window=300000,
    fetch_contract=True,
)
time.sleep(2)

person_id = os.environ["SHIOAJI_PERSON_ID"]
ca_passwd = os.environ["SHIOAJI_CA_PASSWORD"]
api.activate_ca(
    ca_path=os.environ.get("SHIOAJI_CA_PATH", ""),
    ca_passwd=ca_passwd,
    person_id=person_id,
)
print(f"signed: {api.futopt_account.signed}")

def order_cb(stat, msg):
    print(f"[CB] {stat}")
    if 'operation' in msg:
        op = msg['operation']
        print(f"  op={op.get('op_code','')} {op.get('op_msg','')}")

api.set_order_callback(order_cb)

contract = api.Contracts.Futures.TMF.TMFR1

# 先看報價
snap = api.snapshots([contract])
for s in snap:
    print(f"TMF: buy={s.buy_price} sell={s.sell_price} last={s.close} change={s.change_price}({s.change_rate}%)")

# Step 1: 平掉多單 (Sell 1 口平倉)
print(f"\n>>> STEP 1: SELL TMF x1 平多單 [{datetime.now().strftime('%H:%M:%S')}] <<<")
sell_order = api.Order(
    action=sj.constant.Action.Sell,
    price=0,
    quantity=1,
    price_type=sj.constant.FuturesPriceType.MKT,
    order_type=sj.constant.OrderType.IOC,
    octype=sj.constant.FuturesOCType.Auto,
    account=api.futopt_account,
)
trade1 = api.place_order(contract, sell_order)
print(f"Status: {trade1.status}")
time.sleep(3)

# Step 2: 開空單 (Sell 1 口新倉)
print(f"\n>>> STEP 2: SELL TMF x1 開空單 [{datetime.now().strftime('%H:%M:%S')}] <<<")
short_order = api.Order(
    action=sj.constant.Action.Sell,
    price=0,
    quantity=1,
    price_type=sj.constant.FuturesPriceType.MKT,
    order_type=sj.constant.OrderType.IOC,
    octype=sj.constant.FuturesOCType.New,
    account=api.futopt_account,
)
trade2 = api.place_order(contract, short_order)
print(f"Status: {trade2.status}")
time.sleep(5)

# 結果
api.update_status(api.futopt_account)
trades = api.list_trades()
print(f"\nAll trades ({len(trades)}):")
for t in trades:
    s = t.status
    print(f"  {t.contract.code} {t.order.action} x{t.order.quantity} oc={t.order.octype if hasattr(t.order, 'octype') else '?'} status={s.status}")
    for d in s.deals:
        print(f"    FILLED: price={d.price} qty={d.quantity}")

# 查部位
print("\n=== Positions ===")
try:
    pos = api.list_positions(api.futopt_account)
    for p in pos:
        print(f"  {p}")
except Exception as e:
    print(f"  {e}")

api.logout()
print("\nDone")
