# -*- coding: utf-8 -*-
"""釣魚防呆單重掛引擎(2026-07-23 開工;釣魚真錢門檻最後一項工程)。

職責:在深價內週選「真的掛限價買單」並跟著 F 即時重掛,防呆單(掛價高於新價平的逆選擇)。
模式:
  shadow(預設): OFFSET=400 → 掛價=價平-400,物理上不可成交 → B級驗收(真單流/零風險),
                量測重掛延遲分佈/撤改失敗率/熔斷搶跑/API節流,寫 metrics CSV。
  live:         OFFSET=90(2026-07-25 40→90,user定,同 paper 引擎口徑),需 FISHING_LIVE=YES
                雙重確認(判活尺未過前禁用)。
規格(7/23 定):
  - 1C+1P 配對網(同側永遠 1 張;反向雙成交=天然box);CAP=1/側
  - v3(2026-07-24)事件驅動 requote:on_tick 判「掛價偏離目標 ≥ REQUOTE_MIN_PTS」才標記需求
    (只 set flag、不在行情線程呼叫 API),主迴圈消化被標記的 side + 每 5s 兜底全掃(首掛/no_parity)。
    取代 v2 主迴圈每 0.2s 無條件 requote(日盤 throttle_hold 爆 12,668 次的根因)。
  - 重掛用 update_order 改價(單op,比撤+掛快一半);目標價變動 < REQUOTE_MIN_PTS 不動(防節流)
  - 節流護欄:每側每分鐘改價 ≤ MAX_UPD_PER_MIN,超過→hold+記
  - 熔斷搶跑:F 在 VEL_WIN 秒內動 > VEL_PTS → 立刻「撤單」(不只凍結)+FREEZE_SEC 後重掛
  - 成交(live才可能):即停該側報價+送MXF對沖(市價)+TG;shadow 若成交(不應發生)→全撤+終止
  - SIGTERM/停止檔(/tmp/requote_stop)→撤光所有單再退;絕不留無人看管的掛單
  - dte≤1 才張網(同 paper 引擎口徑);05:05 自動收工
啟動:CONFIRM_LIVE_ORDER=YES .venv/bin/python3 scripts/fishing_requote_engine.py [--live]
"""
import os, sys, json, time, signal, statistics, threading
import datetime as dt
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

if os.environ.get("CONFIRM_LIVE_ORDER") != "YES":
    sys.exit("需 CONFIRM_LIVE_ORDER=YES(本引擎會送真實委託,即使 shadow 模式)")
LIVE = "--live" in sys.argv
if LIVE and os.environ.get("FISHING_LIVE") != "YES":
    sys.exit("live 模式需 FISHING_LIVE=YES 雙重確認(paper 判活尺未過前禁用)")

OFFSET = 90 if LIVE else 400   # 2026-07-25 live 40→90(shadow 400 不動:物理不可成交=B級驗收用)
DEEP_PCT_LO, DEEP_PCT_HI = 0.015, 0.04     # 魚區:價內 1.5%~4%
REQUOTE_MIN_PTS = 5
MAX_UPD_PER_MIN = 30
REPICK_S = 90.0        # 2026-07-31:腿週期重挑(paper v2.1 動態魚區移植;修開機一次性=7/28漂移bug真錢版)
VEL_WIN, VEL_PTS, FREEZE_SEC = 2.0, 30.0, 10.0
# shadow 可用 FISHING_MAX_DTE 放寬(壓測重掛機制不限獵魚夜;offset400 物理不可成交=零風險);live 鎖死 1
MAX_DTE = 1 if LIVE else int(os.environ.get("FISHING_MAX_DTE", "1"))
STOP_FILE = Path("/tmp/requote_stop")
METRICS = ROOT / "data" / "opt" / f"requote_{'live' if LIVE else 'shadow'}_metrics.csv"
WEBHOOK = os.getenv("DISCORD_WEBHOOK_FISHING") or ""
END_HHMM = (5, 5)

import shioaji as sj
from shioaji import constant as sjc

