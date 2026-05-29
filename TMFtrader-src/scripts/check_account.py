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

# (2026-05-29 lock-tmf-only) 已移除原本的 LIVE 限價測試單尾巴。
# check_account 回歸「純查帳」：只查保證金/餘額/部位/委託，絕不下任何單。

api.logout()
print("\nDone")
