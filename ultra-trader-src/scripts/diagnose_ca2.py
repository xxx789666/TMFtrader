"""診斷 CA 簽署 - 嘗試手動設定 signed 並下單測試"""
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
import os
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

import shioaji as sj

api = sj.Shioaji(simulation=False)

accounts = api.login(
    api_key=os.environ["SHIOAJI_API_KEY"],
    secret_key=os.environ["SHIOAJI_SECRET_KEY"],
    receive_window=300000,
    fetch_contract=True,
)

ca_path = os.environ.get("SHIOAJI_CA_PATH", "")
ca_pass = os.environ.get("SHIOAJI_CA_PASSWORD", "")
person_id = os.environ.get("SHIOAJI_PERSON_ID", "")

print(f"Before activate_ca: signed={api.futopt_account.signed}")

result = api.activate_ca(
    ca_path=ca_path,
    ca_passwd=ca_pass,
    person_id=person_id,
)
print(f"activate_ca returned: {result}")
print(f"After activate_ca: signed={api.futopt_account.signed}")

# 嘗試方法1: 直接 set signed=True
print(f"\n=== 方法1: 手動設定 signed=True ===")
try:
    api.futopt_account.signed = True
    print(f"  signed set to: {api.futopt_account.signed}")
except Exception as e:
    print(f"  Failed: {e}")

# 嘗試實際下單 (TGF 黃金期，用限價單避免成交)
print(f"\n=== 嘗試下單 (用極低限價避免成交) ===")
try:
    contract = api.Contracts.Futures.TGF.TGFR1
    print(f"  contract: {contract.code} limit_down={contract.limit_down}")

    # 用跌停價以下的限價單，不會成交，只測試是否通過 406
    test_price = contract.limit_down
    order = api.Order(
        action=sj.constant.Action.Buy,
        price=test_price,
        quantity=1,
        price_type=sj.constant.FuturesPriceType.LMT,
        order_type=sj.constant.OrderType.ROD,
        octype=sj.constant.FuturesOCType.Auto,
        account=api.futopt_account,
    )
    print(f"  order created, account.signed={order.account.signed}")

    trade = api.place_order(contract, order)
    print(f"  place_order result: {trade}")
    if trade:
        print(f"    status: {trade.status}")
        print(f"    order.id: {trade.order.id}")
        print(f"    order.seqno: {trade.order.seqno}")

        # 立刻取消
        try:
            cancel_result = api.cancel_order(trade)
            print(f"    cancel: {cancel_result}")
        except Exception as ce:
            print(f"    cancel error: {ce}")
    else:
        print("  trade is None!")

except Exception as e:
    print(f"  ERROR: {e}")

# 也試試看不設 signed 會怎樣
print(f"\n=== 方法2: 不指定 account（讓 Shioaji 自動選） ===")
try:
    contract = api.Contracts.Futures.TGF.TGFR1
    order2 = api.Order(
        action=sj.constant.Action.Buy,
        price=contract.limit_down,
        quantity=1,
        price_type=sj.constant.FuturesPriceType.LMT,
        order_type=sj.constant.OrderType.ROD,
        octype=sj.constant.FuturesOCType.Auto,
    )
    print(f"  order2 account: {order2.account}")

    trade2 = api.place_order(contract, order2)
    print(f"  result: {trade2}")
    if trade2:
        print(f"    status: {trade2.status}")
        try:
            api.cancel_order(trade2)
            print(f"    cancelled")
        except:
            pass
except Exception as e:
    print(f"  ERROR: {e}")

api.logout()
print("\nDone")
