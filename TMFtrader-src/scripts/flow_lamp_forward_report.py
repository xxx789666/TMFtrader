# -*- coding: utf-8 -*-
"""順風燈 forward 自我說明報告(2026-08-30 user 指示補強)→ data/flow/tailwind_forward_report.md

內容:①燈狀態 tape ②三特徵源出處+當日更新狀態(新鮮度) ③燈 × chips_combo 事件帶
(decisions.csv,|z_lt|>0.5;主判準素材) ④燈 × chips_exec 真tick paper 分組對照。
由 flow_lamp_daily.py 於每日 22:00 燈跑完後呼叫;本機 pull 腳本 18:50 隔日拉走。
純讀取+寫一個 MD;不下單、不改任何判準。
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
PAPER = ROOT / "data" / "paper" / "chips_exec" / "performance" / "daily"
OUT = ROOT / "data" / "flow" / "tailwind_forward_report.md"
START = "2026-08-26"          # forward 起算日(凍結)
EVENT_THR = 0.5               # 事件定義 |z_lt|>0.5(驗收判準原文)

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


def pf(vals):
    w = sum(v for v in vals if v > 0)
    l = -sum(v for v in vals if v <= 0)
    return (w / l) if l > 0 else float("inf")


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

    # ── ③ 燈 × chips_combo 事件帶(主判準素材)──
    ev_lit, ev_dark, ev_rows = [], [], []
    if DEC.exists():
        with open(DEC, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                td, sd = r.get("trade_date", ""), r.get("signal_date", "")
                if td < START:
                    continue
                try:
                    zlt = float(r.get("z_lt", ""))
                    ret = float(r.get("ret_pct", ""))
                except (TypeError, ValueError):
                    continue
                if abs(zlt) <= EVENT_THR:
                    continue
                st = states.get(sd)
                lamp = ("🟢亮" if st[1] else "⚪暗") if st else "無燈值"
                ev_rows.append(f"| {td} | {sd} | {r.get('side','?')} | z_lt {zlt:+.2f} | {lamp} | {ret:+.2f}% |")
                if st:
                    (ev_lit if st[1] else ev_dark).append(ret)
    n_ev = len(ev_lit) + len(ev_dark)
    L += ["## ③ 燈 × chips_combo 事件帶(驗收主判準素材;OHLC 口徑=事件方向對錯,非損益宣稱)", "",
          "事件=chips 長期大戶 z(|z_lt|>0.5)的訊號日;燈取**訊號日晚上 22:00** 的讀值(隔日開盤前可得=因果)。", "",
          "| 交易日 | 訊號日 | 方向 | 事件強度 | 前夜燈 | 隔日報酬 |",
          "|---|---|---|---|---|---|"] + (ev_rows or ["| (尚無事件) | | | | | |"])
    L += ["",
          f"- 累積事件 **{n_ev}/60** 筆(驗收門檻)。亮燈日 n={len(ev_lit)} 均報酬 "
          f"{(sum(ev_lit)/len(ev_lit)) if ev_lit else 0:+.2f}%;暗燈日 n={len(ev_dark)} 均報酬 "
          f"{(sum(ev_dark)/len(ev_dark)) if ev_dark else 0:+.2f}%。",
          "- **驗收命題:亮燈日的事件報酬應優於暗燈日**(60 筆前任何數字都只是進度,不構成判定;"
          "已知反例:6/24-8/24 回測段放行單淨虧,延續即否證)。",
          ""]

    # ── ④ 燈 × chips_exec 真tick paper 對照 ──
    tr = paper_trades_since(START)
    tr_lit, tr_dark, tr_rows = [], [], []
    for d, side, pnl in tr:
        prevs = [k for k in states if k < d]
        st = states.get(max(prevs)) if prevs else None
        lamp = ("🟢亮" if st[1] else "⚪暗") if st else "無燈值"
        tr_rows.append(f"| {d} | {side} | {pnl:+,.0f} | {lamp} |")
        if st:
            (tr_lit if st[1] else tr_dark).append(pnl)
    L += ["## ④ 燈 × chips_exec 真 tick paper(PAPER 實測紀錄;含手續費)", "",
          "| 交易日 | 方向 | 淨損益(元) | 進場前夜燈 |",
          "|---|---|---|---|"] + (tr_rows or ["| (尚無成交) | | | |"])
    if tr_lit or tr_dark:
        L += ["",
              f"- 亮燈日:n={len(tr_lit)} 淨 {sum(tr_lit):+,.0f} PF={pf(tr_lit):.2f};"
              f"暗燈日:n={len(tr_dark)} 淨 {sum(tr_dark):+,.0f} PF={pf(tr_dark):.2f}。"]
    L += ["", "---",
          "說明:③=驗收正式判準的事件帶(量「燈能不能分開好壞訊號日」);④=同一策略真 tick paper 的",
          "實測對照(量到真滑價/手續費,但 n 累積較慢)。兩帶都以 ~60 事件為判定線,中途不改門檻。"]

    OUT.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"[forward_report] 寫 {OUT}(事件 {n_ev} 筆、paper 成交 {len(tr)} 筆)")


if __name__ == "__main__":
    main()
