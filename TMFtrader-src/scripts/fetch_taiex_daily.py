# -*- coding: utf-8 -*-
"""抓台股加權指數(TSE 001 / 大盤)日K,增量更新到 data/taiex_daily.csv。

來源:Shioaji api.kbars(1 分 K)→ resample 成日K(O=first/H=max/L=min/C=last)。
官方限制:指數歷史最早 2020-03-02。

金鑰(唯讀「調數據」金鑰,勿用下單金鑰):
  優先讀環境變數 SJ_DATA_KEY / SJ_DATA_SEC;
  沒有則從金鑰檔解析「數據」段(預設 C:\\Users\\xx\\Desktop\\vps api.txt,可用 SJ_KEYFILE 覆蓋)。
  → 金鑰不寫死在 repo。

用法(一鍵更新):
    python scripts/fetch_taiex_daily.py            # 增量:只補上次之後的新交易日
    python scripts/fetch_taiex_daily.py --full     # 重抓 2020-03-02 → 今(覆蓋)

不是自動每日更新;要每日自動請另掛排程(Windows 工作排程器 / 排程 agent)。
"""
import os
import sys
import argparse
import datetime as dt
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "taiex_daily.csv"
HISTORY_START = "2020-03-02"  # Shioaji 指數歷史下限

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)   # VPS:讓 WAVE_DATA_* 等從 .env 讀到(cron 不帶環境)
except Exception:
    pass


def load_keys():
    """讀唯讀調數據金鑰(優先序):SJ_DATA_* → WAVE_DATA_*(.env、與 wave_exec 共用)→
    SHIOAJI_DATA_* → 金鑰檔『數據』段(本機 Windows 用;VPS 走 .env)。"""
    for ak, sk in (("SJ_DATA_KEY", "SJ_DATA_SEC"),
                   ("WAVE_DATA_API_KEY", "WAVE_DATA_SECRET_KEY"),
                   ("SHIOAJI_DATA_API_KEY", "SHIOAJI_DATA_SECRET_KEY")):
        k, s = (os.environ.get(ak) or "").strip(), (os.environ.get(sk) or "").strip()
        if k and s:
            return k, s
    keyfile = Path(os.environ.get("SJ_KEYFILE", r"C:\Users\xx\Desktop\vps api.txt"))
    if not keyfile.exists():
        sys.exit(f"找不到金鑰:請設 SJ_DATA_KEY/SJ_DATA_SEC 或備妥 {keyfile}")
    lines = [ln.strip() for ln in keyfile.read_text(encoding="utf-8", errors="ignore").splitlines()]
    # 從「數據」段往後找 API Key / Secret Key 的下一個非空行;找不到「數據」則用全檔最後一組
    start = next((i for i, ln in enumerate(lines) if "數據" in ln), 0)

    def after(label, frm):
        for i in range(frm, len(lines)):
            if lines[i].replace(" ", "").lower() == label.replace(" ", "").lower():
                for j in range(i + 1, len(lines)):
                    if lines[j]:
                        return lines[j]
        return None

    k = after("API Key", start)
    s = after("Secret Key", start)
    if not (k and s):
        sys.exit("金鑰檔解析失敗:請確認『數據』段有 API Key / Secret Key")
    return k, s


def fetch_daily(api, idx, start, end):
    """分年抓 1 分 K 並 resample 成日K。回 DataFrame[d,Open,High,Low,Close]。"""
    frames = []
    y0, y1 = int(start[:4]), int(end[:4])
    for y in range(y0, y1 + 1):
        s = max(start, f"{y}-01-01")
        e = min(end, f"{y}-12-31")
        kb = api.kbars(idx, start=s, end=e)
        d = pd.DataFrame({**kb})
        if len(d) == 0:
            continue
        d["ts"] = pd.to_datetime(d["ts"])
        frames.append(d)
        print(f"  {s}~{e}: {len(d)} 分K", flush=True)
    if not frames:
        return pd.DataFrame(columns=["d", "Open", "High", "Low", "Close"])
    df = pd.concat(frames, ignore_index=True).drop_duplicates("ts")
    df["d"] = df["ts"].dt.date
    daily = df.groupby("d").agg(
        Open=("Open", "first"), High=("High", "max"),
        Low=("Low", "min"), Close=("Close", "last")
    ).reset_index()
    return daily


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="重抓 2020-03-02 → 今(覆蓋)")
    args = ap.parse_args()

    today = dt.date.today().isoformat()
    existing = None
    if OUT.exists() and not args.full:
        existing = pd.read_csv(OUT, parse_dates=["d"])
        existing["d"] = existing["d"].dt.date
        last = max(existing["d"])
        start = last.isoformat()  # 重抓最後一天(補完當日)後續
        print(f"增量:已有 {len(existing)} 天,最後 {last} → 自 {start} 續抓", flush=True)
    else:
        start = HISTORY_START
        print(f"完整抓取:{start} → {today}", flush=True)

    import shioaji as sj
    k, s = load_keys()
    api = sj.Shioaji(simulation=True)
    try:
        api.login(api_key=k, secret_key=s)
        print("login OK", flush=True)
        idx = api.Contracts.Indexs.TSE["001"]
        print(f"contract: {idx.code} {idx.name}", flush=True)
        new = fetch_daily(api, idx, start, today)
    finally:
        try:
            api.logout()
        except Exception:
            pass

    if existing is not None and len(new):
        merged = pd.concat([existing, new], ignore_index=True)
        merged = merged.drop_duplicates("d", keep="last").sort_values("d").reset_index(drop=True)
    elif len(new):
        merged = new.sort_values("d").reset_index(drop=True)
    else:
        merged = existing if existing is not None else new
    OUT.parent.mkdir(parents=True, exist_ok=True)
    merged.to_csv(OUT, index=False)
    print(f"\n完成:{OUT}  共 {len(merged)} 天  {merged['d'].min()} → {merged['d'].max()}", flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
