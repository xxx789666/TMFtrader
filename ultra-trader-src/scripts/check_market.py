"""查看黃金期貨和小台即時行情"""
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

# 查看黃金期貨合約
print("=== Gold Futures (GDF) ===")
try:
    gdf_contracts = [c for c in api.Contracts.Futures.GDF]
    for c in gdf_contracts:
        print(f"  {c.code} | {c.name} | delivery={c.delivery_month} | ref={c.reference} | limit_up={c.limit_up} | limit_down={c.limit_down}")
except Exception as e:
    print(f"  Error: {e}")

# 查看小台合約
print("\n=== Mini TAIEX (MXF) ===")
try:
    mxf = api.Contracts.Futures.MXF.MXFR1
    print(f"  {mxf.code} | {mxf.name} | ref={mxf.reference} | up={mxf.limit_up} | down={mxf.limit_down}")
except Exception as e:
    print(f"  Error: {e}")

# 訂閱即時報價
snapshots = {}

print("\n=== Snapshots ===")

# 黃金近月
try:
    gdf = api.Contracts.Futures.GDF.GDFR1
    print(f"\nGDF contract: {gdf.code} {gdf.name} delivery={gdf.delivery_month}")
    snap_gdf = api.snapshots([gdf])
    for s in snap_gdf:
        print(f"  close={s.close} open={s.open} high={s.high} low={s.low}")
        print(f"  buy_price={s.buy_price} sell_price={s.sell_price}")
        print(f"  volume={s.volume} total_volume={s.total_volume}")
        print(f"  change_price={s.change_price} change_rate={s.change_rate}")
        print(f"  amount={s.amount} total_amount={s.total_amount}")
except Exception as e:
    print(f"  GDF snapshot error: {e}")

# 小台近月
try:
    mxf = api.Contracts.Futures.MXF.MXFR1
    print(f"\nMXF contract: {mxf.code} {mxf.name} delivery={mxf.delivery_month}")
    snap_mxf = api.snapshots([mxf])
    for s in snap_mxf:
        print(f"  close={s.close} open={s.open} high={s.high} low={s.low}")
        print(f"  buy_price={s.buy_price} sell_price={s.sell_price}")
        print(f"  volume={s.volume} total_volume={s.total_volume}")
        print(f"  change_price={s.change_price} change_rate={s.change_rate}")
        print(f"  amount={s.amount} total_amount={s.total_amount}")
except Exception as e:
    print(f"  MXF snapshot error: {e}")

# 也看看微黃金
print("\n=== Micro Gold (TGF) ===")
try:
    for cat in dir(api.Contracts.Futures):
        if 'G' in cat.upper() or 'TG' in cat.upper():
            print(f"  Found category: {cat}")
except:
    pass

try:
    tgf = api.Contracts.Futures.TGF
    tgf_contracts = [c for c in tgf]
    for c in tgf_contracts:
        print(f"  {c.code} | {c.name} | delivery={c.delivery_month} | ref={c.reference}")
    if tgf_contracts:
        snap_tgf = api.snapshots([tgf_contracts[0]])
        for s in snap_tgf:
            print(f"  close={s.close} volume={s.total_volume} change={s.change_price}")
except Exception as e:
    print(f"  TGF error: {e}")

api.logout()
print("\nDone")
