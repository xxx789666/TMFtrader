"""正式環境下單 - MXF 買進 1 口"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import shioaji as sj

api = sj.Shioaji(simulation=False)

# Login
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
print(f"CA activated: {result}, signed: {api.futopt_account.signed}")

if not api.futopt_account.signed:
    print("ERROR: signed is False, cannot place order!")
    api.logout()
    sys.exit(1)

# Set order callback
def order_cb(stat, msg):
    print(f"[ORDER] {stat}")
    print(f"  {msg}")

api.set_order_callback(order_cb)

# Place order: MXF Buy 1 lot, Market IOC
contract = api.Contracts.Futures.MXF.MXFR1
print(f"\nContract: {contract.code} ({contract.name})")

# 先看一下最新報價
snap = api.snapshots([contract])
for s in snap:
    print(f"Latest: buy={s.buy_price} sell={s.sell_price} last={s.close} vol={s.total_volume}")

order = api.Order(
    action=sj.constant.Action.Buy,
    price=0,
    quantity=1,
    price_type=sj.constant.FuturesPriceType.MKT,
    order_type=sj.constant.OrderType.IOC,
    octype=sj.constant.FuturesOCType.Auto,
    account=api.futopt_account,
)

print(f"\n>>> Placing order: BUY MXF x1 @ MKT IOC <<<")
trade = api.place_order(contract, order)
print(f"\nOrder result:")
print(f"  Status: {trade.status}")
print(f"  Order: {trade.order}")

# Wait for callback
time.sleep(3)

# Check trades
trades = api.list_trades()
print(f"\nAll trades ({len(trades)}):")
for t in trades:
    print(f"  {t}")

api.logout()
print("\nDone")