F = None
fut_buf = deque()
frozen_until = 0.0
legs = {}          # side("C"/"P") -> dict(contract, trade, quoted_px, K, upd_times=deque, filled=False)
ack_evt = {}       # seqno -> (Event, [t_ack])
requote_req = {}   # side -> True(v3 事件驅動:on_tick 標記「掛價已偏離」,主迴圈消化;不在行情線程下單)
deal_evt = {}      # seqno -> (Event, [(t, price)]) — 對沖單成交回報等待(2026-07-31 對沖分支實作)
HEDGE_C = None     # MXF 近月合約(main 登入後解析)
HEDGE_FAIL = False # 對沖失敗旗標:True → 主迴圈撤光掛單停機(絕不邊裸抱邊釣)
mlock = threading.Lock()
mrows = []

def tst_now():
    """VPS 時鐘=UTC;所有時間判斷/顯示一律 TST(2026-07-24 修:05:05 收工用 UTC 永不觸發)"""
    return dt.datetime.now(dt.timezone.utc).astimezone(dt.timezone(dt.timedelta(hours=8))).replace(tzinfo=None)

def log(m):
    print(f"{tst_now():%H:%M:%S} {m}", flush=True)

def tg(msg):
    if not WEBHOOK:
        return
    try:
        import urllib.request
        data = json.dumps({"username": f"重掛{'LIVE' if LIVE else '影子'}", "content": msg},
                          ensure_ascii=False).encode("utf-8")
        urllib.request.urlopen(urllib.request.Request(
            WEBHOOK, data=data, headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}), timeout=15)
    except Exception as e:
        log(f"TG 失敗: {e}")

def metric(kind, side="", ms=None, note=""):
    with mlock:
        mrows.append(f"{tst_now():%F %T.%f},{kind},{side},{'' if ms is None else f'{ms:.0f}'},{note}")

def flush_metrics():
    with mlock:
        if not mrows:
            return
        new = not METRICS.exists()
        with open(METRICS, "a", encoding="utf-8") as f:
            if new:
                f.write("t,kind,side,ms,note\n")
            f.write("\n".join(mrows) + "\n")
        mrows.clear()

def tick_size(p):
    """TXO 真實跳動點(2026-07-24 修:昨夜9929拒單4125筆):<10→0.1 10-50→0.5 50-500→1 500-1000→5 ≥1000→10"""
    if p < 10: return 0.1
    if p < 50: return 0.5
    if p < 500: return 1.0
    if p < 1000: return 5.0
    return 10.0

def snap_px(p):
    t = tick_size(p)
    return max(t, round(round(p / t) * t, 2))

def target_px(side, K):
    if F is None:
        return None
    parity = (F - K) if side == "C" else (K - F)
    if parity <= OFFSET:
        return None
    return snap_px(parity - OFFSET)

# ── 委託操作(每個都量延遲) ──
def wait_ack(seqno, timeout=1.5):
    """等該序號的任何回報(00=成功,其他=拒單);回 (t, op_code) 或 None(逾時)。"""
    evt = threading.Event(); box = []
    ack_evt[seqno] = (evt, box)
    ok = evt.wait(timeout)
    ack_evt.pop(seqno, None)
    return box[0] if ok and box else None

LIVE_ORDER_STATES = ("PendingSubmit", "PreSubmitted", "Submitted", "PartFilled")

# ── 外部部位登記簿(2026-08-02 A案):對沖成交→登記,結算平腿器移除;
#    core 引擎(chips/maxpain)對帳前扣除登記倉,防「魅影對沖腿被判手動倉→halt」誤傷。
EXT_POS_FILE = ROOT / "data" / "external_positions.json"


def _ext_reg(entry=None, remove_opt=None):
    """讀改寫登記簿(key=fishing_requote)。entry=新增;remove_opt=按 opt_code 移除。失敗只 log 不擋交易。"""
    try:
        d = {}
        if EXT_POS_FILE.exists():
            d = json.loads(EXT_POS_FILE.read_text(encoding="utf-8"))
        lst = d.get("fishing_requote", [])
        if entry:
            lst.append(entry)
        if remove_opt:
            lst = [e for e in lst if e.get("opt_code") != remove_opt]
        d["fishing_requote"] = lst
        EXT_POS_FILE.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"外部倉登記簿更新:{'+' + entry['opt_code'] if entry else '-' + str(remove_opt)}(現 {len(lst)} 筆)")
    except Exception as e:
        log(f"⚠️ 登記簿寫入失敗:{e} — chips/maxpain 可能誤判手動倉而 halt,無資金風險但要人工補寫")


