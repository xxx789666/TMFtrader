"""
補資料 + 重跑回測完整流程
1. 暫停 watchdog（避免 Shioaji session 衝突）
2. 下載 TMF 1m 資料（2026-04-18 ~ 今天）並合併進 parquet
3. 補夜盤 ORB 特徵 + 信號
4. 重新生成雙策略回測報告
5. 重啟 watchdog

用法：python scripts/fetch_and_update_all.py
"""
import sys, os, time, signal, warnings, subprocess
warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')

import psutil
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

# ── 設定 ─────────────────────────────────────────────────
DATA_1M   = Path('data/historical/tmf_5y_1m.parquet')
FETCH_END = datetime(2026, 4, 27)   # 補到 4/27

# ════════════════════════════════════════════════════════
# Step 0: 找出並暫停 watchdog
# ════════════════════════════════════════════════════════
print("[Step 0] 暫停 watchdog 程序...")
watchdog_procs = []
for p in psutil.process_iter(['pid', 'name', 'cmdline']):
    try:
        cmd = ' '.join(p.info['cmdline'] or [])
        if 'watchdog' in cmd and 'python' in cmd.lower():
            watchdog_procs.append(p)
            print(f"  找到 watchdog PID={p.pid}: {cmd[cmd.find('watchdog'):cmd.find('watchdog')+40]}")
    except Exception:
        pass

for p in watchdog_procs:
    try:
        p.terminate()
        print(f"  已停止 PID={p.pid}")
    except Exception as e:
        print(f"  停止 PID={p.pid} 失敗: {e}")

# 等待 server process 結束（watchdog 會連帶停 server）
print("  等待 3 秒讓 server 完全關閉...")
time.sleep(3)

# ════════════════════════════════════════════════════════
# Step 1: 確認現有資料最後日期
# ════════════════════════════════════════════════════════
print("\n[Step 1] 確認現有資料範圍...")
df_exist = pd.read_parquet(DATA_1M)
df_exist['datetime'] = pd.to_datetime(df_exist['datetime'])
last_dt = df_exist['datetime'].max()
print(f"  現有最後一筆: {last_dt}")

