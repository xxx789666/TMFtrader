"""永豐 Shioaji API 開通測試腳本（期貨）

目的：跑這支腳本、永豐後台會收到「登入 + 下單」trace 紀錄，約 5 分鐘審核
       通過後 simulation=False（live）才能下實單。

執行條件（永豐規定）：
  - 已線上簽署「API 電子交易風險預告書暨使用同意書」
  - **週一~週五 08:00–20:00 跑**（18:00–20:00 限台灣 IP）
  - 證券 + 期貨需分別測；本腳本只測期貨（TXF 近月）

執行：
  cd "C:\\Users\\xx\\Desktop\\永豐-自動化交易\\TMFtrader-src"
  python "C:\\Users\\xx\\Desktop\\vps永豐微台指\\scripts\\shioaji_api_test.py"

判斷通過：
  - 看到 "signed=True"
  - place_order 回應含 "Submitted"（非 "Failed"）
  - Event Code 0 / Response Code 0
  → 整段 stdout 截圖留底
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path

# ============== Step 0: 預檢時段 ==============
now = datetime.now()
weekday = now.weekday()  # 0=Mon, 6=Sun
hour = now.hour
print(f"[{now}] 開始 Shioaji API 測試")
print(f"  weekday={weekday} (0=Mon ... 4=Fri), hour={hour}")
if weekday >= 5:
    sys.exit("❌ 永豐 API 測試只能週一~週五跑（現在是週末）")
if not (8 <= hour < 20):
    sys.exit(f"❌ 永豐 API 測試時段是 08:00–20:00（現在 {hour:02d}:{now.minute:02d}）。明天 08:00–20:00 內再跑。")
if 18 <= hour < 20:
    print("⚠️ 18:00–20:00 限台灣 IP。若你在 VPN / 境外、會被擋。")

# ============== Step 0.5: 找 .env 並手動解析（不依賴 python-dotenv）==============
CANDIDATES = [
    Path.cwd() / ".env",
    Path(__file__).resolve().parent.parent.parent / "永豐-自動化交易" / "TMFtrader-src" / ".env",
    Path("C:/Users/xx/Desktop/永豐-自動化交易/TMFtrader-src/.env"),
    Path(__file__).resolve().parent.parent / "TMFtrader-src" / ".env",
]
env_file = next((p for p in CANDIDATES if p.exists()), None)
if env_file is None:
    print("❌ 找不到 .env，已試過：")
    for p in CANDIDATES:
        print(f"   - {p}")
    sys.exit(2)
print(f"  讀 .env: {env_file}")

# 手動解析 KEY=VALUE
with open(env_file, "r", encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)

# 抓 key（確認都有）
required = ["SHIOAJI_API_KEY", "SHIOAJI_SECRET_KEY", "SHIOAJI_CA_PATH", "SHIOAJI_CA_PASSWORD", "SHIOAJI_PERSON_ID"]
missing = [k for k in required if not os.environ.get(k)]
if missing:
    sys.exit(f"❌ .env 缺欄位: {missing}")
API_KEY     = os.environ["SHIOAJI_API_KEY"]
SECRET_KEY  = os.environ["SHIOAJI_SECRET_KEY"]
CA_PATH     = os.environ["SHIOAJI_CA_PATH"]
CA_PASSWORD = os.environ["SHIOAJI_CA_PASSWORD"]
PERSON_ID   = os.environ["SHIOAJI_PERSON_ID"]

# 驗證憑證檔存在
if not Path(CA_PATH).exists():
    sys.exit(f"❌ 憑證找不到: {CA_PATH}")
print(f"  ✓ .env OK; 憑證 {CA_PATH} 存在 ({Path(CA_PATH).stat().st_size} bytes)")

import shioaji as sj
print(f"  Shioaji 版本: {sj.__version__}（需 ≥ 1.2）")

# ============== Step 1: Login（simulation=True 沙箱） ==============
print("\n[Step 1] 登入 simulation 環境...")
api = sj.Shioaji(simulation=True)
accounts = api.login(API_KEY, SECRET_KEY)
print(f"  登入成功，accounts 數量={len(accounts)}")

fut_account = api.futopt_account
print(f"  futopt_account: {fut_account}")
signed = getattr(fut_account, "signed", None)
print(f"  signed = {signed}    ← 應為 True；若 False 表示同意書還沒簽 或 還沒測試過")

# ============== Step 2: 啟用憑證 ==============
print("\n[Step 2] activate_ca（啟用憑證）...")
ca_ok = api.activate_ca(ca_path=CA_PATH, ca_passwd=CA_PASSWORD, person_id=PERSON_ID)
print(f"  activate_ca = {ca_ok}    ← 應為 True")

# ============== Step 3: 取 TXF 近月（不要 R1/R2 遠月） ==============
print("\n[Step 3] 取 TXF 近月合約...")
all_txf = [c for c in api.Contracts.Futures.TXF if c.code[-2:] not in ("R1", "R2")]
near = min(all_txf, key=lambda c: c.delivery_date)
print(f"  TXF 近月 = code={near.code}, delivery={near.delivery_date}, name={near.name}")

# ============== Step 4: 下測試單（用 contract.reference 動態合理價、避免漲跌幅 reject）==============
ref_price = getattr(near, "reference", None)
if ref_price is None or ref_price <= 0:
    ref_price = 15000  # fallback 用官方舊範例的固定價（會被 reject 但 trace 仍計入）
print(f"\n[Step 4] 下測試單 (Buy 1 lot {near.code} @ {ref_price} ROD)...")
order = sj.order.FuturesOrder(
    action=sj.constant.Action.Buy,
    price=ref_price,
    quantity=1,
    price_type=sj.constant.FuturesPriceType.LMT,
    order_type=sj.constant.OrderType.ROD,
    octype=sj.constant.FuturesOCType.Auto,
    account=fut_account,
)
trade = api.place_order(near, order)
time.sleep(0.5)
api.update_status()  # 同步真實 status
status = getattr(trade.status, "status", "?")
order_id = getattr(trade.status, "order_id", "?")
print(f"  status = {status}    ← 應為 'Submitted'（非 'Failed'）")
print(f"  order_id = {order_id}")

# ============== Step 5: 等 ≥ 1 秒 ==============
print("\n[Step 5] sleep 2 秒（永豐規定兩筆委託需間隔 ≥ 1s）...")
time.sleep(2)

# ============== Step 6: 取消測試單 ==============
print("\n[Step 6] 取消剛才的測試單...")
try:
    api.cancel_order(trade)
    time.sleep(1)
    api.update_status()
    print(f"  cancel 後 status = {trade.status.status}")
except Exception as e:
    print(f"  cancel 失敗（沙箱可能不支援、可忽略）: {e}")

# ============== Step 7: 摘要 ==============
print("\n" + "=" * 60)
print("測試完成 —— 截圖整段 stdout 留底備查")
print("=" * 60)
print(f"  signed:    {signed}")
print(f"  ca_ok:     {ca_ok}")
print(f"  order:     {status}")
print(f"  時間:      {datetime.now()}")
print()
print("通過條件：signed=True + ca_ok=True + status=Submitted")
print("永豐後台收到此 trace 後約 5 分鐘審核完。")
print("審核通過 = futopt_account.signed 變 True（重新跑 login 確認）")
print("通過後 simulation=False 才能下實單。")
