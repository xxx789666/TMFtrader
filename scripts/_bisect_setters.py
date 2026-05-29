"""
Bisect Shioaji setter culprit — Discord expert 第 3 次建議

逐個累加 callback setter、每輪跑 N 秒觀察是否 crash。第一個加進去就死的就是 culprit。

用法：
  python3 _bisect_setters.py <round_idx> [observe_seconds]

round_idx：
  0 = only tick_fop_v1 (baseline)
  1 = + order
  2 = + session_down (api.quote)
  3 = + event (api.quote)
  4 = + init (api.quote)
  5 = + msg (api.quote)
  6 = + quote (api.quote)
  7 = + bidask_fop_v1 (api.quote)
  8 = + bidask_stk_v1 (api.quote)
  9 = + quote_fop_v1 (api.quote)
  10 = + quote_stk_v1 (api.quote)
  11 = + tick_stk_v1 (api.quote)
  12 = + api.set_session_down_callback (top-level)

observe_seconds default 180。
"""
from pathlib import Path
import os, sys, time
from datetime import datetime
import shioaji as sj
from dotenv import load_dotenv

load_dotenv(Path("/home/xx/TMFtrader-src/.env"))

ROUND = int(sys.argv[1]) if len(sys.argv) > 1 else 0
OBSERVE_SEC = int(sys.argv[2]) if len(sys.argv) > 2 else 180

# 累加順序（idx 從 0 開始、每加一個加進來）
SETTERS = [
    ("set_on_tick_fop_v1_callback", "quote"),   # 0 baseline
    ("set_order_callback",          "api"),     # 1 (prod 用)
    ("set_session_down_callback",   "quote"),   # 2
    ("set_event_callback",          "quote"),   # 3
    ("set_init_callback",           "quote"),   # 4
    ("set_msg_callback",            "quote"),   # 5
    ("set_quote_callback",          "quote"),   # 6
    ("set_on_bidask_fop_v1_callback","quote"),  # 7
    ("set_on_bidask_stk_v1_callback","quote"),  # 8
    ("set_on_quote_fop_v1_callback", "quote"),  # 9
    ("set_on_quote_stk_v1_callback", "quote"),  # 10
    ("set_on_tick_stk_v1_callback",  "quote"),  # 11
    ("set_session_down_callback",    "api"),    # 12 top-level
]

print(f"=== Bisect round {ROUND}: {SETTERS[ROUND][0]} (on {SETTERS[ROUND][1]}) ===")
print(f"=== Will register {ROUND+1} setter(s), observe {OBSERVE_SEC}s ===")
print(f"=== Started at {datetime.now():%H:%M:%S} ===")

api = sj.Shioaji(simulation=False)
api.login(
    api_key=os.environ["SHIOAJI_API_KEY"],
    secret_key=os.environ["SHIOAJI_SECRET_KEY"],
    receive_window=300000,
    fetch_contract=False,
)
print(f"[{datetime.now():%H:%M:%S}] Login OK")

api.fetch_contracts(contracts_timeout=30000)
print(f"[{datetime.now():%H:%M:%S}] fetch_contracts done")

ca_path = os.environ.get("SHIOAJI_CA_PATH", "")
ca_pw = os.environ.get("SHIOAJI_CA_PASSWORD", "")
person_id = os.environ.get("SHIOAJI_PERSON_ID", "")
if ca_path:
    api.activate_ca(ca_path=ca_path, ca_passwd=ca_pw, person_id=person_id)
    print(f"[{datetime.now():%H:%M:%S}] CA activated")

time.sleep(2)

# 累加註冊 setter 0..ROUND
_strong_refs = []  # 全部存強 ref 防 GC
for idx in range(ROUND + 1):
    name, scope = SETTERS[idx]
    target = api.quote if scope == "quote" else api

    def make_noop(slot_name):
        def _noop(*args, **kwargs):
            pass
        _noop.__name__ = f"noop_{slot_name}"
        return _noop

    fn = make_noop(name)
    setter = getattr(target, name, None)
    if not callable(setter):
        print(f"[{datetime.now():%H:%M:%S}] SKIP idx={idx} {scope}.{name} not callable")
        continue
    try:
        setter(fn)
        _strong_refs.append(fn)
        print(f"[{datetime.now():%H:%M:%S}] OK idx={idx} {scope}.{name} registered")
    except Exception as e:
        print(f"[{datetime.now():%H:%M:%S}] ERR idx={idx} {scope}.{name}: {e}")

# 訂閱 TMFR1 tick（觸發 callback 流量）
contract = api.Contracts.Futures.TMF.TMFR1
api.quote.subscribe(contract, quote_type=sj.constant.QuoteType.Tick,
                    version=sj.constant.QuoteVersion.v1)
print(f"[{datetime.now():%H:%M:%S}] Subscribed TMF tick")

# 觀察
print(f"[{datetime.now():%H:%M:%S}] === 觀察 {OBSERVE_SEC}s 開始 ===")
ticks = OBSERVE_SEC // 30
for i in range(ticks):
    time.sleep(30)
    print(f"[{datetime.now():%H:%M:%S}] +{(i+1)*30}s elapsed, alive")

# 跑完代表沒撞、印 SURVIVED token 給外層 bash grep
print(f"[{datetime.now():%H:%M:%S}] === ROUND {ROUND} SURVIVED ({OBSERVE_SEC}s clean) ===")

try:
    api.logout()
except Exception:
    pass
