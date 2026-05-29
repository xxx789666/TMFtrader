"""快速查永豐帳戶餘額 / 期貨保證金 / 持倉、不依賴 start.py。"""
from pathlib import Path
import os, time, shioaji as sj
from dotenv import load_dotenv

load_dotenv(Path("/home/xx/TMFtrader-src/.env"))

api = sj.Shioaji(simulation=False)
api.login(
    api_key=os.environ["SHIOAJI_API_KEY"],
    secret_key=os.environ["SHIOAJI_SECRET_KEY"],
    receive_window=300000,
    fetch_contract=False,
)
time.sleep(5)  # 給 Shioaji session 充分穩定時間

# 啟動 CA（期貨帳戶查詢需要）
ca_path = os.environ.get("SHIOAJI_CA_PATH", "")
ca_pw = os.environ.get("SHIOAJI_CA_PASSWORD", "")
person_id = os.environ.get("SHIOAJI_PERSON_ID", "")
if ca_path and ca_pw and person_id:
    try:
        api.activate_ca(ca_path=ca_path, ca_passwd=ca_pw, person_id=person_id)
        print(f"[CA] activated, 等 8 秒讓 token 同步...")
        time.sleep(8)  # 等 CA token 全 broker 端同步好
    except Exception as e:
        print(f"[CA] 啟用失敗（查餘額不一定需要）: {e}")

print("=" * 60)
print("永豐帳戶資訊")
print("=" * 60)

# 1. 期貨帳戶資訊
print("\n--- 期貨帳戶清單（accounts）---")
try:
    futopt_accounts = [a for a in api.list_accounts() if str(getattr(a, "account_type", "")).lower() in ("future", "futopt", "f")]
    for a in futopt_accounts:
        print(f"  {a}")
except Exception as e:
    print(f"  ERROR: {e}")

# 2. 期貨帳戶餘額 / 權益
print("\n--- 期貨帳戶權益 margin() ---")
try:
    margin = api.margin(account=api.futopt_account)
    for attr in dir(margin):
        if not attr.startswith("_"):
            val = getattr(margin, attr, None)
            if val is not None and not callable(val):
                print(f"  {attr}: {val}")
except Exception as e:
    print(f"  ERROR: {e}")

# 3. 持倉
print("\n--- 期貨持倉 list_positions() ---")
try:
    positions = api.list_positions(account=api.futopt_account)
    if not positions:
        print("  （無持倉）")
    for p in positions:
        print(f"  {p}")
except Exception as e:
    print(f"  ERROR: {e}")

# 4. 結算明細（盈虧）
print("\n--- 期貨損益 list_profit_loss(近 1 個月) ---")
try:
    from datetime import datetime, timedelta
    end = datetime.now().date()
    start = end - timedelta(days=30)
    pls = api.list_profit_loss(
        account=api.futopt_account,
        begin_date=start.strftime("%Y-%m-%d"),
        end_date=end.strftime("%Y-%m-%d"),
    )
    if not pls:
        print(f"  （近 30 天無已實現損益、{start} → {end}）")
    for p in pls[:5]:
        print(f"  {p}")
    if len(pls) > 5:
        print(f"  ... 共 {len(pls)} 筆")
except Exception as e:
    print(f"  ERROR: {e}")

api.logout()
print("\n" + "=" * 60)
