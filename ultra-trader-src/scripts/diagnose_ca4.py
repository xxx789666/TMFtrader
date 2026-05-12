"""診斷 CA - 加入延遲等待 + 嘗試下單看詳細錯誤"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import shioaji as sj

api = sj.Shioaji(simulation=False)

person_id = os.environ["SHIOAJI_PERSON_ID"]
ca_passwd = os.environ["SHIOAJI_CA_PASSWORD"]

accounts = api.login(
    api_key=os.environ["SHIOAJI_API_KEY"],
    secret_key=os.environ["SHIOAJI_SECRET_KEY"],
    receive_window=300000,
    fetch_contract=True,
)

print(f"Login OK. Accounts: {len(accounts)}")
print(f"futopt_account: {api.futopt_account}")
print(f"stock_account: {api.stock_account}")
print(f"signed before CA: {api.futopt_account.signed}")

# activate CA
ca_path = os.environ.get("SHIOAJI_CA_PATH", "")
print(f"\nActivating CA: {ca_path}")
result = api.activate_ca(
    ca_path=ca_path,
    ca_passwd=ca_passwd,
    person_id=person_id,
)
print(f"activate_ca result: {result}")
print(f"signed immediately: {api.futopt_account.signed}")

# 等 3 秒看看 signed 是否異步更新
print("Waiting 3 seconds...")
time.sleep(3)
print(f"signed after 3s: {api.futopt_account.signed}")

# 嘗試查詢 margin
print("\n=== Testing margin ===")
try:
    m = api.margin(api.futopt_account)
    print(f"margin: {m}")
except Exception as e:
    print(f"margin error: {e}")

# 嘗試查看 account_balance
print("\n=== Testing account_balance ===")
try:
    b = api.account_balance()
    print(f"balance: {b}")
except Exception as e:
    print(f"balance error: {e}")

# 嘗試用 list_positions
print("\n=== Testing list_positions ===")
try:
    pos = api.list_positions(api.futopt_account)
    print(f"positions: {pos}")
except Exception as e:
    print(f"positions error: {e}")

# 嘗試下一口小台看錯誤
print("\n=== Testing place_order (小台 MXF) ===")
try:
    contract = api.Contracts.Futures.MXF.MXFR1
    print(f"contract: {contract}")

    order = api.Order(
        action=sj.constant.Action.Buy,
        price=0,  # 市價
        quantity=1,
        price_type=sj.constant.FuturesPriceType.MKT,
        order_type=sj.constant.OrderType.IOC,
        octype=sj.constant.FuturesOCType.Auto,
        account=api.futopt_account,
    )

    # 注意：這裡只是測試，盤後不會成交
    trade = api.place_order(contract, order)
    print(f"place_order result: {trade}")
except Exception as e:
    print(f"place_order error: {type(e).__name__}: {e}")

api.logout()
print("\nDone")
