"""更詳細的 CA 診斷"""
import sys, os
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

person_id = os.environ["SHIOAJI_PERSON_ID"]

# 列出所有帳戶詳細
print("=== Accounts ===")
for acc in accounts:
    print(f"  {acc}")
    for attr in dir(acc):
        if not attr.startswith('_'):
            print(f"    {attr} = {getattr(acc, attr, '?')}")

print(f"\nfutopt_account: {api.futopt_account}")
print(f"stock_account: {api.stock_account}")

# 測試兩個憑證檔案
ca_files = [
    os.environ.get("SHIOAJI_CA_PATH", ""),
    os.environ.get("SHIOAJI_CA_PATH", ""),
]

# 測試不同密碼
passwords = [
    person_id,           # os.environ.get("SHIOAJI_PERSON_ID", "")
    person_id.lower(),   # n125893469
    "",                  # 空密碼
]

for ca_file in ca_files:
    for pw in passwords:
        if not os.path.exists(ca_file):
            continue
        print(f"\n--- Testing: {os.path.basename(ca_file)} | passwd='{pw[:3]}...' ---")
        try:
            result = api.activate_ca(
                ca_path=ca_file,
                ca_passwd=pw,
                person_id=person_id,
            )
            signed = api.futopt_account.signed
            print(f"  result={result} | signed={signed}")
            if signed:
                print("  >>> SUCCESS! This combination works!")
                break
        except Exception as e:
            print(f"  ERROR: {e}")
    else:
        continue
    break

# 最後狀態
print(f"\n=== Final State ===")
print(f"futopt_account.signed = {api.futopt_account.signed}")

# 檢查是否有 list_order_profitloss 等功能可用
print(f"\n=== 查詢委託/成交紀錄 ===")
try:
    orders = api.list_trades()
    print(f"  list_trades: {len(orders) if orders else 0} trades")
    for t in (orders or []):
        print(f"    {t}")
except Exception as e:
    print(f"  list_trades error: {e}")

try:
    from datetime import date
    pnl = api.list_profit_loss(api.futopt_account, begin_date=date(2026, 3, 1), end_date=date(2026, 3, 10))
    print(f"  list_profit_loss: {pnl}")
except Exception as e:
    print(f"  list_profit_loss error: {e}")

# 查詢帳戶餘額
try:
    margin = api.margin(api.futopt_account)
    print(f"\n=== Margin Info ===")
    print(f"  {margin}")
except Exception as e:
    print(f"  margin error: {e}")

try:
    account_balance = api.account_balance()
    print(f"\n=== Account Balance ===")
    print(f"  {account_balance}")
except Exception as e:
    print(f"  account_balance error: {e}")

api.logout()
print("\nDone")