def verify_no_residual(api, trades):
    """退場後查委託「地面實況」(2026-08-01,TING 部署方貢獻)。
    不用撤單 ack 碼推論 — 夜盤 05:00 收盤 ROD 已被交易所自動失效,引擎 05:05 收工時
    cancel 會被拒,用 ack 判定會每晚誤報。查 update_status 才是實況。查不到=保守視為有殘留。"""
    if not trades:
        return True, "無掛單"
    try:
        api.update_status(api.futopt_account)
    except Exception as e:
        return False, f"update_status 失敗({str(e)[:40]}),無法確認"
    live = [f"{t.order.seqno}={str(t.status.status).split('.')[-1]}"
            for t in trades if str(t.status.status).split(".")[-1] in LIVE_ORDER_STATES]
    return (not live), ("已確認無殘留" if not live else "殘留 " + ",".join(live))


def send_hedge(api, side, opt_code, opt_px):
    """live 成交後立即市價對沖 1 口 MXF(C魚=Sell/P魚=Buy)。2026-07-31 實作(原僅規格註解)。
    成功→記 hedge_ms/slip 進 metrics。失敗(IOC 重試 1 次仍無成交回報)→ 🆘 告警 +
    HEDGE_FAIL=True(主迴圈撤光掛單停機),絕不邊裸抱邊繼續釣。"""
    global HEDGE_FAIL
    f0 = F
    action = sjc.Action.Sell if side == "C" else sjc.Action.Buy
    t0 = time.perf_counter()
    for attempt in (1, 2):
        try:
            # octype=New(2026-08-02):對沖腿=新倉,與 chips/maxpain 反向部位雙向共存(鎖倉);
            # Auto 會「先平反向」→ 把別的引擎的倉沖掉、自己變裸選擇權(user 抓到的跨引擎沖銷雷)
            order = api.Order(price=0, quantity=1, action=action,
                              price_type=sjc.FuturesPriceType.MKT, order_type=sjc.OrderType.IOC,
                              octype=sjc.FuturesOCType.New, account=api.futopt_account)
            trade = api.place_order(HEDGE_C, order)
            seq = trade.order.seqno
        except Exception as e:
            metric("hedge_err", side, None, str(e)[:40])
            log(f"[{side}] 對沖下單異常(try{attempt}): {str(e)[:60]}")
            continue
        evt, box = threading.Event(), []
        deal_evt[seq] = (evt, box)
        got = evt.wait(2.0)
        deal_evt.pop(seq, None)
        if not (got and box):
            # 防雙倍對沖(2026-07-31 覆核抓到的競態):callback 晚到/早於註冊時,盲重試=再下一單
            # → 先查委託實況,已成交就補認,不重下。
            try:
                api.update_status(api.futopt_account)
                if "Filled" in str(trade.status.status):
                    dpx = 0.0
                    try:
                        dpx = float(trade.status.deals[-1].price)
                    except Exception:
                        pass
                    box = [(time.perf_counter(), dpx)]
                    got = True
                    metric("hedge_late_deal", side, None, f"status補認 px={dpx}")
                    log(f"[{side}] 對沖回報晚到,status 補認成交 @{dpx}")
            except Exception as e:
                log(f"[{side}] 對沖狀態查詢異常:{str(e)[:40]}")
        if got and box:
            ms = (box[0][0] - t0) * 1000
            px = box[0][1]
            slip = (f0 - px) if action == sjc.Action.Sell else (px - f0)   # 不利滑價為正
            metric("hedge", side, ms, f"px={px} F0={f0:.0f} slip={slip:+.1f} try{attempt}")
            log(f"[{side}] ✅ 對沖成交 MXF {action}@{px} {ms:.0f}ms slip{slip:+.1f}點 (opt {opt_code}@{opt_px})")
            tg(f"✅ **對沖成交** MXF @{px}({ms:.0f}ms, slip {slip:+.1f}點)\n{opt_code}@{opt_px} 組合對鎖成立")
            _ext_reg(entry=dict(code=HEDGE_C.code, direction=("Sell" if side == "C" else "Buy"),
                                qty=1, opt_code=opt_code, K=legs[side]["K"], cp=side,
                                expiry=str(legs[side].get("dd", "")), hedge_px=px,
                                ts=f"{tst_now():%F %T}"))
            return True
        metric("hedge_retry" if attempt == 1 else "hedge_fail", side, None, f"try{attempt} 2s無成交回報")
        log(f"[{side}] ⚠️ 對沖第 {attempt} 次 2s 內無成交回報")
    HEDGE_FAIL = True
    tg(f"🆘 **對沖失敗=裸部位!** {opt_code}@{opt_px} 已成交但 MXF 對沖 2 次無成交回報。"
       f"引擎撤光掛單停機 — **立即人工 MXF {'Sell' if side == 'C' else 'Buy'} 1 口市價對鎖!**")
    return False


