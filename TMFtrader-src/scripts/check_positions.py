"""只查部位，不下單"""
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

# Margin
print("=== Margin ===")
m = api.margin(api.futopt_account)
print(f"  equity={m.equity} available={m.available_margin}")
print(f"  initial_margin={m.initial_margin} maintenance={m.maintenance_margin}")
print(f"  future_open_position={m.future_open_position}")
print(f"  future_settle_profitloss={m.future_settle_profitloss}")

# Positions
print("\n=== Positions ===")
try:
    pos = api.list_positions(api.futopt_account)
    if pos:
        for p in pos:
            print(f"  {p}")
    else:
        print("  No positions")
except Exception as e:
    print(f"  {e}")

# Today's trades
print("\n=== Today's Trades ===")
api.update_status(api.futopt_account)
trades = api.list_trades()
for t in trades:
    s = t.status
    print(f"  {t.contract.code} {t.order.action} x{t.order.quantity} status={s.status}")
    for d in s.deals:
        print(f"    price={d.price} qty={d.quantity}")

# Snapshot
contract = api.Contracts.Futures.TMF.TMFR1
snap = api.snapshots([contract])
for s in snap:
    print(f"\nTMF now: last={s.close} buy={s.buy_price} sell={s.sell_price}")

api.logout()
print("\nDone")
