"""
Min-verify Shioaji live mode callback issue (Discord expert 建議)

把所有 callback body 換成 noop、看 live mode 是否還會 crash。
- noop 還 crash → SDK / register pattern 問題（升 rshioaji 1.5.13）
- noop 不 crash → 我們 callback body 有 bug（修 broker.py 內 _order_cb / on_tick）

用法：21:00 後執行：
  ssh ultratrader-night
  cd ~/TMFtrader-src && source .venv/bin/activate
  python3 /tmp/_min_verify_live.py
"""
from pathlib import Path
import os, time, sys
from datetime import datetime
import shioaji as sj
from dotenv import load_dotenv

load_dotenv(Path("/home/xx/TMFtrader-src/.env"))

print("=" * 60)
print("Min-verify Shioaji live mode callback race")
print("=" * 60)

api = sj.Shioaji(simulation=False)
api.login(
    api_key=os.environ["SHIOAJI_API_KEY"],
    secret_key=os.environ["SHIOAJI_SECRET_KEY"],
    receive_window=300000,
    fetch_contract=False,
)
print(f"[{datetime.now():%H:%M:%S}] Login OK")
time.sleep(3)

api.fetch_contracts(contracts_timeout=30000)
print(f"[{datetime.now():%H:%M:%S}] fetch_contracts done")

# Activate CA（live mode 必要）
ca_path = os.environ.get("SHIOAJI_CA_PATH", "")
ca_pw = os.environ.get("SHIOAJI_CA_PASSWORD", "")
person_id = os.environ.get("SHIOAJI_PERSON_ID", "")
if ca_path:
    api.activate_ca(ca_path=ca_path, ca_passwd=ca_pw, person_id=person_id)
    print(f"[{datetime.now():%H:%M:%S}] CA activated")

time.sleep(2)

# Noop callbacks ↓
def _noop_order_cb(stat, msg):
    """完全空、Python 端做 nothing"""
    pass

def _noop_tick_cb(exchange, tick):
    """完全空、Python 端做 nothing"""
    pass

api.set_order_callback(_noop_order_cb)
api.quote.set_on_tick_fop_v1_callback(_noop_tick_cb)
print(f"[{datetime.now():%H:%M:%S}] Registered NOOP callbacks")

# 訂閱 TMF tick
contract = api.Contracts.Futures.TMF.TMFR1
api.quote.subscribe(contract, quote_type=sj.constant.QuoteType.Tick,
                    version=sj.constant.QuoteVersion.v1)
print(f"[{datetime.now():%H:%M:%S}] Subscribed TMF tick")

# Live mode 用 5 分鐘觀察、若 process 還在跑 = noop 沒爆
print(f"[{datetime.now():%H:%M:%S}] === 開始 5 分鐘觀察期 ===")
for i in range(60):  # 5 min = 60 × 5s
    time.sleep(5)
    elapsed = (i + 1) * 5
    if elapsed % 30 == 0:
        print(f"[{datetime.now():%H:%M:%S}] {elapsed}s elapsed、process 還活著")

print(f"[{datetime.now():%H:%M:%S}] === 5 分鐘完整跑完、NO CRASH ===")
print("→ noop callback 不 crash = 我們的 callback body 有 bug、需修 broker.py")
print("→ 結束 logout")
api.logout()
