"""診斷 CA 簽署問題"""
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
import os
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import shioaji as sj

api = sj.Shioaji(simulation=False)

print(f"Shioaji version: {sj.__version__}")

accounts = api.login(
    api_key=os.environ["SHIOAJI_API_KEY"],
    secret_key=os.environ["SHIOAJI_SECRET_KEY"],
    receive_window=300000,
    fetch_contract=True,
)

print(f"\n=== 帳戶列表 ===")
for i, acc in enumerate(accounts):
    print(f"  [{i}] {acc}")
    print(f"       type={getattr(acc, 'account_type', '?')}")
    print(f"       broker_id={getattr(acc, 'broker_id', '?')}")
    print(f"       account_id={getattr(acc, 'account_id', '?')}")
    print(f"       signed={getattr(acc, 'signed', '?')}")

print(f"\n=== futopt_account ===")
fa = api.futopt_account
print(f"  {fa}")
print(f"  signed={getattr(fa, 'signed', '?')}")
print(f"  account_type={getattr(fa, 'account_type', '?')}")

print(f"\n=== stock_account ===")
sa = api.stock_account
print(f"  {sa}")

# 嘗試 CA 啟用
ca_path = os.environ.get("SHIOAJI_CA_PATH", "")
ca_pass = os.environ.get("SHIOAJI_CA_PASSWORD", "")
person_id = os.environ.get("SHIOAJI_PERSON_ID", "")

print(f"\n=== CA 啟用 ===")
print(f"  ca_path: {ca_path}")
print(f"  ca_pass: {'*' * len(ca_pass)}")
print(f"  person_id: {person_id}")
print(f"  ca_path exists: {os.path.exists(ca_path)}")

try:
    result = api.activate_ca(
        ca_path=ca_path,
        ca_passwd=ca_pass,
        person_id=person_id,
    )
    print(f"  activate_ca result: {result}")
except Exception as e:
    print(f"  activate_ca ERROR: {e}")

print(f"\n=== CA 啟用後 ===")
fa2 = api.futopt_account
print(f"  futopt_account signed={getattr(fa2, 'signed', '?')}")

# 檢查所有帳戶的 signed 狀態
print(f"\n=== 所有帳戶 signed 狀態 ===")
for attr_name in ['stock_account', 'futopt_account', 'margin_account', 'stock_account_list', 'futopt_account_list']:
    obj = getattr(api, attr_name, None)
    if obj is None:
        continue
    if isinstance(obj, list):
        for i, a in enumerate(obj):
            print(f"  {attr_name}[{i}]: signed={getattr(a, 'signed', '?')} | {a}")
    else:
        print(f"  {attr_name}: signed={getattr(obj, 'signed', '?')} | {obj}")

# 嘗試下一小單測試
print(f"\n=== 測試下單（不會真的成交，只檢查是否被 406 擋） ===")
try:
    contract = api.Contracts.Futures.TGF.TGFR1
    print(f"  contract: {contract.code} ({contract.name})")

    order = api.Order(
        action=sj.constant.Action.Buy,
        price=0,
        quantity=1,
        price_type=sj.constant.FuturesPriceType.MKT,
        order_type=sj.constant.OrderType.IOC,
        octype=sj.constant.FuturesOCType.Auto,
        account=api.futopt_account,
    )
    print(f"  order account signed: {order.account.signed}")

    # 不要真的下單，只檢查到這裡
    print(f"  (如果 signed=True，下單應該可以成功)")

    if getattr(api.futopt_account, 'signed', False):
        print(f"\n  ✓ CA 簽署成功！可以下單")
    else:
        print(f"\n  ✗ CA 簽署失敗！signed 仍然是 False")
        print(f"  可能原因：")
        print(f"    1. CA 憑證密碼錯誤")
        print(f"    2. CA 憑證已過期")
        print(f"    3. person_id 不匹配")
        print(f"    4. 需要重新下載憑證")
except Exception as e:
    print(f"  ERROR: {e}")

api.logout()
print("\n已登出")
