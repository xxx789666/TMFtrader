# -*- coding: utf-8 -*-
"""對沖鏈路真實測試(2026-07-31,user 核准花真錢):
走引擎本尊 send_hedge() 代碼路徑 — C側=市價賣1口MXF、P側=市價買1口(正好互相沖平,每輪結束零部位)。
量測 hedge_ms(下單→成交回報)與 slip(成交價 vs 當下F,不利為正),寫進 metrics + 印總結。
成本≈6 筆市價單手續費+滑價(百元級)。REPS=3。夜盤執行(魚汛時段=最具代表性)。"""
import os
import sys
import time
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("CONFIRM_LIVE_ORDER", "YES")
import fishing_requote_engine as eng
import shioaji as sj
from shioaji import constant as sjc

REPS = int(os.environ.get('HEDGE_REPS', '3'))


def main():
    api = sj.Shioaji(simulation=False)
    api.login(api_key=os.environ["SHIOAJI_API_KEY"], secret_key=os.environ["SHIOAJI_SECRET_KEY"],
              contracts_timeout=30000)
    api.activate_ca(ca_path=os.environ["SHIOAJI_CA_PATH"], ca_passwd=os.environ["SHIOAJI_CA_PASSWORD"],
                    person_id=os.environ["SHIOAJI_PERSON_ID"])

    def cb(stat, msg):
        s = str(stat)
        try:
            if "Deal" in s or "FDeal" in s:
                seq = msg.get("seqno", "")
                if seq in eng.deal_evt:
                    evt, box = eng.deal_evt[seq]
                    box.append((time.perf_counter(), float(msg.get("price", 0) or 0)))
                    evt.set()
        except Exception as e:
            print("cb err:", e, flush=True)

    api.set_order_callback(cb)
    mxf = [c for c in api.Contracts.Futures.MXF if not str(c.code).endswith(("R1", "R2"))]
    eng.HEDGE_C = min(mxf, key=lambda c: str(c.delivery_month))
    fut = api.Contracts.Futures.TXF.TXFR1
    print(f"對沖合約={eng.HEDGE_C.code}", flush=True)
    order_sides = ["P", "C"] if "--reverse" in sys.argv else ["C", "P"]   # --reverse=對照組:Buy先Sell後
    print(f"順序={order_sides}", flush=True)
    results = []
    try:
        for rep in range(1, REPS + 1):
            eng.F = float(api.snapshots([fut])[0].close)
            print(f"--- rep{rep} F={eng.F:.0f}", flush=True)
            ok1 = eng.send_hedge(api, order_sides[0], f"TEST_rep{rep}", 0)
            time.sleep(1.0)
            eng.F = float(api.snapshots([fut])[0].close)
            ok2 = eng.send_hedge(api, order_sides[1], f"TEST_rep{rep}", 0)
            results.append((ok1, ok2))
            if eng.HEDGE_FAIL:
                print("HEDGE_FAIL 觸發,中止", flush=True)
                break
            time.sleep(2.0)
    finally:
        time.sleep(1.5)
        pos = api.list_positions(api.futopt_account)
        print("=== 殘餘部位(必須為空) ===", flush=True)
        for p in pos:
            print(f"  ⚠️ {p.code} {p.direction} x{p.quantity} @{p.price}", flush=True)
        if not pos:
            print("  (無,乾淨收工)", flush=True)
        eng.flush_metrics()
        print(f"metrics → {eng.METRICS}", flush=True)
        api.logout()
    print("results:", results, flush=True)


if __name__ == "__main__":
    main()
