"""MaxPainExec — maxpain_v2 訊號的「引擎真 tick paper 執行載具」(完整 2 口含加碼)。

訊號計算(TXO 各履約 OI → Max Pain、dist)留在 scripts/maxpain_daily.py(cron、HTTP 官網資料),
寫 data/maxpain_v2/next_signal.json。這支只負責「執行」:用引擎真實 tick 成交價跑完整策略——
日盤開盤窗進第1口、盤中漲 +1% 加第2口(靠引擎 check_scale/scale-in 機制)、−2% 全停(引擎硬停)、
抱到該週選結算日 13:30 強平。多日持倉跨每日 cron 重啟:狀態(ed/S1/scaled)走引擎 _strategy_state
持久化(跟 position 存、平倉自動清、重啟 _restore_strategy_state 還原)。

規則(對齊 maxpain_v2 凍結配置):
- next_signal.json state=="signal_fired"(dist>0、訊號日 t 收盤算、隔日開盤進)→ t+1 開盤窗進第1口。
- 第1口 S1,停損 S1×0.98(交給引擎 tick 級硬停;加碼後 stop 不變→兩口一起 −2% 出、第2口實虧 −3%)。
- 盤中漲到 S1×1.01 → 加第2口(check_scale)。每組最多加一次。
- 抱到結算日(ed)13:30 強平(check_exit、用 snapshot.timestamp)。
- 固定口:第1口靠 RISK_PROFILE=fixed1_paper(cap 1);加碼那口走引擎 scale-in 路徑(不過 sizer)。

⚠️ TF 須 ≥30(讓 −2% 停損 ≈3.5-5×ATR 過 risk_manager 8×ATR gate)。標的 MXF 小台(pv50)。
"""
import csv
import json
import os
import time as time_mod
from datetime import date, time
from pathlib import Path
from typing import Optional

from strategy.base import BaseStrategy, Signal, SignalDirection
from core.market_data import KBar, MarketSnapshot
from core.position import Position, Side

ROOT = Path(__file__).resolve().parent.parent
SIGNAL_FILE = ROOT / "data" / "maxpain_v2" / "next_signal.json"

# 保護性 put 模式(2026-07-02,交接規格「maxpain+put −2%」轉真錢):
#   ① 進場連鎖(fail-closed):put watcher(maxpain_put_watch.py --watch,MAXPAIN_PUT_LIVE=1)心跳
#      data/maxpain_v2/put_ready.json 新鮮(<PUT_READY_FRESH_S)才准進場——否則進了= 無硬停+無 put 裸倉。
#   ② 進場後移除引擎 −2% 硬停(put 即地板;check_exit 首 tick 清 position.stop_loss)。
#      進場 Signal 照帶 −2% 停損=讓風控 gate/口數計算不變;whatif 影子(noTP=v2口徑)照記 → 對帳不斷。
PUT_PROTECT = os.environ.get("MAXPAIN_PUT_PROTECT", "0").strip() == "1"
PUT_READY = ROOT / "data" / "maxpain_v2" / "put_ready.json"
PUT_READY_FRESH_S = float(os.environ.get("MAXPAIN_PUT_READY_FRESH_S", 180))
# 停損/put 互鎖(2026-07-03 user 定案的執行規則):
#   進場先掛 −2% 停損 → watcher 同時掛「限價 70 點(NT$3,500)」買 put(不追價)→
#   put 成交張數(覆蓋率)≥ 期貨口數才撤停損;等不到 = 停損留著(該役=v2、保費 0)。
#   加碼後口數+1 → 覆蓋率不足 → 停損自動裝回,等 put2 成交再撤。
PUT_PENDING = ROOT / "data" / "paper" / "maxpain_put" / "put_pending.json"

# what-if 影子記錄:同一筆真實進場下,平行算多個出場變體會如何(−1.25%=實際執行線、其餘為影子)。
# (label, trail, be_floor, use_stop):
#   be_floor=True → trail 出場價須 ≥ 成本(均價);低於成本不鎖虧(2026-06-11 加)。
#   use_stop=False → 連 −2% 停損都不設、純抱到結算(noSL,2026-06-12 加:6/11 該筆 −2% 停在
#   −107k、不停損抱到 6/12 反彈變賺;全史掃 130 筆無停損 +1.085M/PF1.84 vs 凍結 +574k/1.35,
#   且最大單筆反而較小 —— gap 穿停損時停損版照樣吃滿、回頭單卻被砍。in-sample,先影子驗)。
WF_VARIANTS = [("noTP", 0.0, False, True), ("trail1.0", 0.010, False, True),
               ("trail1.25", 0.0125, False, True), ("trail1.5", 0.015, False, True),
               ("trail1.25be", 0.0125, True, True), ("noSL", 0.0, False, False)]
