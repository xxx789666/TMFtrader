# -*- coding: utf-8 -*-
"""chips_exec live 滑價序列 + live RR 對照(2026-07-27 起,7/27 濾網判死案的後續工程)。

從 VPS chips 引擎 log 抽「決策價(訊號讀取)/實際成交(FILL)」建進場滑價序列,
出場合併:①引擎自己的 SELL FILL(規則出場)②data/manual_close_vs_rule_tape.csv(手動/特例)。
對照基準 = 2020-2025 回測(733 訊號日,MXFR1 1分K,首分Close 進場含滑價):
  總 +11,192 點 / 勝率 54% / PF 1.33 / 平均 +15.3 點/筆。
口徑注意:live 滑價=「成交 vs 決策價」(決策價=開盤前最後所見價,含集合競價跳動),
比回測的「首分Close vs 開盤價」口徑天生偏大,兩者不能直接互減。
用法(本機):python TMFtrader-src/scripts/chips_live_slippage.py
輸出:TMFtrader-src/data/chips_live_slippage.csv + console 摘要
"""
import csv
import io
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "chips_live_slippage.csv"
MANUAL_TAPE = ROOT / "data" / "manual_close_vs_rule_tape.csv"
BT = dict(n=733, total=11192, wr=0.54, pf=1.33, avg=15.3)   # 2020-2025 滑價後口徑 baseline

ANSI = re.compile(r"\x1b\[[0-9;]*m")
SSH = ("gcloud compute ssh ultratrader-night --zone=asia-east1-b "
       "--account=x011training@gmail.com --project=project-ae93c5d6-cf6e-402d-969 --command="
       "'grep -h \"訊號讀取\\|log_trade\\|\\[FILL\\]\" "
       "/home/xx/TMFtrader-src/data/logs/TMFtrader_chips_exec_*.log "
       "/home/xx/TMFtrader-src/data/logs/chips_exec_live_*.log 2>/dev/null | sort -u'")


def fetch_lines():
    r = subprocess.run(["wsl", "bash", "-c", SSH], capture_output=True, timeout=180)
    return [ANSI.sub("", ln) for ln in r.stdout.decode("utf-8", "replace").splitlines()]


def parse(lines):
    """回 {date: row};只認有日期前綴的行(TMFtrader_*.log 格式),nohup 無日期行天然去重。"""
    # 不錨定行首日期:nohup log(chips_exec_live_*.log)無日期前綴,但行內帶 trade_date=
    sig = re.compile(r"訊號讀取 → trade_date=(\S+) side=(long|short) "
                     r"combo=([\d.+-]+) → 進場 \w+ @ ([\d.]+)")
    fill = re.compile(r"^(\d{4}-\d{2}-\d{2}) .*\[FILL\] 成交: (BUY|SELL) 1口 @ ([\d.]+)")
    fill_nodate = re.compile(r"^\d{2}:\d{2}:\d{2} .*\[FILL\] 成交: (BUY|SELL) 1口 @ ([\d.]+)")
    rows, undated = {}, []
    for ln in lines:
        m = sig.search(ln)
        if m:
            d = m.group(1)
            rows.setdefault(d, {}).update(date=d, side=m.group(2), combo=float(m.group(3)),
                                          decision=float(m.group(4)))
        m = fill.match(ln)
        if m:
            d, act, px = m.group(1), m.group(2), float(m.group(3))
            r = rows.setdefault(d, {"date": d})
            entry_act = "BUY" if r.get("side", "long") == "long" else "SELL"
            if act == entry_act and "fill" not in r:
                r["fill"] = px
            elif act != entry_act:
                r["exit_px"] = px
                r["exit_kind"] = "rule"
            continue
        m = fill_nodate.match(ln)
        if m:
            undated.append((m.group(1), float(m.group(2))))
    # 無日期 FILL(nohup 格式)fallback:配給「缺 fill 且方向相符、價差 <2%」的唯一候選列
    for act, px in undated:
        cand = [r for r in rows.values()
                if "fill" not in r and "decision" in r
                and act == ("BUY" if r["side"] == "long" else "SELL")
                and abs(px - r["decision"]) / r["decision"] < 0.02]
        if len(cand) == 1:
            cand[0]["fill"] = px
    return rows


