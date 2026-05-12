"""
永豐 Shioaji 模擬環境測試
必須在 週一~週五 08:00~20:00 執行
通過後等 5 分鐘，signed 就會變 True
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import shioaji as sj

print("=" * 50)
print("Step 1: 模擬環境登入測試")
print("=" * 50)

api = sj.Shioaji(simulation=True)

accounts = api.login(
    api_key=os.environ["SHIOAJI_API_KEY"],
    secret_key=os.environ["SHIOAJI_SECRET_KEY"],
    receive_window=300000,
    fetch_contract=True,
)

print(f"登入成功！帳戶數: {len(accounts)}")
for acc in accounts:
    print(f"  {acc}")

print(f"futopt_account: {api.futopt_account}")

# 等合約載入完成
time.sleep(3)

print("\n" + "=" * 50)
print("Step 2: 模擬環境期貨下單測試")
print("=" * 50)

try:
    contract = api.Contracts.Futures.MXF.MXFR1
    print(f"合約: {contract}")

    order = api.Order(
        action=sj.constant.Action.Buy,
        price=0,
        quantity=1,
        price_type=sj.constant.FuturesPriceType.MKT,
        order_type=sj.constant.OrderType.IOC,
        octype=sj.constant.FuturesOCType.Auto,
        account=api.futopt_account,
    )

    trade = api.place_order(contract, order)
    print(f"下單結果: {trade}")
    print(f"狀態: {trade.status}")
except Exception as e:
    print(f"下單錯誤: {e}")

print("\n" + "=" * 50)
print("Step 3: 等待 5 分鐘讓系統審核...")
print("=" * 50)
print("（如果不想等，可以 Ctrl+C 中斷，5 分鐘後再跑驗證腳本）")

for i in range(300, 0, -30):
    print(f"  剩餘 {i} 秒...")
    time.sleep(30)

api.logout()
print("模擬測試完成！")

print("\n" + "=" * 50)
print("Step 4: 正式環境驗證 signed 狀態")
print("=" * 50)

api2 = sj.Shioaji(simulation=False)
accounts2 = api2.login(
    api_key=os.environ["SHIOAJI_API_KEY"],
    secret_key=os.environ["SHIOAJI_SECRET_KEY"],
    receive_window=300000,
    fetch_contract=True,
)

person_id = os.environ["SHIOAJI_PERSON_ID"]
ca_passwd = os.environ["SHIOAJI_CA_PASSWORD"]

result = api2.activate_ca(
    ca_path=os.environ.get("SHIOAJI_CA_PATH", ""),
    ca_passwd=ca_passwd,
    person_id=person_id,
)

print(f"activate_ca: {result}")
print(f"signed: {api2.futopt_account.signed}")

if api2.futopt_account.signed:
    print("\n✅ 成功！signed=True，可以正式下單了！")
else:
    print("\n⚠️ signed 仍為 False，可能需要再等幾分鐘或聯繫永豐客服")

api2.logout()
print("\nDone")
