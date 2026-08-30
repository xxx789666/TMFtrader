# -*- coding: utf-8 -*-
"""券商成交紀錄取證(交接單 2026-08-30 甲-2)— 完全唯讀。

- list_profit_loss(07-01~08-30,按平倉日記帳)+ 逐筆 detail + positions + settlements。
- 原始回應先落盤 data/broker_audit_2026_08_30/raw_*.json,不後製。
- 錨點:2026-07-03 平倉的 MXF(Git f53cf1e 記 2 口 @47,042)。錨點撈不到=查詢方法有問題,
  不得拿本輸出下任何「某日沒成交」的結論。
- 本腳本不下單、不改任何帳務;只新增 data/broker_audit_2026_08_30/ 目錄。
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

import shioaji as sj

OUT = ROOT / "data" / "broker_audit_2026_08_30"
OUT.mkdir(parents=True, exist_ok=True)
BEGIN, END = "2026-07-01", "2026-08-30"


def to_plain(obj):
    """shioaji 物件 → 可序列化(盡量保留原始欄位)。"""
    for m in ("dict", "to_dict"):
        f = getattr(obj, m, None)
        if callable(f):
            try:
                return f()
            except Exception:
                pass
    if isinstance(obj, (list, tuple)):
        return [to_plain(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return repr(obj)


def dump(name, data):
    p = OUT / name
    with open(p, "w", encoding="utf-8") as f:
        json.dump(to_plain(data), f, ensure_ascii=False, indent=1, default=repr)
    print(f"[落盤] {p} ({p.stat().st_size} bytes)")


def main():
    api = sj.Shioaji(simulation=False)
    api.login(api_key=os.environ["SHIOAJI_API_KEY"],
              secret_key=os.environ["SHIOAJI_SECRET_KEY"],
              receive_window=300000, fetch_contract=True)
    time.sleep(2)
    try:
        api.activate_ca(ca_path=os.environ.get("SHIOAJI_CA_PATH", ""),
                        ca_passwd=os.environ["SHIOAJI_CA_PASSWORD"],
                        person_id=os.environ["SHIOAJI_PERSON_ID"])
    except Exception as e:
        print(f"[warn] activate_ca: {e}")
    acct = api.futopt_account
    print(f"signed={getattr(acct, 'signed', '?')} account={getattr(acct, 'account_id', '?')}")

    # 1) 已實現損益(核心;按平倉日)
    try:
        pnl = api.list_profit_loss(acct, begin_date=BEGIN, end_date=END)
    except Exception as e:
        print(f"🔴 list_profit_loss 失敗:{e}")
        pnl = None
    dump("raw_profit_loss.json", pnl if pnl is not None else {"error": "list_profit_loss failed"})

    # 2) 逐筆 detail(進場日在這裡)
    details = []
    if pnl:
        for it in pnl:
            did = getattr(it, "id", None)
            if did is None:
                details.append({"item": to_plain(it), "error": "no id attr"})
                continue
            try:
                d = api.list_profit_loss_detail(acct, did)
                details.append({"id": did, "detail": to_plain(d)})
            except Exception as e:
                details.append({"id": did, "error": str(e)})
            time.sleep(0.3)
    dump("raw_profit_loss_detail.json", details)

    # 3) 現有部位 / 4) 交割 / 5) summary(輔助)
    for name, fn in (("raw_positions.json", lambda: api.list_positions(acct)),
                     ("raw_settlements.json", lambda: api.settlements(acct)),
                     ("raw_profit_loss_summary.json",
                      lambda: api.list_profit_loss_summary(acct, begin_date=BEGIN, end_date=END))):
        try:
            dump(name, fn())
        except Exception as e:
            dump(name, {"error": str(e)})

    # 摘要(純轉述 raw,不判讀)
    print("\n=== 已實現損益逐筆(平倉日口徑)===")
    if pnl:
        anchor = 0
        for it in pnl:
            d = to_plain(it)
            date = d.get("date", "?") if isinstance(d, dict) else "?"
            code = d.get("code", "?") if isinstance(d, dict) else "?"
            print(f"  {date} {code} qty={d.get('quantity','?')} price={d.get('price','?')} "
                  f"pnl={d.get('pnl','?')}" if isinstance(d, dict) else f"  {d}")
            if isinstance(d, dict) and str(date).replace("/", "-").startswith("2026-07-03") \
                    and str(code).startswith("MXF"):
                anchor += 1
        print(f"\n[錨點檢查] 2026-07-03 平倉之 MXF 筆數 = {anchor}"
              f"{'(過:查詢方法可信)' if anchor > 0 else '(🔴 未過:方法不可信,禁止據此下任何結論)'}")
    else:
        print("  (無資料或查詢失敗 — 見 raw 檔)")

    api.logout()
    print("Done(唯讀,零寫入帳務)")


if __name__ == "__main__":
    main()
