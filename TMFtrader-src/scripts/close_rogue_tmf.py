"""
One-shot: 平掉「外部 rogue script 進的」TMF long 2 口
- 用 Sell + IOC + MKT + octype=Cover (強制平倉 — 確保不會誤開新空單)
- 等成交回報、印 fill price、PnL、推 TG
- 不影響 engine 的 active_position.json（因為 engine 從來不知道這部位）
注意：執行前會先 list_positions 確認真的還是 long 2 口；如果已被平、立即 abort。
"""
import os, sys, time, json
from datetime import datetime
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, ".env"))

import shioaji as sj
from threading import Event

# ─── TG ─────────────────────────────────────────────
try:
    from core.notify import tg as _tg
except Exception:
    def _tg(msg): pass

def tg(msg): _tg(f"[RogueClose] {msg}")

# ─── 確認 / 二次確認 ─────────────────────────────
print("=" * 60)
print("  CLOSE ROGUE TMF POSITION — one-shot")
print(f"  time: {datetime.now().isoformat(timespec='seconds')}")
print("=" * 60)

api = sj.Shioaji(simulation=False)
api.login(
    api_key=os.environ["SHIOAJI_API_KEY"],
    secret_key=os.environ["SHIOAJI_SECRET_KEY"],
    receive_window=300000,
    fetch_contract=False,
)
print(f"Login OK. account_id={api.futopt_account.account_id} signed={api.futopt_account.signed}")

# CA activate (place_order 要)
api.activate_ca(
    ca_path=os.environ.get("SHIOAJI_CA_PATH", ""),
    ca_passwd=os.environ.get("SHIOAJI_CA_PASSWORD", ""),
    person_id=os.environ.get("SHIOAJI_PERSON_ID", ""),
)
if not api.futopt_account.signed:
    print("[ABORT] futopt_account.signed=False")
    api.logout()
    sys.exit(1)
print("CA activated.")

# ─── 1) 確認還有 long ────────────────────────────
positions = api.list_positions(api.futopt_account)
tmf_long = None
for p in positions:
    code = getattr(p, "code", "")
    if code.startswith("TMF") and str(getattr(p, "direction", "")).endswith("Buy"):
        tmf_long = p
        break

if tmf_long is None:
    print("[ABORT] 帳號目前沒有 TMF long 部位、可能已被平、不做動作")
    tg("帳號目前已沒有 TMF long、不平了")
    api.logout()
    sys.exit(0)

qty_to_close = int(tmf_long.quantity)
avg_entry = float(tmf_long.price)
last_price = float(tmf_long.last_price)
print(f"\nPosition: TMF long x{qty_to_close} @ avg {avg_entry:.1f}  last={last_price:.1f}")
print(f"Unrealized pnl: {tmf_long.pnl}")

# ─── 2) 用 contract TMFR1 (跟 night_orb 同合約) ───
api.fetch_contracts(contracts_timeout=30000)
contract = api.Contracts.Futures.TMF.TMFR1
print(f"Contract: {contract.code} ({getattr(contract, 'name', '')})")

# ─── 3) 註冊 callback 等成交 ───────────────────
deal_evt = Event()
deal_info = {"price": 0.0, "qty": 0, "ts": 0}

def order_cb(stat, msg):
    print(f"[CB] {stat}")
    try:
        if stat == sj.constant.OrderState.FuturesDeal:
            if msg.get("code", "") == "TMF" and msg.get("action", "") == "Sell":
                deal_info["price"] = float(msg.get("price", 0))
                deal_info["qty"]   = int(msg.get("quantity", 0))
                deal_info["ts"]    = float(msg.get("ts", 0))
                deal_evt.set()
        elif stat == sj.constant.OrderState.FuturesOrder:
            op = msg.get("operation", {})
            print(f"  op_code={op.get('op_code','')} op_msg={op.get('op_msg','')}")
    except Exception as e:
        print(f"[CB err] {e}")

api.set_order_callback(order_cb)
time.sleep(1)

# ─── 4) 送 Sell + MKT + IOC + octype=Cover ─────
order = api.Order(
    action=sj.constant.Action.Sell,
    price=0,
    quantity=qty_to_close,
    price_type=sj.constant.FuturesPriceType.MKT,
    order_type=sj.constant.OrderType.IOC,
    octype=sj.constant.FuturesOCType.Cover,   # ← 強制平倉
    account=api.futopt_account,
)
print(f"\n>>> SUBMIT: SELL TMF x{qty_to_close} MKT IOC octype=Cover  @ {datetime.now().strftime('%H:%M:%S')}")
trade = api.place_order(contract, order)
print(f"Trade submitted: status={trade.status}")
tg(f"已送平倉單 Sell TMF x{qty_to_close} MKT IOC Cover, 等成交回報")

# ─── 5) 等成交 ────────────────────────────────
if deal_evt.wait(timeout=10):
    fill_p = deal_info["price"]
    fill_q = deal_info["qty"]
    # 算 PnL (用 TMF 點值 10)
    pnl_pts = (fill_p - avg_entry) * fill_q
    gross = pnl_pts * 10
    commission = 18 * fill_q
    tax = (int((avg_entry * 10 * 0.00002)) + 1) * fill_q + (int((fill_p * 10 * 0.00002)) + 1) * fill_q
    net = gross - commission - tax
    msg = (f"✅ 平倉 TMF Sell x{fill_q} @ {fill_p:.0f}  進 {avg_entry:.1f}  "
           f"毛 {gross:+.0f}  手 {commission}  稅 {tax}  淨 {net:+.0f} NT")
    print(f"\n{msg}")
    tg(msg)
else:
    print("[WARN] 10s 沒收到 deal callback、用 list_positions 再查一次")
    api.update_status(api.futopt_account)
    positions2 = api.list_positions(api.futopt_account)
    still_long = any(getattr(p,'code','').startswith('TMF') and str(getattr(p,'direction','')).endswith('Buy') for p in positions2)
    if still_long:
        msg = f"🚨 平倉未成交！TMF 還是 long、請立即手動 app 平倉！"
        print(msg)
        tg(msg)
    else:
        msg = "⚠️ Deal callback 沒收到但部位已歸 flat、可能 deal event 漏推"
        print(msg)
        tg(msg)

api.logout()
print("\nDone")