def place_leg(api, side):
    leg = legs[side]
    px = target_px(side, leg["K"])
    if px is None or leg["filled"]:
        return
    order = api.Order(price=px, quantity=1, action=sjc.Action.Buy,
                      price_type=sjc.FuturesPriceType.LMT, order_type=sjc.OrderType.ROD,
                      octype=sjc.FuturesOCType.New, account=api.futopt_account)
    t0 = time.perf_counter()
    trade = api.place_order(leg["contract"], order)
    ack = wait_ack(trade.order.seqno)
    ms = (((ack[0] if ack else None) or time.perf_counter()) - t0) * 1000
    if ack and ack[1] != "00":                    # 拒單:不算已掛 + 退避(2026-07-31:首晚壓測 12s 拒 732 次=無退避風暴)
        leg["trade"] = None; leg["quoted_px"] = None
        leg["rej_n"] = leg.get("rej_n", 0) + 1
        leg["hold_until"] = time.time() + (15 if leg["rej_n"] < 3 else 180)
        metric("place_reject", side, ms, f"px={px} code={ack[1]} rej_n={leg['rej_n']}")
        log(f"[{side}] 掛單被拒 code={ack[1]} px={px} → 退避 {15 if leg['rej_n'] < 3 else 180}s")
        if leg["rej_n"] == 3:
            tg(f"🔴 重掛引擎 [{side}] 連續拒單 code={ack[1]}(px={px}) → 該側暫停 3 分鐘輪詢。"
               f"常見因=可用保證金/權利金購買力不足,查帳戶餘裕")
        return
    leg["trade"] = trade; leg["quoted_px"] = px
    leg["rej_n"] = 0
    metric("place", side, ms, f"px={px}{'' if ack else '·ack逾時'}")
    log(f"[{side}] 掛 {leg['contract'].code} @{px} ({ms:.0f}ms{'' if ack else '·ack逾時'})")

def update_leg(api, side, px):
    leg = legs[side]
    t0 = time.perf_counter()
    try:
        api.update_order(trade=leg["trade"], price=px)
        ack = wait_ack(leg["trade"].order.seqno)
        ms = (((ack[0] if ack else None) or time.perf_counter()) - t0) * 1000
        if ack and ack[1] != "00":                # 改價被拒:狀態不明→撤+重掛
            metric("update_reject", side, ms, f"px={px} code={ack[1]}")
            cancel_leg(api, side, "update_reject")
            place_leg(api, side)
            return
        leg["quoted_px"] = px
        metric("update", side, ms, f"px={px}{'' if ack else '·ack逾時'}")
    except Exception as e:
        metric("update_fail", side, None, str(e)[:40])
        log(f"[{side}] 改價失敗({str(e)[:40]}) → 撤+重掛")
        cancel_leg(api, side)
        place_leg(api, side)

def cancel_leg(api, side, why="requote"):
    leg = legs[side]
    if not leg.get("trade"):
        return
    t0 = time.perf_counter()
    try:
        api.cancel_order(leg["trade"])
        ack = wait_ack(leg["trade"].order.seqno)
        ms = (((ack[0] if ack else None) or time.perf_counter()) - t0) * 1000
        metric("cancel", side, ms, why + (f" code={ack[1]}" if ack and ack[1] != "00" else ""))
    except Exception as e:
        metric("cancel_fail", side, None, str(e)[:40])
        log(f"[{side}] 撤單失敗: {str(e)[:40]}")
    leg["trade"] = None; leg["quoted_px"] = None

