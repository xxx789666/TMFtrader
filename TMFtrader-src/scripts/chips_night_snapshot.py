# -*- coding: utf-8 -*-
"""chips_combo 夜盤變體 B 紙上 tape(2026-07-02 部署)。

cron 18:36 TST(10:36 UTC),排在 chips_combo_daily 18:30 之後:
  1) 結算 night_tape.csv 未平倉列 —— 用 history.csv 該 trade_date 的日盤 OHLC:
     ±2% 停損近似(夜盤時段停損未模擬)、否則收盤平倉。該日資料還沒有(FinMind 慢)→ 下次再結。
  2) 讀 next_signal.json:side=long/short 且 trade_date > 今天 → snapshot TXFR1 記夜盤進場價。
     stale 訊號(trade_date ≤ 今天)不記 —— 與 chips_exec 的 stale 告警互補。

回測依據(TXFR1 1min 2020-05~2026-05、774 筆、同 −2% 停損):
  B 全期 +14,939 vs A 現版 +13,445;但 2020-23 B 連四年較劣(2022 年 A+708 vs B−1,932)、
  2024-26 B 優(2026:B+6,765 vs A+3,127、夜盤段+121.6點/筆)→ regime 依賴。
此 tape 只為累積 forward 證據,不動凍結現版。判準:forward ~60 筆 B 仍明顯優 → 考慮升真 tick;
B 連續落後 → 關閉(2026 甜蜜期結束)。

注意:18:36 短暫 login = 主帳號第 6 條連線(常駐 5 條),歷史實測 7-8 條可跑、且僅 ~20 秒。
"""
# VPS 系統時鐘 UTC → date.today()/datetime.now() 一律 TST(2026-07-03 稽核:別依賴 crontab TZ 前綴)
import os as _os, time as _time_tz
_os.environ.setdefault('TZ', 'Asia/Taipei')
try:
    _time_tz.tzset()
except AttributeError:
    pass

import csv
import json
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DDIR = ROOT / "data" / "chips_combo"
TAPE = DDIR / "night_tape.csv"
HIST = DDIR / "history.csv"
SIGNAL = DDIR / "next_signal.json"
COLS = ["signal_eve", "trade_date", "side", "combo", "entry", "exit", "exit_reason", "pnl_pts", "pnl", "note"]
STOP = 0.02
PV = 50   # 小台 1 口,與 A tape(decisions.csv)同基準


def load_tape():
    if not TAPE.exists():
        return []
    with open(TAPE, encoding="utf-8") as f:
        # 濾歷史「重複 header 列」汙染(trade_date 欄值= 'trade_date' 的假列);save_tape 重寫即自癒
        return [r for r in csv.DictReader(f) if r.get("trade_date") != "trade_date"]


def save_tape(rows):
    with open(TAPE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in COLS})


def settle(rows):
    hist = {}
    if HIST.exists():
        with open(HIST, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                hist[r["date"]] = r
    n = 0
    for r in rows:
        if str(r.get("exit", "")).strip():
            continue
        h = hist.get(r["trade_date"])
        if not h:
            continue                       # 該日 OHLC 還沒有 → 下次再結
        entry = float(r["entry"]); dirn = 1 if r["side"] == "long" else -1
        hi, lo, cl = float(h["high"]), float(h["low"]), float(h["close"])
        stop_px = entry * (1 - STOP) if dirn > 0 else entry * (1 + STOP)
        if (dirn > 0 and lo <= stop_px) or (dirn < 0 and hi >= stop_px):
            ex, reason = stop_px, "−2%停損"
        else:
            ex, reason = cl, "收盤平倉"
        pts = round((ex - entry) * dirn, 1)
        r.update(exit=round(ex, 1), exit_reason=reason, pnl_pts=pts, pnl=round(pts * PV))
        n += 1
        print(f"結算 {r['trade_date']} {r['side']} 進{entry}→出{round(ex, 1)} {pts:+.0f}點 ({reason})")
    return n


def snapshot_txf():
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        import shioaji as sj
        api = sj.Shioaji(simulation=True)
        api.login(api_key=os.environ["SHIOAJI_API_KEY"], secret_key=os.environ["SHIOAJI_SECRET_KEY"],
                  contracts_timeout=30000)
        try:
            snap = api.snapshots([api.Contracts.Futures["TXFR1"]])[0]
            px = float(getattr(snap, "close", 0) or 0)
            return px if px > 0 else None
        finally:
            try:
                api.logout()
            except Exception:
                pass
    except Exception as e:
        print(f"snapshot error: {e}")
        return None


def record(rows):
    if not SIGNAL.exists():
        print("無 next_signal.json"); return
    sig = json.loads(SIGNAL.read_text(encoding="utf-8"))
    side, td = sig.get("side"), str(sig.get("trade_date", ""))
    today = date.today().isoformat()
    if side not in ("long", "short"):
        print(f"訊號 {td} side={side} → 不記"); return
    if td <= today:
        print(f"訊號 trade_date={td} ≤ 今天 → stale/已過 → 不記"); return
    if any(r["trade_date"] == td for r in rows):
        print(f"{td} 已記過"); return
    px = snapshot_txf()
    if px is None:
        print("snapshot 失敗,本晚不記(明晚訊號重新來過)"); return
    rows.append(dict(signal_eve=today, trade_date=td, side=side, combo=sig.get("combo", ""),
                     entry=px, exit="", exit_reason="", pnl_pts="", pnl="",
                     note="night18:36 snapshot"))
    print(f"記錄 夜盤進場 {td} {side} @ {px}")


def main():
    rows = load_tape()
    ns = settle(rows)
    record(rows)
    save_tape(rows)
    print(f"done. 結算 {ns} 筆 | tape 共 {len(rows)} 筆")


if __name__ == "__main__":
    main()
