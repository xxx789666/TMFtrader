"""測試下單 - 確認額度是否已開通"""
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
print(f"Login OK. Account: {api.futopt_account.account_id}")
time.sleep(2)

person_id = os.environ["SHIOAJI_PERSON_ID"]
ca_passwd = os.environ["SHIOAJI_CA_PASSWORD"]
api.activate_ca(
    ca_path=os.environ.get("SHIOAJI_CA_PATH", ""),
    ca_passwd=ca_passwd,
    person_id=person_id,
)
print(f"signed: {api.futopt_account.signed}")

# Order callback
def order_cb(stat, msg):
    print(f"[CB] {stat}")
    if 'operation' in msg:
        op = msg['operation']
        print(f"  op_code={op.get('op_code','')} op_msg={op.get('op_msg','')}")

api.set_order_callback(order_cb)

# Check MXF price
contract = api.Contracts.Futures.MXF.MXFR1
snap = api.snapshots([contract])
for s in snap:
    print(f"\nMXF: buy={s.buy_price} sell={s.sell_price} last={s.close}")
    print(f"  change={s.change_price} ({s.change_rate}%) vol={s.total_volume}")

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

time.sleep(5)

# Check result
api.update_status(api.futopt_account)
trades = api.list_trades()
print(f"\nTrades ({len(trades)}):")
for t in trades:
    s = t.status
    print(f"  {t.contract.code} {t.order.action} x{t.order.quantity} status={s.status}")
    for d in s.deals:
        print(f"    FILLED: price={d.price} qty={d.quantity}")

api.logout()
print("\nDone")
