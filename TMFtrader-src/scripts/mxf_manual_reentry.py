#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""手動重進場管理器(user 2026-07-03 指示:掛 47,042 買回 2 口 MXF、成交後 -2% 停損、抱到 7/8 結算)。

背景:maxpain 首役 2 口被「領養預設停利+夜盤試撮」誤平在 47,042;user 要買回續抱。
這是策略外的一次性倉位,由本腳本全權管理(引擎經由 position_lock owner 不同而不領養):
  - seeking:掛限價 TARGET_PX 買(ROD,session 結束自然失效,下個 session 由 cron 重掛;支援部分成交)
  - holding:每 3s snapshot 盯價;px <= 成交均價×(1-STOP_PCT) → 市價全平(停損);
            2026-07-08 13:30 → 市價全平(結算);試撮時段(08:30-08:45/14:45-15:00)不評估停損
  - done:全平後釋放鎖、之後 cron 拉起直接退出
已知陷阱清單全掃:限價非市價、孤兒單清理、pidfile 單實例、simtrade 窗、TZ、TG 全事件、狀態持久化。
用完請移除 crontab 兩行(41 0 / 56 6)。
"""
import json
import os
import sys
import time as _time
from datetime import date, datetime, time as dtime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("TZ", "Asia/Taipei")
try:
    _time.tzset()
except AttributeError:
    pass

STATE = ROOT / "data" / "manual_reentry_state.json"
PIDF = Path("/tmp/mxf_manual_reentry.pid")

TARGET_PX = 47042.0
QTY = 2
STOP_PCT = 0.02
ED = date(2026, 7, 8)              # 結算週三
SETTLE_T = dtime(13, 30)
OWNER = "maxpain_manual"           # 獨立 lock owner:引擎不領養、breakout 維持互斥
POLL_S = 3.0


def log(m):
    print(f"[{datetime.now():%F %T}] {m}", flush=True)


def tg(m):
    tok = os.environ.get("TG_BOT_TOKEN", "").strip()
    cid = os.environ.get("TG_CHAT_ID", "").strip()
    if not tok or not cid:
        return
    try:
        import requests
        requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                      json={"chat_id": cid, "text": f"[手動重進場] {m}"}, timeout=15)
    except Exception as e:
        log(f"TG 失敗: {e}")


def jload():
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"phase": "seeking", "filled_qty": 0, "avg_px": 0.0}


def jsave(d):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")


def in_session(t):
    """真交易時段(可評估停損):日盤 08:45-13:45、夜盤 15:00-次日 05:00。試撮/收盤集合競價窗排除。"""
    if dtime(8, 45) <= t <= dtime(13, 40):
        return True
    if t >= dtime(15, 0) or t <= dtime(4, 55):
        return True
    return False


def session_end_reached(t):
    """本 session 收尾 → 腳本退出(下個 session cron 重拉)。"""
    return dtime(13, 46) <= t < dtime(14, 55) or dtime(5, 0) <= t < dtime(8, 40)


def single_instance():
    try:
        old = int(PIDF.read_text().strip())
        os.kill(old, 0)
        log(f"已有實例(PID {old}) → 退出")
        return False
    except (OSError, ValueError):
        pass
    try:
        PIDF.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass
    return True


def main():
    st = jload()
    if st.get("phase") == "done":
        log("已完結 → 退出(可移除 crontab 兩行)")
        return
    if not single_instance():
        return

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import shioaji as sj
    from core import position_lock

    api = sj.Shioaji()
    api.login(api_key=os.environ["SHIOAJI_API_KEY"], secret_key=os.environ["SHIOAJI_SECRET_KEY"],
              contracts_timeout=30000)
    api.activate_ca(ca_path=os.environ["SHIOAJI_CA_PATH"], ca_passwd=os.environ["SHIOAJI_CA_PASSWORD"],
                    person_id=os.environ.get("SHIOAJI_PERSON_ID", ""))
    mxf = api.Contracts.Futures["MXFR1"]
    log(f"登入 OK。phase={st['phase']} filled={st['filled_qty']}/{QTY} avg={st['avg_px']}")

    def px_now():
        try:
            return float(api.snapshots([mxf])[0].close)
        except Exception:
            return None

    def cancel_own_mxf_buys():
        """孤兒單清理:撤掉本帳號還活著的 MXF 買單(前一 session/實例殘留)。"""
        try:
            api.update_status(api.futopt_account)
            for t in api.list_trades():
                try:
                    if (str(getattr(t.contract, "code", "")).startswith("MXF")
                            and str(t.order.action) .endswith("Buy")
                            and any(k in str(getattr(t.status, "status", ""))
                                    for k in ("Submitted", "PreSubmitted", "PartFilled"))):
                        api.cancel_order(t)
                        log(f"撤殘留買單 @{t.order.price}")
                except Exception:
                    pass
            api.update_status(api.futopt_account)
        except Exception as e:
            log(f"孤兒掃描失敗: {e}")

    def market_close_all(reason):
        n = st["filled_qty"]
        if n <= 0:
            return True
        try:
            order = sj.Order(price=0, quantity=n, action=sj.constant.Action.Sell,
                             price_type=sj.constant.FuturesPriceType.MKT,
                             order_type=sj.constant.OrderType.IOC,
                             octype=sj.constant.FuturesOCType.Cover,
                             account=api.futopt_account)
            trade = api.place_order(mxf, order)
            _time.sleep(3)
            api.update_status(api.futopt_account)
            deals = getattr(trade.status, "deals", None) or []
            fp = (sum(float(d.price) * int(d.quantity) for d in deals) /
                  max(1, sum(int(d.quantity) for d in deals))) if deals else None
            pnl = ((fp - st["avg_px"]) * n * 50) if fp else None
            tg(f"⛔ {reason} → 市價平 {n} 口" + (f" @{fp:.0f}(損益 {pnl:+,.0f})" if fp else "(成交回報待查)"))
            log(f"{reason} 平倉 fp={fp}")
            return True
        except Exception as e:
            tg(f"🚨 {reason} 平倉下單失敗: {e} — 請人工!")
            log(f"平倉失敗: {e}")
            return False

    try:
        pending_trade = None
        while True:
            now = datetime.now()
            t = now.time()
            if session_end_reached(t):
                log("session 收尾 → 退出(下個 session cron 重拉)")
                break
            # ---- seeking:掛/顧限價買單 ----
            if st["phase"] == "seeking":
                if not in_session(t):
                    _time.sleep(30); continue
                if pending_trade is None:
                    cancel_own_mxf_buys()
                    need = QTY - st["filled_qty"]
                    order = sj.Order(price=TARGET_PX, quantity=need, action=sj.constant.Action.Buy,
                                     price_type=sj.constant.FuturesPriceType.LMT,
                                     order_type=sj.constant.OrderType.ROD,
                                     octype=sj.constant.FuturesOCType.New,
                                     account=api.futopt_account)
                    pending_trade = api.place_order(mxf, order)
                    log(f"掛限價買 {need} 口 @{TARGET_PX:.0f}(現價 {px_now()})")
                    tg(f"🧷 掛限價買回 {need} 口 MXF @{TARGET_PX:.0f}(現價 {px_now()});成交後停損 -2%")
                _time.sleep(5)
                try:
                    api.update_status(api.futopt_account)
                    deals = getattr(pending_trade.status, "deals", None) or []
                    fq = sum(int(d.quantity) for d in deals)
                    if fq > 0:
                        fp = sum(float(d.price) * int(d.quantity) for d in deals) / fq
                        tot = st["filled_qty"] + fq
                        st["avg_px"] = (st["avg_px"] * st["filled_qty"] + fp * fq) / tot
                        st["filled_qty"] = tot
                        # 這批 deals 已計入 → 重掛剩餘(避免重覆累計:掛單物件換新)
                        if tot < QTY:
                            try:
                                api.cancel_order(pending_trade)
                            except Exception:
                                pass
                            pending_trade = None
                        jsave(st)
                        if tot >= QTY:
                            st["phase"] = "holding"
                            st["stop_px"] = round(st["avg_px"] * (1 - STOP_PCT), 1)
                            jsave(st)
                            position_lock.acquire(owner=OWNER, side="buy", entry_price=st["avg_px"],
                                                  instrument="MXF", quantity=QTY, mode="live",
                                                  stale_hours=220.0, reason="manual reentry 47042")
                            tg(f"✅ 買回成交 {QTY} 口 均價 {st['avg_px']:.0f};停損 {st['stop_px']:.0f}(-2%)、"
                               f"抱到 {ED} 13:30 結算。引擎不接管(獨立鎖 {OWNER})")
                            log(f"holding: avg={st['avg_px']:.0f} stop={st['stop_px']:.0f}")
                except Exception as e:
                    log(f"查成交失敗: {e}")
                continue
            # ---- holding:盯停損/結算 ----
            d = now.date()
            if d > ED or (d == ED and t >= SETTLE_T):
                if market_close_all("結算日 13:30 強平"):
                    st["phase"] = "done"; jsave(st)
                    position_lock.release(OWNER, mode="live")
                break
            if in_session(t):
                px = px_now()
                if px and px <= st["stop_px"]:
                    if market_close_all(f"-2% 停損({st['stop_px']:.0f})觸發 現價{px:.0f}"):
                        st["phase"] = "done"; jsave(st)
                        position_lock.release(OWNER, mode="live")
                    break
            _time.sleep(POLL_S)
    finally:
        try:
            api.logout()
        except Exception:
            pass
    log("腳本結束")


if __name__ == "__main__":
    main()
