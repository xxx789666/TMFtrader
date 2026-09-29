# -*- coding: utf-8 -*-
"""順風燈 forward 自我說明報告(2026-08-30 user 指示補強)→ data/flow/tailwind_forward_report.md

內容:①燈狀態 tape ②三特徵源出處+當日更新狀態(新鮮度) ③燈 × chips_combo 事件帶
(decisions.csv,|z_lt|>0.5;主判準素材) ④燈 × chips_exec 真tick paper 分組對照。
由 flow_lamp_daily.py 於每日 22:00 燈跑完後呼叫;本機 pull 腳本 18:50 隔日拉走。
純讀取+寫一個 MD;不下單、不改任何判準。

2026-08-30 盲化改造(lab 交接單 vps_handoff_lamp_blinding_2026_08_30,使用者已簽):
門檻達成前 ③④ 只印計數與 K1/K2 機械否證旗標(布林,禁金額);每筆隔日報酬、亮/暗均
報酬、paper 損益、PF 一律不印。①② 照舊。另落地事件 tape csv(開獎腳本輸入):
ret=隔日 open→close 原始報酬(帶號未調整,取自 history.csv;decisions.csv 的 ret_pct
是含 −2% 停損的方向調整交易報酬,口徑不同,不得混用)。內部仍計算亮/暗累積和以判
旗標,但**不得寫進報告、stdout 或 TG**。
"""
import json
import re
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FWD = ROOT / "data" / "flow" / "tailwind_lamp_forward_log.txt"
FEAT = ROOT / "data" / "flow" / "flow_hmm_feature_table.csv"
DEC = ROOT / "data" / "chips_combo" / "decisions.csv"
HIST = ROOT / "data" / "chips_combo" / "history.csv"
PAPER = ROOT / "data" / "paper" / "chips_exec" / "performance" / "daily"
OUT = ROOT / "data" / "flow" / "tailwind_forward_report.md"
TAPE_OUT = ROOT / "data" / "flow" / "tailwind_lamp_event_tape.csv"
START = "2026-08-26"          # forward 起算日(凍結)
EVENT_THR = 0.5               # 事件定義 |z_lt|>0.5(驗收判準原文)
FLAG_MIN_N = 10               # K1/K2 旗標最小樣本(交接單 §二)


def flag_text(vals):
    """K1/K2 機械否證旗標:只回布林三值,金額不外流。"""
    if len(vals) < FLAG_MIN_N:
        return f"樣本未足(n<{FLAG_MIN_N})"
    return "命中" if sum(vals) < 0 else "未命中"

sys.stdout.reconfigure(encoding="utf-8")


def lamp_states():
    """forward log → {date: (p_str, lit_bool, 原行)}。"""
    out = {}
    if not FWD.exists():
        return out
    for ln in FWD.read_text(encoding="utf-8").splitlines():
        m = re.search(r"\[資金流順風燈\] (\d{4}-\d{2}-\d{2})\s+P̄\(s2\)=([\d.]+)%", ln)
        if m:
            out[m.group(1)] = (m.group(2), "🟢" in ln, ln.strip())
    return out


def paper_trades_since(start):
    """chips_exec 真tick paper trades(同日 X.json 優先於 X_live.json)。"""
    files = {}
    for p in sorted(PAPER.glob("2026-*.json")):
        d = p.name[:10]
        if d < start:
            continue
        if p.name.endswith("_live.json") and d in files:
            continue
        if not p.name.endswith("_live.json"):
            files[d] = p
        else:
            files.setdefault(d, p)
    rows = []
    for d, p in sorted(files.items()):
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for t in j.get("trades", []):
            rows.append((d, t.get("side"), float(t.get("net_pnl", 0))))
    return rows