def cancel_all(api, why):
    for s in list(legs):
        cancel_leg(api, s, why)

def requote(api, side):
    leg = legs[side]
    if leg["filled"] or time.time() < frozen_until:
        return
    if time.time() < leg.get("hold_until", 0):    # 拒單退避中(2026-07-31)
        return
    px = target_px(side, leg["K"])
    if px is None:
        if leg.get("trade"):
            cancel_leg(api, side, "no_parity")
        return
    if leg.get("trade") is None:
        place_leg(api, side)
        return
    if abs(px - leg["quoted_px"]) < REQUOTE_MIN_PTS:
        return
    now = time.time()
    ut = leg["upd_times"]
    while ut and now - ut[0] > 60:
        ut.popleft()
    if len(ut) >= MAX_UPD_PER_MIN:
        metric("throttle_hold", side, None, f"quoted={leg['quoted_px']} want={px}")
        return
    ut.append(now)
    update_leg(api, side, px)

def main():
    global F, frozen_until
    api = sj.Shioaji(simulation=False)
    api.login(api_key=os.environ["SHIOAJI_API_KEY"], secret_key=os.environ["SHIOAJI_SECRET_KEY"],
              contracts_timeout=30000)
    api.activate_ca(ca_path=os.environ["SHIOAJI_CA_PATH"],
                    ca_passwd=os.environ["SHIOAJI_CA_PASSWORD"],
                    person_id=os.environ["SHIOAJI_PERSON_ID"])
    log(f"登入 OK({'LIVE' if LIVE else 'SHADOW'} offset{OFFSET})")
    global HEDGE_C
    _mxf = [c for c in api.Contracts.Futures.MXF if not str(c.code).endswith(("R1", "R2"))]
    HEDGE_C = min(_mxf, key=lambda c: str(c.delivery_month))
    log(f"對沖合約={HEDGE_C.code}({HEDGE_C.delivery_month})")

    def order_cb(stat, msg):
        try:
            s = str(stat)
            if "Order" in s:
                op = msg.get("operation", {})
                seq = msg.get("order", {}).get("seqno", "")
                if seq in ack_evt:
                    evt, box = ack_evt[seq]
                    box.append((time.perf_counter(), op.get("op_code", "")))
                    evt.set()
                if op.get("op_code") not in ("00", ""):
                    metric("reject", "", None, f"{op.get('op_code')}:{op.get('op_msg','')[:30]}")
            elif "Deal" in s or "FDeal" in s:
                dseq = msg.get("seqno", "")
                if dseq in deal_evt:               # 對沖單成交回報(2026-07-31)
                    devt, dbox = deal_evt[dseq]
                    dbox.append((time.perf_counter(), float(msg.get("price", 0) or 0)))
                    devt.set()
                code = msg.get("code", "")
                for side, leg in legs.items():
                    if leg["contract"].code == code and msg.get("action") == "Buy":
                        leg["filled"] = True
                        metric("FILL", side, None, f"{code}@{msg.get('price')}")
                        log(f"🚨 [{side}] 成交!{code} @{msg.get('price')}")
                        if LIVE:
                            tg(f"🎣 **重掛引擎成交** {code} @{msg.get('price')} → 送對沖(市價)")
                            # 2026-07-31 對沖分支實作:另線程跑(callback 不阻塞行情/回報流),
                            # place_order 本體 ~50ms,等成交回報最長 2s×2 次在線程內進行。
                            threading.Thread(target=send_hedge,
                                             args=(api, side, code, msg.get("price")),
                                             daemon=True).start()
                        else:
                            tg(f"🚨 **影子單成交(不應發生!)** {code} @{msg.get('price')} → 全撤+終止,查 OFFSET")
        except Exception as e:
            log(f"cb 異常: {e}")

    api.set_order_callback(order_cb)

    # F 串流
    fut = api.Contracts.Futures.TXF.TXFR1
    fsnap = api.snapshots([fut])[0]
    F = float(fsnap.close)
    api.quote.subscribe(fut, quote_type=sjc.QuoteType.Tick, version=sjc.QuoteVersion.v1)

    @api.on_tick_fop_v1()
    def on_tick(exchange, tick):
        global F, frozen_until
        if getattr(tick, "simtrade", 0):   # 試撮假tick不入(2026-07-31:不餵F/不觸熔斷/不標requote)
            return
        if not str(tick.code).startswith("TXF"):
            return
        try:
            px = float(tick.close)
        except Exception:
            return
        F = px
        now = time.time()
        fut_buf.append((now, px))
        while fut_buf and now - fut_buf[0][0] > VEL_WIN:
            fut_buf.popleft()
        if len(fut_buf) >= 2:
            mv = max(p for _, p in fut_buf) - min(p for _, p in fut_buf)
            if mv > VEL_PTS and now >= frozen_until:
                frozen_until = now + FREEZE_SEC
                metric("freeze", "", None, f"mv={mv:.0f}")
        # v3 事件驅動:只在「掛價已偏離目標 ≥ 門檻」時標記需求(不呼叫 API、不阻塞行情線程)。
        # 取代 v2 主迴圈每 0.2s 無條件 requote → 消掉 throttle_hold 爆量。
        if now >= frozen_until:
            for s, leg in legs.items():
                if leg["filled"] or leg.get("trade") is None or leg.get("quoted_px") is None:
                    continue
                tgt = target_px(s, leg["K"])
                if tgt is not None and abs(tgt - leg["quoted_px"]) >= REQUOTE_MIN_PTS:
                    requote_req[s] = True

    # 挑腿:最近週選,dte≤MAX_DTE 才張網;C=價內1.5-4%(K<F)、P=價內1.5-4%(K>F),取量最大檔
    def pick_legs():
        today = tst_now().date()   # 每次呼叫重算(跨夜 dte 不 stale;2026-07-31 隨週期重挑一併修)
        best = {}
        groups = {}
        for catname in ("TXO", "TX1", "TX2", "TX4", "TX5", "TXU", "TXV", "TXX", "TXY", "TXZ"):  # 2026-07-31 TXW死碼→TXZ第5週五(7/29 live實證)
            cat = getattr(api.Contracts.Options, catname, None)
            if cat is None:
                continue
            for c in cat:
                ddl = str(c.delivery_date).replace("/", "-")
                try:
                    ddate = dt.date.fromisoformat(ddl)
                except Exception:
                    continue
                dte = (ddate - today).days
                if dte < 0 or dte > MAX_DTE:
                    continue
                K = float(c.strike_price)
                right = "C" if str(c.option_right).endswith("Call") else "P"
                itm = (F - K) if right == "C" else (K - F)
                if not (F * DEEP_PCT_LO <= itm <= F * DEEP_PCT_HI):
                    continue
                groups.setdefault(right, []).append((c, K, dte))
        for right, lst in groups.items():
            snaps = api.snapshots([c for c, _, _ in lst])
            vol = {s.code: (s.total_volume or 0) for s in snaps}
            lst.sort(key=lambda x: -vol.get(x[0].code, 0))
            c, K, dte = lst[0]
            ddl = str(c.delivery_date).replace('/', '-')
            best[right] = (c, K, dte, ddl)
        return best

    picked = pick_legs()
    if not picked:
        log("dte≤1 無魚區腿(非獵魚夜)→ 收工")
        api.logout(); return
    for side, (c, K, dte, ddl) in picked.items():
        legs[side] = dict(contract=c, K=K, dd=ddl, trade=None, quoted_px=None,
                          upd_times=deque(), filled=False)
        log(f"[{side}] 腿={c.code} K={K:.0f} dte={dte}")
    tg(f"🕸️ **重掛{'LIVE' if LIVE else '影子'}引擎啟動** offset={OFFSET} | "
       + " | ".join(f"{s}:{l['contract'].code}" for s, l in legs.items())
       + f"\n熔斷={VEL_PTS:.0f}點/{VEL_WIN:.0f}s 節流={MAX_UPD_PER_MIN}/分 停止檔={STOP_FILE}")

    stop = {"flag": False}
    def on_term(sig, frm):
        stop["flag"] = True
    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGINT, on_term)

    last_flush = last_beat = last_struct = last_repick = time.time()
    try:
        while not stop["flag"]:
            time.sleep(0.05)                         # v3:主迴圈快轉,靠 tick 標記驅動、非固定改價
            now = tst_now()
            if STOP_FILE.exists():
                log("停止檔 → 收工"); break
            if HEDGE_FAIL:
                log("🆘 對沖失敗 → 撤光掛單停機(裸部位需人工處理)"); break
            hm = (now.hour, now.minute)
            if (5, 5) <= hm and hm < (8, 40):
                log("盤間空窗(05:05-08:40)→ 收工"); break
            if hm >= (13, 25) and hm < (14, 50) and any(
                    str(l.get("dd", "")) == now.date().isoformat() for l in legs.values()):
                log("結算日 13:25 → 收工(避 13:30 結算)"); break
            if time.time() < frozen_until:
                for s, leg in legs.items():          # 熔斷=搶跑撤單
                    if leg.get("trade") and not leg["filled"]:
                        cancel_leg(api, s, "freeze_pull")
            else:
                # v3 事件驅動:tick 標記的 side 立即消化;每 5s 兜底全掃(首掛/no_parity/漏標)
                struct_due = time.time() - last_struct >= 5.0
                if struct_due:
                    last_struct = time.time()
                for s in legs:
                    if requote_req.pop(s, False) or legs[s].get("trade") is None or struct_due:
                        requote(api, s)
            # 腿週期重挑(2026-07-31):F 漂出 1.5-4% 帶 → 撤舊腿換新腿(7/28 漂移 bug 真錢版根治);
            # 已成交腿不動;throttle deque 沿用(節流連續)。
            if time.time() - last_repick >= REPICK_S and time.time() >= frozen_until:
                last_repick = time.time()
                try:
                    newp = pick_legs()
                except Exception as e:
                    log(f"repick 異常:{e}"); newp = {}
                for side, (c, K, dte_, ddl) in (newp or {}).items():
                    cur = legs.get(side)
                    if cur is None:
                        legs[side] = dict(contract=c, K=K, dd=ddl, trade=None, quoted_px=None,
                                          upd_times=deque(), filled=False)
                        log(f"[{side}] 新增腿 {c.code} K={K:.0f}(dte{dte_})")
                    elif not cur["filled"] and cur["contract"].code != c.code:
                        try:
                            cancel_leg(api, side, "repick_switch")
                        except Exception as e:
                            log(f"[{side}] repick 撤舊腿異常:{e}")
                        legs[side] = dict(contract=c, K=K, dd=ddl, trade=None, quoted_px=None,
                                          upd_times=cur["upd_times"], filled=False)
                        log(f"[{side}] 魚區漂移換腿 → {c.code} K={K:.0f}(dte{dte_})")
            if all(l["filled"] for l in legs.values()):
                log("兩側皆成交(box)→ 停止報價"); break
            if time.time() - last_flush > 30:
                flush_metrics(); last_flush = time.time()
            if time.time() - last_beat > 300:
                last_beat = time.time()
                held = {s: l["quoted_px"] for s, l in legs.items()}
                log(f"[心跳] F={F:.0f} 掛價={held} 凍結={'是' if time.time()<frozen_until else '否'}")
    finally:
        # 2026-08-01 修(TING 部署方抓到):原版「收工」無條件寫入 — cancel_leg 吞例外+無條件清
        # trade=None → cancel_all 永不上拋 → 撤單失敗照樣寫「收工/掛單已全撤」= 騙過看門狗零告警。
        # 改:撤單後查地面實況,乾淨才寫「收工」;不乾淨的退場訊息刻意不含「收工」二字(看門狗判準)。
        log("退出:撤光所有掛單...")
        _trades = [l["trade"] for l in legs.values() if l.get("trade")]   # cancel 會清 None,先留參照
        try:
            cancel_all(api, "shutdown")
        except Exception as e:
            log(f"退場撤單異常: {e}")
        time.sleep(1.0)
        clean, detail = verify_no_residual(api, _trades)
        flush_metrics()
        if clean:
            log(f"logout,收工({detail})")
            tg(f"🕸️ 重掛{'LIVE' if LIVE else '影子'}引擎收工({detail};metrics={METRICS.name})")
        else:
            log(f"logout,退場但掛單未確認撤除 — 需人工處理({detail})")
            tg(f"🆘 **重掛引擎已退場但掛單未確認撤除!**\n{detail}\n立即開券商 App 查未成交委託並手動撤光")
        try:
            api.logout()
        except Exception:
            pass

if __name__ == "__main__":
    main()
