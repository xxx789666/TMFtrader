"""One-shot:平掉 MXF SHORT 部位(2026-06-10 breakout_v7 夜盤誤進的 live 空單)。
- Buy + MKT + IOC + octype=Cover(強制平倉、確保不會誤開新多單)。
- 先 list_positions 確認真的還是 MXF short;已平則 abort。
- 用部位實際 code 對合約(非假設 R1);等成交、印 fill/PnL、推 TG。
⚠️ 前提:執行前**先 kill 管理此倉的 breakout_v7 引擎**,否則 broker 平了引擎還以為有空單、
   它停損會反向再 Buy → 開出多單(平錯)。平完後手動清 data/active_position.json。
⚠️ 需環境變數 CONFIRM_LIVE_ORDER=YES 才會真下單。MXF 小台 pv=50。
模板來自已驗證的 close_short_tmf.py(TMF→MXF、pv10→50)。
"""
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))

import shioaji as sj
from threading import Event

try:
    from core.notify import tg as _tg
except Exception:
    def _tg(msg):
        pass


def tg(msg):
    _tg(f"[MXFShortClose] {msg}")


PV = 50
if os.environ.get("CONFIRM_LIVE_ORDER") != "YES":
    print("[ABORT] 需 CONFIRM_LIVE_ORDER=YES 才會真下單"); sys.exit(2)

print("=" * 60)
print("  CLOSE MXF SHORT POSITION — one-shot")
print(f"  time: {datetime.now().isoformat(timespec='seconds')}")
print("=" * 60)

api = sj.Shioaji(simulation=False)
api.login(api_key=os.environ["SHIOAJI_API_KEY"], secret_key=os.environ["SHIOAJI_SECRET_KEY"],
          receive_window=300000, fetch_contract=False)
print(f"Login OK. account_id={api.futopt_account.account_id} signed={api.futopt_account.signed}")
api.activate_ca(ca_path=os.environ.get("SHIOAJI_CA_PATH", ""),
                ca_passwd=os.environ.get("SHIOAJI_CA_PASSWORD", ""),
                person_id=os.environ.get("SHIOAJI_PERSON_ID", ""))
if not api.futopt_account.signed:
    print("[ABORT] futopt_account.signed=False"); api.logout(); sys.exit(1)
print("CA activated.")

# 1) 確認還有 MXF short
positions = api.list_positions(api.futopt_account)
mxf_short = None
for p in positions:
    if getattr(p, "code", "").startswith("MXF") and str(getattr(p, "direction", "")).endswith("Sell"):
        mxf_short = p; break
if mxf_short is None:
    print("[ABORT] 帳號目前沒有 MXF short 部位、可能已被平、不做動作")
    tg("帳號已無 MXF short、不平了"); api.logout(); sys.exit(0)

qty_to_close = int(mxf_short.quantity)
avg_entry = float(mxf_short.price)
pos_code = getattr(mxf_short, "code", "")
print(f"\nPosition: MXF SHORT x{qty_to_close} @ avg {avg_entry:.1f} code={pos_code} "
      f"last={getattr(mxf_short,'last_price','?')} pnl={getattr(mxf_short,'pnl','?')}")

# 2) contract（用部位實際 code 對；對不到退 MXFR1 前月）
api.fetch_contracts(contracts_timeout=30000)
contract = None
for c in api.Contracts.Futures.MXF:
    if getattr(c, "code", "") == pos_code:
        contract = c; break
if contract is None:
    contract = api.Contracts.Futures.MXF.MXFR1
    print(f"[WARN] 用部位 code {pos_code} 對不到合約、退用 MXFR1={contract.code}")
print(f"Contract: {contract.code}")

# 3) callback
deal_evt = Event(); deal_info = {"price": 0.0, "qty": 0}
def order_cb(stat, msg):
    print(f"[CB] {stat}")
    try:
        if stat == sj.constant.OrderState.FuturesDeal:
            if str(msg.get("code", "")).startswith("MXF") and msg.get("action", "") == "Buy":
                deal_info["price"] = float(msg.get("price", 0)); deal_info["qty"] = int(msg.get("quantity", 0))
                deal_evt.set()
        elif stat == sj.constant.OrderState.FuturesOrder:
            op = msg.get("operation", {}); print(f"  op_code={op.get('op_code','')} op_msg={op.get('op_msg','')}")
    except Exception as e:
        print(f"[CB err] {e}")
api.set_order_callback(order_cb); time.sleep(1)

# 4) Buy + MKT + IOC + Cover
order = api.Order(action=sj.constant.Action.Buy, price=0, quantity=qty_to_close,
                  price_type=sj.constant.FuturesPriceType.MKT, order_type=sj.constant.OrderType.IOC,
                  octype=sj.constant.FuturesOCType.Cover, account=api.futopt_account)
print(f"\n>>> SUBMIT: BUY MXF x{qty_to_close} MKT IOC octype=Cover @ {datetime.now().strftime('%H:%M:%S')}")
trade = api.place_order(contract, order)
print(f"Trade submitted: status={trade.status}")
tg(f"已送平空單 Buy MXF x{qty_to_close} MKT IOC Cover, 等成交")

# 5) 等成交(short PnL: 進場-平倉)
if deal_evt.wait(timeout=10):
    fill_p = deal_info["price"]; fill_q = deal_info["qty"]
    gross = (avg_entry - fill_p) * fill_q * PV
    commission = 18 * fill_q
    tax = (int((avg_entry * PV * 0.00002)) + 1) * fill_q + (int((fill_p * PV * 0.00002)) + 1) * fill_q
    net = gross - commission - tax
    msg = (f"✅ 平空 MXF Buy x{fill_q} @ {fill_p:.0f} 進 {avg_entry:.1f} 毛 {gross:+.0f} 手 {commission} 稅 {tax} 淨 {net:+.0f} NT")
    print(f"\n{msg}"); tg(msg)
else:
    print("[WARN] 10s 無 deal callback、再查 list_positions")
    api.update_status(api.futopt_account)
    still = any(getattr(p, 'code', '').startswith('MXF') and str(getattr(p, 'direction', '')).endswith('Sell')
               for p in api.list_positions(api.futopt_account))
    m = "🚨 平倉未成交！MXF 還是 short、請立即手動 app 平倉！" if still else "⚠️ deal callback 漏推但部位已 flat"
    print(m); tg(m)

api.logout(); print("\nDone")
