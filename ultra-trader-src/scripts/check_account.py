"""查帳戶狀態 - 保證金、部位、委託"""
import sys, os, time
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

# 1. Margin
print("\n=== Margin ===")
try:
    m = api.margin(api.futopt_account)
    print(f"  {m}")
except Exception as e:
    print(f"  error: {e}")

# 2. Account balance
print("\n=== Account Balance ===")
try:
    b = api.account_balance()
    print(f"  {b}")
except Exception as e:
    print(f"  error: {e}")

# 3. List positions
print("\n=== Positions ===")
try:
    pos = api.list_positions(api.futopt_account)
    if pos:
        for p in pos:
            print(f"  {p}")
    else:
        print("  No positions")
except Exception as e:
    print(f"  error: {e}")

# 4. List trades (today)
print("\n=== Trades ===")
try:
    trades = api.list_trades()
    if trades:
        for t in trades:
            print(f"  {t.contract.code} {t.order.action} x{t.order.quantity} status={t.status.status}")
    else:
        print("  No trades")
except Exception as e:
    print(f"  error: {e}")

# 5. Try limit order instead of market
print("\n=== Try Limit Order ===")
contract = api.Contracts.Futures.MXF.MXFR1
snap = api.snapshots([contract])
for s in snap:
    print(f"  MXF: buy={s.buy_price} sell={s.sell_price} last={s.close}")
    sell_price = s.sell_price

# Try with explicit limit price at sell_price
if sell_price > 0:
    print(f"\n  Trying LIMIT order at {sell_price}...")
    order = api.Order(
        action=sj.constant.Action.Buy,
        price=sell_price,
        quantity=1,
        price_type=sj.constant.FuturesPriceType.LMT,
        order_type=sj.constant.OrderType.IOC,
        octype=sj.constant.FuturesOCType.Auto,
        account=api.futopt_account,
    )

    def order_cb(stat, msg):
        print(f"  [CB] {stat}")
        if 'operation' in msg:
            print(f"    op={msg['operation']}")
        if 'status' in msg and isinstance(msg['status'], dict):
            st = msg['status']
            print(f"    status={st}")

    api.set_order_callback(order_cb)
    trade = api.place_order(contract, order)
    print(f"  result: {trade.status}")
    time.sleep(3)

    # Update
    api.update_status(api.futopt_account)
    for t in api.list_trades():
        print(f"  trade: {t.contract.code} {t.order.action} status={t.status.status} deals={len(t.status.deals)}")
        for d in t.status.deals:
            print(f"    FILLED: price={d.price} qty={d.quantity}")

api.logout()
print("\nDone")