def merge_manual(rows):
    if not MANUAL_TAPE.exists():
        return
    with open(MANUAL_TAPE, encoding="utf-8-sig") as f:
        for m in csv.DictReader(f):
            d = m["date"]
            if d in rows and "exit_px" not in rows[d]:
                rows[d]["exit_px"] = float(m["manual_px"])
                rows[d]["exit_kind"] = "manual/" + (m.get("note", "")[:12] or "tape")


def main():
    rows = parse(fetch_lines())
    merge_manual(rows)
    # 舊 CSV 合併:VPS log 會輪替,log 裡撈不到的歷史日期沿用上次結果(防 tape 縮水)
    if OUT.exists():
        with open(OUT, encoding="utf-8-sig") as f:
            for old in csv.DictReader(f):
                d = old["date"]
                if d not in rows or "decision" not in rows[d] or "fill" not in rows[d]:
                    for k in ("combo", "decision", "fill", "exit_px", "pnl_pts"):
                        if old.get(k):
                            try:
                                old[k] = float(old[k])
                            except ValueError:
                                pass
                    rows[d] = old
    out = []
    for d in sorted(rows):
        r = rows[d]
        if "decision" not in r or "fill" not in r:
            continue
        sgn = 1 if r["side"] == "long" else -1
        r["slip"] = (r["fill"] - r["decision"]) * sgn
        if r.get("exit_px") not in ("", None):
            r["pnl_pts"] = (r["exit_px"] - r["fill"]) * sgn
        else:
            r["exit_px"] = ""; r["exit_kind"] = "OPEN"; r["pnl_pts"] = ""
        out.append(r)
    if not out:
        print("無 live 交易紀錄"); return
    cols = ["date", "side", "combo", "decision", "fill", "slip", "exit_px", "exit_kind", "pnl_pts"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader(); w.writerows(out)

    slips = [r["slip"] for r in out]
    closed = [r for r in out if r["pnl_pts"] != ""]
    print(f"== chips_exec live tape({out[0]['date']} 起,n={len(out)},其中未平 "
          f"{len(out)-len(closed)}) → {OUT.name} ==")
    for r in out:
        pnl = f"{r['pnl_pts']:+.0f}" if r["pnl_pts"] != "" else "OPEN"
        print(f"  {r['date']} {r['side']:5s} 決策{r['decision']:.0f} → 成交{r['fill']:.0f} "
              f"(滑{r['slip']:+.0f}) 出場{r['exit_px'] or '—'}[{r['exit_kind']}] pnl={pnl}")
    slips_s = sorted(slips)
    print(f"\n進場滑價: mean {sum(slips)/len(slips):+.0f} 點 | "
          f"median {slips_s[len(slips)//2]:+.0f} | range [{min(slips):+.0f},{max(slips):+.0f}]"
          f" | ⚠️ 決策價口徑(含競價跳動),≠回測的開盤價口徑")
    if closed:
        tot = sum(r["pnl_pts"] for r in closed)
        wins = sum(1 for r in closed if r["pnl_pts"] > 0)
        print(f"已平倉 {len(closed)} 筆: 總 {tot:+,.0f} 點 勝率 {wins/len(closed)*100:.0f}% "
              f"平均 {tot/len(closed):+.0f} 點/筆")
        print(f"回測 baseline(2020-2025,n={BT['n']},滑價後口徑): "
              f"平均 {BT['avg']:+.1f} 點/筆 勝率 {BT['wr']*100:.0f}% PF {BT['pf']}")
        print(f"⚠️ n={len(closed)} 遠不足以比較 RR;此工具目的=累積序列,n≥30 再下結論"
              f";出場含手動干預(exit_kind 標註),非純規則損益")


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    main()
