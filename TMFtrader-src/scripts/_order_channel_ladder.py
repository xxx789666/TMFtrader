# -*- coding: utf-8 -*-
"""委託通道冷卻曲線實驗(2026-07-31,零成交風險):
深價外 TXO 月選 put 掛限價 0.1(ROD)→ 量 place ack 延遲 → 立即撤單,
按閒置階梯 [1,3,5,7,10,15,20,30]s × 2 輪。
Phase B:閒置 7s/10s 但倒數 1s 時先打一發 snapshots(行情通道)→ 驗「行情暖不到委託通道」假說。
成本:0(0.1 掛深價外幾乎不可能成交;萬一成交=NT$5)。"""
import os
import sys
import time
import threading

os.environ.setdefault("CONFIRM_LIVE_ORDER", "YES")
import shioaji as sj
from shioaji import constant as sjc

IDLES = [1, 3, 5, 7, 10, 15, 20, 30]
acks = {}   # seqno -> (Event, [t_ack])


def main():
    api = sj.Shioaji(simulation=False)
    api.login(api_key=os.environ["SHIOAJI_API_KEY"], secret_key=os.environ["SHIOAJI_SECRET_KEY"],
              contracts_timeout=30000)
    api.activate_ca(ca_path=os.environ["SHIOAJI_CA_PATH"], ca_passwd=os.environ["SHIOAJI_CA_PASSWORD"],
                    person_id=os.environ["SHIOAJI_PERSON_ID"])

    def cb(stat, msg):
        try:
            if "Order" in str(stat):
                seq = msg.get("order", {}).get("seqno", "")
                if seq in acks:
                    evt, box = acks[seq]
                    box.append(time.perf_counter())
                    evt.set()
        except Exception:
            pass

    api.set_order_callback(cb)
    # 深價外月選 put:202608 鏈最低履約價(最遠 OTM,夜盤可交易)
    txo = [c for c in api.Contracts.Options.TXO
           if getattr(c, "delivery_month", "") == "202608"
           and str(c.option_right).endswith("Put")]
    deep = min(txo, key=lambda c: c.strike_price)
    fut = api.Contracts.Futures.TXF.TXFR1
    print(f"標的={deep.code} K={deep.strike_price}", flush=True)

    def timed_place():
        order = api.Order(price=0.1, quantity=1, action=sjc.Action.Buy,
                          price_type=sjc.FuturesPriceType.LMT, order_type=sjc.OrderType.ROD,
                          octype=sjc.FuturesOCType.New, account=api.futopt_account)
        t0 = time.perf_counter()
        tr = api.place_order(deep, order)
        seq = tr.order.seqno
        evt, box = threading.Event(), []
        acks[seq] = (evt, box)
        got = evt.wait(5.0)
        acks.pop(seq, None)
        ms = (box[0] - t0) * 1000 if got and box else -1
        time.sleep(0.3)
        try:
            api.cancel_order(tr)
        except Exception as e:
            print(f"  cancel err: {e}", flush=True)
        time.sleep(0.3)
        return ms

    print("=== Phase A:純閒置階梯(距上次委託操作 N 秒) ===", flush=True)
    for rnd in (1, 2):
        for idle in IDLES:
            time.sleep(idle)
            ms = timed_place()
            print(f"A r{rnd} idle={idle:>2d}s place_ack={ms:.0f}ms", flush=True)
    print("=== Phase B:閒置 7/10s,但倒數 1s 先打行情 snapshot ===", flush=True)
    for rnd in (1, 2):
        for idle in (7, 10):
            time.sleep(idle - 1)
            api.snapshots([fut])          # 行情通道呼叫
            time.sleep(1)
            ms = timed_place()
            print(f"B r{rnd} idle={idle:>2d}s(+snap@-1s) place_ack={ms:.0f}ms", flush=True)
    pos = api.list_positions(api.futopt_account)
    print("殘餘部位:", [(p.code, p.quantity) for p in pos] or "無", flush=True)
    api.logout()


if __name__ == "__main__":
    main()