WF_PENDING = ROOT / "data" / "maxpain_v2" / "whatif_pending.json"   # 進行中持倉的影子狀態(跨重啟還原)
WF_TAPE = ROOT / "data" / "maxpain_v2" / "whatif.csv"              # 已結束持倉的逐筆變體結果


class MaxPainExecStrategy(BaseStrategy):
    def __init__(self, stop_pct: float = 0.02, scale_pct: float = 0.01, point_value: float = 50.0,
                 session_start: tuple = (8, 45), entry_window_end: tuple = (9, 30),
                 settle_close: tuple = (13, 30), trail_pct: float = 0.0, arm_pct: float = 0.01):
        self.stop_pct = stop_pct
        self.scale_pct = scale_pct
        self.point_value = point_value
        # 追蹤止盈(2026-06-09 forward 驗證版):獲利曾漲過 +arm_pct 後,從持有期最高點回落
        # trail_pct → 鎖利出場。trail_pct=0 即停用(回到凍結「無止盈、抱到結算」)。
        self.trail_pct = trail_pct
        self.arm_pct = arm_pct
        # 多日持倉:豁免引擎每日盤末強平(否則 MXF 用 default 盤別、每天 13:40 被平、無法抱到週選結算)。
        # 出場由 check_exit(結算日 13:30)+ −2% tick 硬停管理。
        self.holds_overnight = True
        self.session_start = time(*session_start)
        self.entry_window_end = time(*entry_window_end)
        self.settle_close = time(*settle_close)
        self.reset()

    @property
    def name(self) -> str:
        return "MaxPainExec"

    def _read_signal(self) -> Optional[dict]:
        try:
            return json.loads(SIGNAL_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _read_signal_cached(self) -> Optional[dict]:
        """2s TTL 快取(tick 級進場每 tick 都會查;訊號檔前一晚 18:40 cron 寫好、盤中不變)。"""
        import time as _t
        now = _t.monotonic()
        if now - getattr(self, "_sig_cache_at", 0.0) > 2.0:
            self._sig_cache = self._read_signal()
            self._sig_cache_at = now
        return self._sig_cache

    def _entry_decision(self, today, bt, price, dt) -> Optional[Signal]:
        """進場判斷(on_kbar 與 check_entry_tick 共用;_acted_signal 防重複進場)。"""
        if bt < self.session_start or bt >= self.entry_window_end:
            return None
        sig = self._read_signal_cached()
        if not sig or sig.get("state") != "signal_fired" or sig.get("side") != "long":
            return None
        signal_t = sig.get("signal_t")
        ed = sig.get("ed")
        if not signal_t or not ed:
            return None
        # 進場日 = 訊號日的次一交易日;今天須晚於訊號日、且在合理範圍(≤4 天、避免漏掉後追高)
        try:
            d_sig = date.fromisoformat(signal_t)
        except ValueError:
            return None
        if today <= d_sig or (today - d_sig).days > 4:
            return None
        if self._acted_signal == signal_t:            # 同一訊號已進過 → 不重進
            return None

        if PUT_PROTECT and not self._put_ready():     # put 腿沒就緒 → 不進場(訊號不消耗、就緒後窗內可進)
            if time_mod.monotonic() - getattr(self, "_put_warn_at", 0.0) > 30:
                self._put_warn_at = time_mod.monotonic()
                print(f"[maxpain_exec] PUT_PROTECT: put watcher 心跳不新鮮 → 暫不進場(fail-closed)", flush=True)
            return None

        self._acted_signal = signal_t
        self._mp_ed = ed                              # ISO 字串(供引擎 _strategy_state 持久化)
        self._mp_s1 = price
        self._mp_scaled = False
        self._mp_hi = price                           # 追蹤止盈:持有期最高點(從進場價起算)
        self._mp_armed = False
        self._wf_start(signal_t, ed, price, dt)
        return Signal(direction=SignalDirection.BUY, strength=0.7,
                      stop_loss=round(price * (1 - self.stop_pct), 1), take_profit=0.0,
                      reason=f"maxpain long dist{sig.get('dist')}", source=self.name)

    def check_entry_tick(self, snapshot: MarketSnapshot) -> Optional[Signal]:
        """tick 級進場(引擎無倉時每 tick 呼叫):08:45 開盤第一筆 tick 就能進,
        不必等首根 30m K 收盤(09:00)→ 更貼研究口徑(t+1 開盤價)。與 on_kbar 共用
        _entry_decision/_acted_signal,tick 先進了 on_kbar 就不會重進。"""
        ts = snapshot.timestamp
        px = snapshot.price
        if ts is None or px <= 0:
            return None
        return self._entry_decision(ts.date(), ts.time(), px, ts)

    def on_kbar(self, kbar: KBar, snapshot: MarketSnapshot, **kw) -> Optional[Signal]:
        self._bar_time = kbar.datetime
        today = kbar.datetime.date()
        if today != self._cur_day:
            self._cur_day = today
        # K 棒收盤路徑(備援;正常 tick 路徑已在 08:45 先進)
        return self._entry_decision(today, kbar.datetime.time(), snapshot.price, kbar.datetime)

    def check_scale(self, position: Position, snapshot: MarketSnapshot) -> Optional[Signal]:
        """持倉中:漲到 S1×(1+scale_pct) 且尚未加碼 → 回傳加第2口的同向 Signal(引擎 _execute_scale 執行)。"""
        if self._mp_scaled or position.side != Side.LONG:
            return None
        s1 = self._mp_s1
        if s1 <= 0:
            return None
        if snapshot.price >= s1 * (1 + self.scale_pct):
            self._mp_scaled = True
            s = Signal(direction=SignalDirection.BUY, strength=0.7,
                       stop_loss=position.stop_loss, take_profit=0.0,
                       reason=f"maxpain +{self.scale_pct*100:.0f}% 加碼", source=self.name)
            s.close_quantity = 1                      # 借此欄帶「加 1 口」
            return s
        return None

    def _put_ready(self) -> bool:
        """put watcher 心跳檢查(epoch ts,跨 process;watcher 每輪 poll 刷新)。"""
        try:
            rd = json.loads(PUT_READY.read_text(encoding="utf-8"))
            return (rd.get("date") == date.today().isoformat()
                    and (time_mod.time() - float(rd.get("ts", 0))) <= PUT_READY_FRESH_S)
        except (OSError, ValueError, TypeError):
            return False

    def _put_coverage(self) -> int:
        """已成交 put 張數(0/1/2)。pend 須屬本役(ed 相符,防舊 pend 殘留誤撤停損);5s 快取。"""
        nowm = time_mod.monotonic()
        if nowm - getattr(self, "_pc_at", 0.0) > 5.0:
            self._pc_at = nowm
            n = 0
            try:
                d = json.loads(PUT_PENDING.read_text(encoding="utf-8"))
                if d and d.get("ed") == self._mp_ed:
                    if str(d.get("put_fill", "")).strip() not in ("", "None"):
                        n += 1
                    if d.get("added") and str(d.get("put2_fill", "")).strip() not in ("", "None"):
                        n += 1
            except (OSError, ValueError):
                pass
            self._pc_cache = n
        return getattr(self, "_pc_cache", 0)

    def check_exit(self, position: Position, snapshot: MarketSnapshot) -> Optional[Signal]:
        """−2% 由引擎硬停(stop_loss=S1×0.98);這裡管 追蹤止盈 + 結算日強平。
        ed/最高點/武裝 走 _strategy_state 跨重啟還原。
        PUT_PROTECT 模式(user 2026-07-03 規則,每 tick 雙向):
          put 成交張數 ≥ 期貨口數 → 撤停損(put 即地板);不足 → 停損裝回 S1×0.98(等 put 或整役 v2)。"""
        if PUT_PROTECT and self._mp_s1 > 0:
            lots = int(getattr(position, "quantity", 1) or 1)
            if self._put_coverage() >= lots:
                if getattr(position, "stop_loss", 0):
                    position.stop_loss = 0.0
                    print(f"[maxpain_exec] PUT_PROTECT: put 覆蓋 {lots}/{lots} → 撤停損(put 即地板)", flush=True)
            else:
                if getattr(position, "stop_loss", 0) <= 0:
                    position.stop_loss = round(self._mp_s1 * (1 - self.stop_pct), 1)
                    print(f"[maxpain_exec] PUT_PROTECT: put 覆蓋不足({self._put_coverage()}/{lots}) → "
                          f"停損裝回 @{position.stop_loss:.0f}", flush=True)
        ts = snapshot.timestamp
        if ts is None or not self._mp_ed:
            return None
        px = snapshot.price
        # 追蹤止盈(每 tick):更新最高點 → 漲過 +arm% 武裝 → 武裝後從高點回落 trail% 鎖利出場。
        # 實際執行線 = trail_pct(預設 0.0125=V3 −1.25%)。影子各變體另由引擎每-tick wf_record_tick 推進。
        if self.trail_pct > 0 and self._mp_s1 > 0 and px > 0:
            if px > self._mp_hi:
                self._mp_hi = px
            if not self._mp_armed and px >= self._mp_s1 * (1 + self.arm_pct):
                self._mp_armed = True
            if self._mp_armed and px <= self._mp_hi * (1 - self.trail_pct):
                return Signal(direction=SignalDirection.CLOSE, strength=1.0,
                              stop_loss=px, take_profit=px,
                              reason=f"maxpain 追蹤止盈 -{self.trail_pct*100:.2f}%"
                                     f"(高{self._mp_hi:.0f}→{px:.0f})", source=self.name)
        try:
            ed = date.fromisoformat(self._mp_ed)
        except (ValueError, TypeError):
            return None
        d = ts.date()
        hit = (d > ed) or (d == ed and ts.time() >= self.settle_close)
        if hit:
            return Signal(direction=SignalDirection.CLOSE, strength=1.0,
                          stop_loss=snapshot.price, take_profit=snapshot.price,
                          reason="maxpain 週選結算強平", source=self.name)
        return None

    # ---- what-if 影子記錄器(每 tick 由引擎 wf_record_tick 驅動;與實際執行線獨立)----
    # 實際執行 trail_pct(−1.25%)那條會較早平真倉,但影子的無止盈/−1.5% 出場更晚 → 靠引擎
    # 每-tick(不管有無倉)呼叫 wf_record_tick,在真倉平掉後續追每個變體到各自出場,全到齊才寫一列。
    def _wf_start(self, signal_t: str, ed: str, price: float, dt) -> None:
        if getattr(self, "_wf", None):                 # 前一筆影子未跑完(極端重疊)→ 現價強制收尾
            for lab, *_ in WF_VARIANTS:
                v = self._wf["v"].get(lab)
                if v is not None and not v["done"]:
                    v.update(done=True, exit_px=round(price, 1), reason="flush_new_entry",
                             exit_t=dt.isoformat(), scaled=self._wf["scaled"])
            self._wf_finalize()
        self._wf = {
            "signal_t": signal_t, "ed": ed, "s1": price, "scaled": False,
            "entry_t": dt.isoformat(),
            "v": {lab: {"hi": price, "armed": False, "done": False,
                        "exit_px": None, "reason": None, "exit_t": None, "scaled": False}
                  for lab, *_ in WF_VARIANTS},
        }
        self._wf_save()

    def wf_record_tick(self, px: float, ts) -> None:
        """引擎每 tick 呼叫(有無倉皆呼叫)。推進所有變體;全部出場後寫一列到 whatif.csv。"""
        wf = getattr(self, "_wf", None)
        if not wf or px <= 0 or ts is None:
            return
        s1 = wf["s1"]
        if s1 <= 0:
            return
        if not wf["scaled"] and px >= s1 * (1 + self.scale_pct):   # 鏡像 +1% 加碼(影子用)
            wf["scaled"] = True
        stp = s1 * (1 - self.stop_pct)
        settle_hit = False                                         # 結算日 13:30 收盤平(未出場變體共用)
        try:
            ed = date.fromisoformat(wf["ed"]); d = ts.date()
            settle_hit = (d > ed) or (d == ed and ts.time() >= self.settle_close)
        except (ValueError, TypeError, KeyError):
            pass
        changed = False
        for lab, trail, be, use_stop in WF_VARIANTS:
            v = wf["v"].get(lab)
            if v is None or v["done"]:           # 舊 pending(部署前開的)沒有新變體 → 跳過
                continue
            if px > v["hi"]:
                v["hi"] = px; changed = True
            if not v["armed"] and px >= s1 * (1 + self.arm_pct):
                v["armed"] = True; changed = True
            if use_stop and px <= stp:                             # −2% 停損(noSL 變體關閉)
                v.update(done=True, exit_px=round(stp, 1), reason="stop",
                         exit_t=ts.isoformat(), scaled=wf["scaled"]); changed = True
                continue
            if trail > 0 and v["armed"] and px <= v["hi"] * (1 - trail):   # 追蹤止盈鎖利
                # be_floor 變體:出場價須 ≥ 成本(均價;加碼後 = S1×(1+scale/2))。
                # 低於成本不鎖虧 → 不出,交給 −2% 停損/結算。價回到成本上方且仍破 trail 線才出。
                cost = s1 * (1 + self.scale_pct / 2) if wf["scaled"] else s1
                if be and px < cost:
                    pass
                else:
                    v.update(done=True, exit_px=round(px, 1), reason="trail",
                             exit_t=ts.isoformat(), scaled=wf["scaled"]); changed = True
                    continue
            if settle_hit:                                         # 抱到結算日收盤平
                v.update(done=True, exit_px=round(px, 1), reason="settle",
                         exit_t=ts.isoformat(), scaled=wf["scaled"]); changed = True
        if all((wf["v"].get(lab) or {"done": True})["done"] for lab, *_ in WF_VARIANTS):
            self._wf_finalize()
        elif changed:
            self._wf_save()

    def _wf_finalize(self) -> None:
        """所有變體出場後,計各自損益(含 +1% 加碼第2口)寫一列 whatif.csv,清 pending。"""
        wf = getattr(self, "_wf", None)
        if not wf:
            return
        s1 = wf["s1"]; pv = self.point_value; s2 = s1 * (1 + self.scale_pct)
        row = {"signal_t": wf["signal_t"], "entry_t": wf["entry_t"], "ed": wf["ed"], "s1": round(s1, 1)}
        for lab, *_ in WF_VARIANTS:
            v = wf["v"].get(lab)
            if v is None:                          # 舊 pending 無此變體 → 欄位留空(維持固定 schema)
                row[f"{lab}_px"] = ""; row[f"{lab}_reason"] = ""
                row[f"{lab}_lots"] = ""; row[f"{lab}_pnl"] = ""
                continue
            ex = v["exit_px"] if v["exit_px"] is not None else s1
            pnl = ((ex - s1) + ((ex - s2) if v["scaled"] else 0.0)) * pv
            row[f"{lab}_px"] = ex
            row[f"{lab}_reason"] = v["reason"]
            row[f"{lab}_lots"] = 2 if v["scaled"] else 1
            row[f"{lab}_pnl"] = round(pnl, 0)
        self._wf_append(row)
        self._wf = None
        try:
            WF_PENDING.unlink()
        except OSError:
            pass

    def _wf_save(self) -> None:
        if getattr(self, "_wf", None) is None:
            return
        try:
            WF_PENDING.parent.mkdir(parents=True, exist_ok=True)
            WF_PENDING.write_text(json.dumps(self._wf, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def _wf_load(self) -> None:
        try:
            self._wf = json.loads(WF_PENDING.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._wf = None

    def _wf_append(self, row: dict) -> None:
        try:
            WF_TAPE.parent.mkdir(parents=True, exist_ok=True)
            new = not WF_TAPE.exists()
            with open(WF_TAPE, "a", newline="", encoding="utf-8-sig") as f:
                w = csv.DictWriter(f, fieldnames=list(row.keys()))
                if new:
                    w.writeheader()
                w.writerow(row)
        except OSError:
            pass

    def get_parameters(self) -> dict:
        return {"stop_pct": self.stop_pct, "scale_pct": self.scale_pct,
                "trail_pct": self.trail_pct, "arm_pct": self.arm_pct,
                "settle_close": str(self.settle_close)}

    def reset(self):
        self._cur_day = None
        self._bar_time = None
        self._acted_signal = None
        self._sig_cache = None
        self._sig_cache_at = 0.0
        # 多日持倉狀態(paper:引擎 _restore_strategy_state 重啟+持倉時覆寫還原)
        self._mp_ed = None
        self._mp_s1 = 0.0
        self._mp_scaled = False
        self._mp_hi = 0.0
        self._mp_armed = False
        self._wf_load()                # 重啟還原進行中的影子狀態(無則 None)
        # ⚠️ LIVE 模式無 position 持久化(engine _state_dir 僅 paper)→ 每日 cron 重啟後 _mp_* 全丟:
        # 結算強平(需 _mp_ed)/put 覆蓋率(需 _mp_s1)/同訊號防重進(_acted_signal)全失效(2026-07-03 發現)。
        # 用 whatif_pending 嫁接還原(進場當下建立、活到結算、含 signal_t/ed/s1/scaled;
        # paper 模式稍後 _restore_strategy_state 會以正式持久化值覆寫,同值無害)。
        wf = getattr(self, "_wf", None)
        if wf and wf.get("ed") and float(wf.get("s1") or 0) > 0:
            self._mp_ed = wf["ed"]
            self._mp_s1 = float(wf["s1"])
            self._mp_scaled = bool(wf.get("scaled"))
            self._mp_hi = max(self._mp_s1, float(self._mp_hi or 0))
            self._acted_signal = wf.get("signal_t")     # 同訊號不重進(停損後重啟殘窗防呆)
            print(f"[maxpain_exec] 重啟還原(wf 嫁接): ed={self._mp_ed} S1={self._mp_s1:.0f} "
                  f"scaled={self._mp_scaled} signal={self._acted_signal}", flush=True)
