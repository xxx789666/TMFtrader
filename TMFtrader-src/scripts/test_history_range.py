"""
測試 Shioaji 歷史 K 棒能抓多遠
用法：先停 server，然後執行
  python scripts/test_history_range.py

注意：Shioaji 同帳號只能一個 session，執行前須先停 server
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from dotenv import load_dotenv
load_dotenv()

import shioaji as sj
from datetime import datetime, timedelta

API_KEY    = os.getenv("SHIOAJI_API_KEY")
SECRET_KEY = os.getenv("SHIOAJI_SECRET_KEY")
CA_PATH    = os.getenv("SHIOAJI_CA_PATH", "")
CA_PWD     = os.getenv("SHIOAJI_CA_PASSWORD", "")

print(f"[登入] API Key: {API_KEY[:8]}...")
api = sj.Shioaji(simulation=False)
api.login(API_KEY, SECRET_KEY, fetch_contract=False)
print("[登入] 成功")

# 取得 TMF 近月合約
api.fetch_contracts(contract_type=sj.constant.ContractType.Future)
contract = api.Contracts.Futures.TMF.TMFR1
print(f"[合約] {contract.code} - {contract.name}")

# 測試從最遠日期開始抓
test_starts = [
    "2020-01-01",
    "2021-01-01",
    "2022-01-01",
    "2023-01-01",
    "2024-01-01",
    "2025-01-01",
]

today = datetime.now().strftime("%Y-%m-%d")
print(f"\n[測試] 今天: {today}")
print("-" * 50)

for start in test_starts:
    try:
        kbars = api.kbars(contract=contract, start=start, end=today)
        if kbars and hasattr(kbars, 'Close') and len(kbars.Close) > 0:
            first_ts = kbars.ts[0]
            last_ts  = kbars.ts[-1]
            print(f"start={start} → {len(kbars.Close):6,} bars  "
                  f"首根={first_ts}  末根={last_ts}")
        else:
            print(f"start={start} → 0 bars（無資料）")
    except Exception as e:
        print(f"start={start} → 錯誤: {e}")

api.logout()
print("\n[完成] 已登出")
