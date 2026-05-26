"""Quick diagnostic to test Shioaji kbars() API directly."""
from datetime import datetime, timedelta
from pathlib import Path
import shioaji as sj
import os
import time
from dotenv import load_dotenv

load_dotenv(Path("/home/xx/ultra-trader-src/.env"))

api = sj.Shioaji(simulation=False)
api.login(
    api_key=os.environ["SHIOAJI_API_KEY"],
    secret_key=os.environ["SHIOAJI_SECRET_KEY"],
    receive_window=300000,
    fetch_contract=False,
)
api.fetch_contracts(contract_download=True)
time.sleep(5)

contract = api.Contracts.Futures.TMF.TMFR1
print(f"Contract: {contract.code} {contract.name}")

now = datetime.now()
today = now.strftime("%Y-%m-%d")
for days in [1, 2, 3, 5, 7]:
    start = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        k = api.kbars(contract=contract, start=start, end=today)
        n = len(k.Close) if hasattr(k, "Close") else 0
        print(f"  start={start} end={today} -> {n} bars")
    except Exception as e:
        print(f"  start={start} end={today} -> ERROR: {type(e).__name__}: {e}")

print("--- Other contracts ---")
for code in ["TXFR1", "MXFR1"]:
    try:
        prefix = code[:3]
        c = getattr(getattr(api.Contracts.Futures, prefix), code)
        start = (now - timedelta(days=3)).strftime("%Y-%m-%d")
        k = api.kbars(contract=c, start=start, end=today)
        n = len(k.Close) if hasattr(k, "Close") else 0
        print(f"  {code}: {n} bars")
    except Exception as e:
        print(f"  {code}: ERROR {type(e).__name__}: {e}")

# 試試只給 end 不給 start
print("--- end=today only (no start) ---")
try:
    k = api.kbars(contract=contract, end=today)
    n = len(k.Close) if hasattr(k, "Close") else 0
    print(f"  end={today} only: {n} bars")
except Exception as e:
    print(f"  ERROR: {type(e).__name__}: {e}")

# 試試前一個工作日
prev_day = (now - timedelta(days=1)).strftime("%Y-%m-%d")
print(f"--- end={prev_day} (yesterday only) ---")
try:
    k = api.kbars(contract=contract, start=prev_day, end=prev_day)
    n = len(k.Close) if hasattr(k, "Close") else 0
    print(f"  start={prev_day} end={prev_day}: {n} bars")
except Exception as e:
    print(f"  ERROR: {type(e).__name__}: {e}")

print("--- Stock kbars test (台積電 2330) ---")
try:
    stock = api.Contracts.Stocks["2330"]
    start = (now - timedelta(days=3)).strftime("%Y-%m-%d")
    k = api.kbars(contract=stock, start=start, end=today)
    n = len(k.Close) if hasattr(k, "Close") else 0
    print(f"  2330 start={start} end={today}: {n} bars")
    if n > 0:
        print(f"  first 3 ts: {[k.ts[i] for i in range(min(3,n))]}")
except Exception as e:
    print(f"  ERROR: {type(e).__name__}: {e}")

print("--- 古老日期測試 ---")
try:
    k = api.kbars(contract=contract, start="2024-01-02", end="2024-01-05")
    n = len(k.Close) if hasattr(k, "Close") else 0
    print(f"  TMFR1 2024-01-02 to 2024-01-05: {n} bars")
except Exception as e:
    print(f"  ERROR: {type(e).__name__}: {e}")

print("--- 列舉 TMF 所有 future 合約 ---")
try:
    tmf_all = api.Contracts.Futures.TMF
    for attr in dir(tmf_all):
        if not attr.startswith("_") and attr.startswith("TMF"):
            c = getattr(tmf_all, attr)
            print(f"  {attr}: code={getattr(c,'code','?')} delivery={getattr(c,'delivery_month','?')}")
except Exception as e:
    print(f"  ERROR: {e}")

print(f"--- shioaji version ---")
print(f"  sj.__version__ = {sj.__version__}")

print("--- 🔥 api.usage() 流量診斷 ---")
try:
    u = api.usage()
    print(f"  raw: {u}")
    # 嘗試解析各欄位
    for attr in ["connections", "bytes", "limit_bytes", "remaining_bytes", "limit", "remaining", "used"]:
        if hasattr(u, attr):
            print(f"  {attr}: {getattr(u, attr)}")
except Exception as e:
    print(f"  ERROR: {type(e).__name__}: {e}")

api.logout()
