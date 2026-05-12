"""更多可能性測試"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import shioaji as sj

person_id = os.environ["SHIOAJI_PERSON_ID"]
ca_passwd = os.environ["SHIOAJI_CA_PASSWORD"]

# 測試 1: 用 store=1
print("=" * 50)
print("TEST 1: activate_ca with store=1")
print("=" * 50)

api = sj.Shioaji(simulation=False)
accounts = api.login(
    api_key=os.environ["SHIOAJI_API_KEY"],
    secret_key=os.environ["SHIOAJI_SECRET_KEY"],
    receive_window=300000,
    fetch_contract=True,
)

# 用較新的憑證 + store=1
ca_path = os.environ.get("SHIOAJI_CA_PATH", "")
print(f"CA: {ca_path}")
result = api.activate_ca(
    ca_path=ca_path,
    ca_passwd=ca_passwd,
    person_id=person_id,
    store=1,
)
print(f"result={result}, signed={api.futopt_account.signed}")
time.sleep(2)
print(f"signed after 2s: {api.futopt_account.signed}")

# 設定 order callback 看詳細錯誤
def order_cb(stat, msg):
    print(f"  [ORDER CB] stat={stat}, msg={msg}")

def error_cb(err):
    print(f"  [ERROR CB] {err}")

api.set_order_callback(order_cb)

# 嘗試下單
print("\nTrying place_order...")
try:
    contract = api.Contracts.Futures.MXF.MXFR1
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
    print(f"trade status: {trade.status}")
    print(f"trade full: {trade}")
except Exception as e:
    print(f"error: {e}")

time.sleep(2)
api.logout()

# 測試 2: 不帶 person_id 看看
print("\n" + "=" * 50)
print("TEST 2: activate_ca without person_id")
print("=" * 50)

api2 = sj.Shioaji(simulation=False)
accounts2 = api2.login(
    api_key=os.environ["SHIOAJI_API_KEY"],
    secret_key=os.environ["SHIOAJI_SECRET_KEY"],
    receive_window=300000,
    fetch_contract=True,
)

result2 = api2.activate_ca(
    ca_path=ca_path,
    ca_passwd=ca_passwd,
)
print(f"result={result2}, signed={api2.futopt_account.signed}")
time.sleep(2)
print(f"signed after 2s: {api2.futopt_account.signed}")

api2.logout()

# 測試 3: 先 login 再等久一點再 activate
print("\n" + "=" * 50)
print("TEST 3: Wait 5s after login, then activate")
print("=" * 50)

api3 = sj.Shioaji(simulation=False)
accounts3 = api3.login(
    api_key=os.environ["SHIOAJI_API_KEY"],
    secret_key=os.environ["SHIOAJI_SECRET_KEY"],
    receive_window=300000,
    fetch_contract=True,
)
print("Waiting 5s after login...")
time.sleep(5)

# 用原始檔案
ca_path_orig = os.environ.get("SHIOAJI_CA_PATH", "")
result3 = api3.activate_ca(
    ca_path=ca_path_orig,
    ca_passwd=ca_passwd,
    person_id=person_id,
)
print(f"result={result3}, signed={api3.futopt_account.signed}")
time.sleep(3)
print(f"signed after 3s: {api3.futopt_account.signed}")

# 查看 shioaji 版本
print(f"\nShioaji version: {sj.__version__}")

api3.logout()
print("\nAll tests done")