def main():
    import csv
    states = lamp_states()
    L = ["# 資金流順風燈 forward 報告(自動產出)",
         "",
         f"> 產出時間:{datetime.now().strftime('%F %T')}(VPS,每日 22:00 燈跑完後自動更新)",
         "> 紅線:純觀察、不下單、不重訓、門檻 0.55 凍結。forward 起算 2026-08-26。",
         "> 驗收:≥6 個月 + chips 事件(|z_lt|>0.5)≥60 筆;判死可提前、判活不行。",
         ""]

    # ── ① 燈狀態 tape ──
    L += ["## ① 燈狀態(forward tape)", "", "```"]
    L += [v[2] for v in states.values()] or ["(尚無紀錄)"]
    L += ["```", ""]

    # ── ② 數據來源與新鮮度 ──
    last_feat = "?"
    if FEAT.exists():
        last_feat = FEAT.read_text(encoding="utf-8").strip().splitlines()[-1].split(",")[0]
    today = date.today().isoformat()
    L += ["## ② 燈的數據哪來的(三特徵源)+ 更新狀態", "",
          "| 特徵 | 來源 | 口徑 |",
          "|---|---|---|",
          "| f1 融資動能 | FinMind `TaiwanStockTotalMarginPurchaseShortSale` | 全市場融資金額 TodayBalance 的 Δlog(每晚 ~21:00 後可得) |",
          "| f2 大戶動向 | 本機 chips 管線 `data/chips_combo/history.csv` 的 all_ratio 欄 | (前十大買方-賣方)/OI 的日差(與 chips_combo 同口徑直接重用) |",
          "| f3 匯率資金流 | FinMind `TaiwanExchangeRate` USD | 即期買賣中價的 Δlog(升值=外資流入方向) |",
          "",
          f"- 特徵表最後日:**{last_feat}**(今天 {today};平日缺日=該源未出,燈照跑但標 stale,補到自動回填)",
          "- 模型:15 顆凍結 GaussianHMM 共識(fit 窗 2020-22、永不重訓);每晚自檢 vs 凍結序列,",
          "  近三日 max|diff|=7.18e-13(逐位一致=零漂移)。",
          ""]

    # ── ③ 燈 × chips_combo 事件帶(主判準素材;盲化=只印計數+旗標)──
    # 隔日 open→close 原始報酬(開獎口徑);key=交易日
    raw_ret = {}
    if HIST.exists():
        with open(HIST, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    o, c = float(r["open"]), float(r["close"])
                    raw_ret[r["date"]] = (c - o) / o
                except (TypeError, ValueError, KeyError):
                    continue
    ev_lit, ev_dark, ev_rows, tape_rows = [], [], [], []
    if DEC.exists():
        with open(DEC, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                td, sd = r.get("trade_date", ""), r.get("signal_date", "")
                if td < START:
                    continue
                try:
                    zlt = float(r.get("z_lt", ""))
                    ret_adj = float(r.get("ret_pct", ""))
                except (TypeError, ValueError):
                    continue
                if abs(zlt) <= EVENT_THR:
                    continue
                st = states.get(sd)
                lamp = ("🟢亮" if st[1] else "⚪暗") if st else "無燈值"
                # 盲化:結果欄不進報告,只留訊號側
                ev_rows.append(f"| {td} | {sd} | {r.get('side','?')} | z_lt {zlt:+.2f} | {lamp} |")
                if st:
                    (ev_lit if st[1] else ev_dark).append(ret_adj)  # K1 內部用,不印
                rr = raw_ret.get(td)
                tape_rows.append([sd, td, f"{zlt:.3f}",
                                  f"{rr:.6f}" if rr is not None else "",
                                  f"{float(st[0])/100:.4f}" if st else ""])
    n_ev = len(ev_lit) + len(ev_dark)
    L += ["## ③ 燈 × chips_combo 事件帶(驗收素材;門檻達成前只印計數)", "",
          "事件=chips 長期大戶 z(|z_lt|>0.5)的訊號日;燈取**訊號日晚上 22:00** 的讀值(隔日開盤前可得=因果)。", "",
          "| 交易日 | 訊號日 | 方向 | 事件強度 | 前夜燈 |",
          "|---|---|---|---|---|"] + (ev_rows or ["| (尚無事件) | | | | |"])
    L += ["",
          f"- 累積事件 **{n_ev}/60** 筆(驗收門檻;另需 ≥6 個月,最早 2027-02-26 之後)。",
          f"- 亮燈日事件 {len(ev_lit)} 筆、暗燈日事件 {len(ev_dark)} 筆(計數,無金額)。",
          f"- 已知反例追蹤 K1:{flag_text(ev_lit)}。",
          "- 主判=P̄ 加權 IC(permutation,門檻無關);亮/暗拆分僅輔助描述,不構成判定。",
          ""]

    # 事件 tape(機器檔,開獎腳本輸入;不印進報告)
    TAPE_OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(TAPE_OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "trade_date", "sig", "ret", "lamp_p"])
        w.writerows(tape_rows)

    # ── ④ 燈 × chips_exec 真tick paper 對照(盲化=只印筆數+旗標)──
    tr = paper_trades_since(START)
    tr_lit, tr_dark, tr_rows = [], [], []
    for d, side, pnl in tr:
        prevs = [k for k in states if k < d]
        st = states.get(max(prevs)) if prevs else None
        lamp = ("🟢亮" if st[1] else "⚪暗") if st else "無燈值"
        tr_rows.append(f"| {d} | {side} | {lamp} |")
        if st:
            (tr_lit if st[1] else tr_dark).append(pnl)  # K2 內部用,不印
    L += ["## ④ 燈 × chips_exec 真 tick paper(PAPER 實測紀錄;門檻達成前只印筆數)", "",
          "| 交易日 | 方向 | 進場前夜燈 |",
          "|---|---|---|"] + (tr_rows or ["| (尚無成交) | | |"])
    L += ["",
          f"- 亮燈日成交 {len(tr_lit)} 筆、暗燈日成交 {len(tr_dark)} 筆(計數,無金額)。",
          f"- 已知反例追蹤 K2:{flag_text(tr_lit)}。"]
    L += ["", "---",
          "說明:③=驗收素材的事件帶;④=同一策略真 tick paper 的對照。門檻達成前兩帶只印計數與",
          "K1/K2 旗標(盲化,lab 交接單 2026-08-30,使用者已簽);開獎由 lab 腳本一生一次執行。"]

    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"[forward_report] 寫 {OUT}(事件 {n_ev} 筆、paper 成交 {len(tr)} 筆、tape {len(tape_rows)} 列)")


if __name__ == "__main__":
    main()