fetch_start = (last_dt + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
fetch_start_str = fetch_start.strftime('%Y-%m-%d')
fetch_end_str   = FETCH_END.strftime('%Y-%m-%d')

if fetch_start > FETCH_END:
    print(f"  資料已是最新（{last_dt.date()}），無需下載。")
else:
    print(f"  需下載：{fetch_start_str} ~ {fetch_end_str}")

    # ════════════════════════════════════════════════════
    # Step 2: Shioaji 下載
    # ════════════════════════════════════════════════════
    print("\n[Step 2] 登入 Shioaji 並下載 TMF 1m K 棒...")
    import shioaji as sj

    # 優先用備用 Key（410.txt），若無則用 .env
    API_KEY    = os.environ.get('SHIOAJI_API_KEY', '')
    SECRET_KEY = os.environ.get('SHIOAJI_SECRET_KEY', '')

    api = sj.Shioaji(simulation=False)
    try:
        api.login(API_KEY, SECRET_KEY, receive_window=300000, fetch_contract=False)
        print("  登入成功")
    except Exception as e:
        print(f"  登入失敗: {e}")
        print("  重啟 watchdog 並退出...")
        for wd in watchdog_procs:
            try:
                cmd = ' '.join(wd.cmdline())
                subprocess.Popen(cmd, shell=True)
            except Exception:
                pass
        sys.exit(1)

    api.fetch_contracts()
    try:
        contract = api.Contracts.Futures.TMF.TMFR1
        print(f"  合約: {contract.code} {contract.name}")
    except Exception as e:
        print(f"  取得合約失敗: {e}")
        api.logout()
        sys.exit(1)

    # 分批拉（每批 5 天）
    all_new_bars = []
    current_start = fetch_start
    while current_start <= FETCH_END:
        batch_end = min(current_start + timedelta(days=4), FETCH_END)
        s_str = current_start.strftime('%Y-%m-%d')
        e_str = batch_end.strftime('%Y-%m-%d')
        print(f"  下載 {s_str} ~ {e_str} ...", end='', flush=True)
        try:
            kbars = api.kbars(contract=contract, start=s_str, end=e_str)
            if kbars and hasattr(kbars, 'Close') and len(kbars.Close) > 0:
                for i in range(len(kbars.Close)):
                    raw_ts = kbars.ts[i]
                    if isinstance(raw_ts, (int, float)):
                        epoch_sec = raw_ts / 1e9 if raw_ts > 1e12 else raw_ts
                        ts = datetime.fromtimestamp(epoch_sec)
                    else:
                        ts = pd.Timestamp(raw_ts).to_pydatetime().replace(tzinfo=None)
                    all_new_bars.append({
                        'datetime': ts,
                        'open':   float(kbars.Open[i]),
                        'high':   float(kbars.High[i]),
                        'low':    float(kbars.Low[i]),
                        'close':  float(kbars.Close[i]),
                        'volume': int(kbars.Volume[i]),
                    })
                print(f" {len(kbars.Close)} 根")
            else:
                print(" 無資料（休市？）")
        except Exception as e:
            print(f" 錯誤: {e}")
        current_start = batch_end + timedelta(days=1)

    api.logout()
    print("  已登出 Shioaji")

    if not all_new_bars:
        print("  沒有新資料可以合併。")
    else:
        df_new = pd.DataFrame(all_new_bars)
        df_new['datetime'] = pd.to_datetime(df_new['datetime'])
        # 只保留比現有資料新的
        df_new = df_new[df_new['datetime'] > last_dt]
        df_new = df_new.drop_duplicates('datetime').sort_values('datetime').reset_index(drop=True)

        df_merged = pd.concat([df_exist, df_new], ignore_index=True)
        df_merged = df_merged.drop_duplicates('datetime').sort_values('datetime').reset_index(drop=True)
        df_merged.to_parquet(DATA_1M, index=False)
        print(f"  合併完成：{len(df_new)} 根新資料，共 {len(df_merged):,} 根")
        print(f"  新資料期間：{df_new['datetime'].min()} ~ {df_new['datetime'].max()}")

# ════════════════════════════════════════════════════════
# Step 3: 補夜盤 ORB 資料
# ════════════════════════════════════════════════════════
print("\n[Step 3] 補夜盤 ORB 資料...")
import subprocess
r = subprocess.run([sys.executable, 'scripts/update_night_orb_data.py'],
                   capture_output=True, text=True, encoding='utf-8')
print(r.stdout)
if r.returncode != 0:
    print("錯誤:", r.stderr[-500:])

# ════════════════════════════════════════════════════════
# Step 4: 重新生成回測報告
# ════════════════════════════════════════════════════════
print("\n[Step 4] 重新生成雙策略回測報告...")
r = subprocess.run([sys.executable, 'scripts/gen_dual_report.py'],
                   capture_output=True, text=True, encoding='utf-8')
print(r.stdout)
if r.returncode != 0:
    print("錯誤:", r.stderr[-500:])

# ════════════════════════════════════════════════════════
# Step 5: 重啟 watchdog
# ════════════════════════════════════════════════════════
print("\n[Step 5] 重啟 watchdog...")
wd_dir = Path('.').resolve()

for p in watchdog_procs:
    try:
        orig_cmd = p.cmdline()
        # 用 pythonw 重啟（背景執行）
        new_cmd = orig_cmd.copy()
        new_cmd[0] = new_cmd[0].replace('python.exe', 'pythonw.exe')
        subprocess.Popen(new_cmd, cwd=str(wd_dir),
                         creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0)
        print(f"  已重啟: {' '.join(new_cmd[1:])}")
    except Exception as e:
        print(f"  重啟失敗: {e}，請手動執行 python scripts/watchdog.py")

print("\n完成！")
