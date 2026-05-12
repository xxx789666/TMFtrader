"""正式環境下單 - MXF 買進 1 口 (夜盤)"""
import sys, os, time
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import shioaji as sj

# 等到 15:00
now = datetime.now()
target = now.replace(hour=15, minute=0, second=1, microsecond=0)
if now < target:
    wait = (target - now).total_seconds()
    print(f"Current time: {now.strftime('%H:%M:%S')}")
    print(f"Waiting {int(wait)} seconds until 15:00:01...")
    while datetime.now() < target:
        remaining = (target - datetime.now()).total_seconds()
        if remaining > 0:
            print(f"  {int(remaining)}s remaining...", end='\r')
            time.sleep(min(remaining, 10))
    print(f"\n15:00 reached! Go!")

api = sj.Shioaji(simulation=False)

accounts = api.login(
    api_key=os.environ["SHIOAJI_API_KEY"],
    secret_key=os.environ["SHIOAJI_SECRET_KEY"],
    receive_window=300000,
    fetch_contract=True,
)
print(f"Login OK. Account: {api.futopt_account.account_id}")
time.sleep(2)

# Activate CA
person_id = os.environ["SHIOAJI_PERSON_ID"]
ca_passwd = os.environ["SHIOAJI_CA_PASSWORD"]
result = api.activate_ca(
    ca_path=os.environ.get("SHIOAJI_CA_PATH", ""),
    ca_passwd=ca_passwd,
    person_id=person_id,
)
print(f"CA: {result}, signed: {api.futopt_account.signed}")

if not api.futopt_account.signed:
    print("ERROR: signed=False!")
    api.logout()
    sys.exit(1)

# Order callback
def order_cb(stat, msg):
    print(f"[ORDER] {stat}")
    if 'operation' in msg:
        op = msg['operation']
        print(f"  op_code={op.get('op_code','')} op_msg={op.get('op_msg','')}")
    if 'status' in msg and isinstance(msg['status'], dict):
        print(f"  status={msg['status']}")

api.set_order_callback(order_cb)

# Check price before ordering
contract = api.Contracts.Futures.MXF.MXFR1
snap = api.snapshots([contract])
for s in snap:
    print(f"\nMXF: buy={s.buy_price} sell={s.sell_price} last={s.close} change={s.change_price} ({s.change_rate}%)")
    print(f"  high={s.high} low={s.low} vol={s.total_volume}")

# Place order
order = api.Order(
    action=sj.constant.Action.Buy,
    price=0,
    quantity=1,
    price_type=sj.constant.FuturesPriceType.MKT,
    order_type=sj.constant.OrderType.IOC,
    octype=sj.constant.FuturesOCType.Auto,
    account=api.futopt_account,
)

print(f"\n>>> BUY MXF x1 @ MKT IOC [{datetime.now().strftime('%H:%M:%S')}] <<<")
trade = api.place_order(contract, order)
print(f"Status: {trade.status}")

# Wait for fill
time.sleep(5)

# Update and check
api.update_status(api.futopt_account)
trades = api.list_trades()
print(f"\nTrades ({len(trades)}):")
for t in trades:
    s = t.status
    print(f"  {t.contract.code} {t.order.action} x{t.order.quantity}")
    print(f"  status={s.status} deals={len(s.deals)}")
    for d in s.deals:
        print(f"    FILLED: price={d.price} qty={d.quantity} ts={d.ts}")

api.logout()
print("\nDone")
