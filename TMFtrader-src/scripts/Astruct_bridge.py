# -*- coding: utf-8 -*-
"""A_struct 橋接(本機跑,非 VPS) — 把人工審核後的夜盤閘門決定推到 VPS 真 tick 執行器。

讀 lab 管線輸出:
  Astruct_queue.csv(自動:dir/break_lvl/abstain)+ Astruct_decisions.csv(人工:skip/override/ok)
解析(忠實對齊 Astruct_exec.py)→ 寫 next_signal.json → scp 推到 VPS data/astruct_nightgate/。

決定規則:
  人工 skip            → action=skip(不做)
  人工 override        → action=go, dir/break=人工 final
  人工 ok              → action=go, dir/break=自動
  無人工 + 非 abstain  → action=go, dir/break=自動(自動未審;對齊 Astruct_exec 預設)
  無人工 + abstain     → action=skip(碎盤/無回檔程式舉手、人工沒確認 → 安全不做)

排程:本機 Windows 工作排程 ~08:35(人工審核 08:30 截止後);ENTRY 預設今天。
用法: python Astruct_bridge.py [YYYY-MM-DD]
"""
import csv
import json
import subprocess
import sys
import time
import datetime as dt
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")   # 避 Windows cp950 撞 CJK/emoji
except Exception:
    pass

LAB = Path(r"C:\Users\xx\Desktop\tmf-strategy-lab-main\tmf-strategy-lab-main")
QUEUE = LAB / "data" / "forward" / "Astruct_queue.csv"
DECISIONS = LAB / "data" / "forward" / "Astruct_decisions.csv"
# STAGE 放 ASCII 路徑(lab repo)→ 避 cmd/.bat 在 CJK 路徑 cp950 garbled;Python 內部處理 CJK 字串無礙
STAGE = Path(r"C:\Users\xx\Desktop\tmf-strategy-lab-main\tmf-strategy-lab-main\data\forward\astruct_ng_signal.json")
STAGE_WSL = "/mnt/c/Users/xx/Desktop/tmf-strategy-lab-main/tmf-strategy-lab-main/data/forward/astruct_ng_signal.json"
VPS_DEST = "ultratrader-night:/home/xx/TMFtrader-src/data/astruct_nightgate/next_signal.json"
ZONE = "asia-east1-b"

ENTRY = sys.argv[1] if len(sys.argv) > 1 else dt.date.today().isoformat()


def rows_for(path, entry):
    if not path.exists():
        return []
    with open(path, encoding="utf-8-sig") as f:
        return [r for r in csv.DictReader(f) if str(r.get("entry")) == entry]


def build_signal(entry):
    """讀 queue + decisions → 當下最終決定 dict(None=不在 queue)。"""
    qr = rows_for(QUEUE, entry)
    if not qr:
        return None
    q = qr[-1]
    auto_dir = q.get("dir")                       # '空' / '多'
    auto_break = q.get("break_lvl")
    abstain = str(q.get("abstain", "")).strip().lower() in ("true", "1", "yes")

    action, fdir, fbreak, src = "go", auto_dir, auto_break, "自動未審"
    dr = rows_for(DECISIONS, entry)
    if dr:
        last = dr[-1]
        act = str(last.get("action", "")).strip()
        if act == "skip":
            action, src = "skip", "人工跳過"
        elif act == "override":
            fdir = str(last.get("final_dir") or auto_dir)
            fb = str(last.get("final_break") or "")
            fbreak = fb if fb not in ("", "nan") else auto_break
            src = "人工覆寫"
        elif act == "ok":
            src = "人工採用"
    elif abstain:
        action, src = "skip", "abstain無人工確認"   # 安全:碎盤/無回檔不自動做

    return {"trade_date": entry, "action": action, "dir": fdir,
            "break_lvl": (None if action == "skip" else float(fbreak)),
            "asof": dt.datetime.now().isoformat(timespec="seconds"), "source": src}


def push(sig):
    """寫 stage + scp 推 VPS。回傳 True=成功(主迴圈才記為已推、否則下輪重試)。"""
    STAGE.parent.mkdir(parents=True, exist_ok=True)
    STAGE.write_text(json.dumps(sig, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"寫入 {STAGE}\n{json.dumps(sig, ensure_ascii=False)}")
    cmd = ["wsl", "bash", "-lc",
           f'gcloud compute scp "{STAGE_WSL}" {VPS_DEST} --zone={ZONE}']
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        print("scp 逾時(60s)→ 略過本輪,下輪重試")   # 迴圈內不卡死、保留下一輪推送機會
        return False
    if r.returncode == 0:
        print("scp 推 VPS OK")
        return True
    print(f"scp 失敗(rc={r.returncode}): {r.stderr.strip()}")
    return False


# 時效防呆:08:25-08:45 視窗內持續重推,讓閘門前(含 08:35 後)才到的人工 skip/override
# 一定推到 VPS(nightgate 拖到 08:45 才鎖、人工立刻鎖)。視窗外執行(手動/測試)=單次。
GATE = dt.time(8, 45)
WIN_OPEN = dt.time(8, 25)
POLL_SEC = 12


def main():
    last_key = None
    while True:
        sig = build_signal(ENTRY)
        if sig is None:
            print(f"{ENTRY} 不在 queue → 無夜盤判讀 → 不推(VPS fail-closed 不做)")
            return
        key = (sig["action"], sig["dir"], sig["break_lvl"])   # 比較不含 asof(每次都變)
        if key != last_key:
            if push(sig):
                last_key = key       # 只有推成功才記;失敗保持 last_key 讓下輪重試
        else:
            print(f"(決定無變化 {key} → 不重推)")
        now = dt.datetime.now().time()
        if not (WIN_OPEN <= now < GATE):       # 視窗外(含手動執行)→ 單次收工
            break
        time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
