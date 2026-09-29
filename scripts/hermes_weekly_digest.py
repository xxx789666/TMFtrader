# -*- coding: utf-8 -*-
"""Hermes 週度稽核前置 digest(2026-07-28 部署;跑四週看抓到幾件真事)。

Task Scheduler 週六 09:00 經 scheduled_trigger.ps1 在 Hermes 之前執行。
把三節的「確定性」部分算好寫成一份緊湊 digest,Hermes 只讀這份+寫敘事+記對答案
(它有 tool-call 硬預算,不能讓它自己掃 vault)。

輸出: TMFtrader-src/data/hermes_digest/digest_latest.md(WSL 經 ~/vps_trader 可讀)
節: ①報告新鮮度巡檢 ②判活尺表 ③一致性素材(晨盤情境牆位 vs 最新收盤)
"""
import glob
import io
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
REPO = Path(__file__).resolve().parent.parent
LAB = Path(r"C:\Users\xx\Desktop\tmf-strategy-lab-main\tmf-strategy-lab-main")
VAULT = Path(r"D:\vps自動化交易每日籌碼分析報告\每日籌碼分析報告")
OUT_DIR = REPO / "TMFtrader-src" / "data" / "hermes_digest"
OUT_DIR.mkdir(parents=True, exist_ok=True)
HOLIDAYS = set()
hf = REPO / "TMFtrader-src" / "scripts" / "market_holidays.txt"
if hf.exists():
    HOLIDAYS = {x.strip() for x in hf.read_text(encoding="utf-8", errors="ignore").split() if x.strip()}

def last_trading_day(ref=None):
    d = ref or date.today()
    while d.weekday() >= 5 or d.isoformat() in HOLIDAYS:
        d -= timedelta(days=1)
    return d

def trading_lag(latest, expect):
    """latest 與 expect 之間差幾個交易日(0=最新)。"""
    if latest is None:
        return 99
    lag, d = 0, expect
    while d > latest and lag < 99:
        d -= timedelta(days=1)
        if d.weekday() < 5 and d.isoformat() not in HOLIDAYS:
            lag += 1
    return lag

L = []
today = date.today()
expect = last_trading_day(today - timedelta(days=1) if today.weekday() < 5 else today)
L.append(f"# Hermes 週度稽核 digest — {today.isoformat()}(基準交易日 {expect.isoformat()})")
L.append("(前置腳本產;數字為確定性計算,Hermes 負責敘事與異常判讀)")

# ── ① 報告新鮮度巡檢 ──
L.append("\n## ① 報告新鮮度巡檢(vault 各報告線)")
L.append("| 報告線 | 最新檔 | 落後交易日 | 檔數 |")
L.append("|---|---|---|---|")
DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")
warn = []
if VAULT.exists():
    for sub in sorted(p for p in VAULT.iterdir() if p.is_dir()):
        dates = []
        for f in sub.glob("*.md"):
            m = DATE_RE.search(f.name)
            if m:
                try:
                    dates.append(date.fromisoformat(m.group(1)))
                except ValueError:
                    pass
        if len(dates) < 8:          # 檔太少=不定期線(研究報告等),不巡
            continue
        latest = max(dates)
        lag = trading_lag(latest, expect)
        flag = " ⚠️" if lag >= 2 else ""
        if lag >= 2:
            warn.append(f"{sub.name} 落後 {lag} 交易日")
        L.append(f"| {sub.name}{flag} | {latest} | {lag} | {len(dates)} |")
else:
    L.append("| (vault 路徑不存在) | — | — | — |")
L.append(f"\n巡檢警報: {'; '.join(warn) if warn else '無(全數新鮮)'}")

# ── ② 判活尺表 ──
L.append("\n## ② 判活尺表(各累積 tape n / 門檻)")
L.append("| tape | n | 門檻 | 進度 |")
L.append("|---|---|---|---|")

def rows_of(p):
    try:
        with open(p, encoding="utf-8-sig", errors="ignore") as f:
            return max(0, sum(1 for _ in f) - 1)
    except Exception:
        return None

def tape(name, path, thr, note=""):
    n = rows_of(path) if path else None
    if n is None:
        L.append(f"| {name} | 檔缺 | {thr} | ⚠️ 找不到 |")
        return
    pct = min(100, int(n / thr * 100)) if thr else 0
    L.append(f"| {name} | {n} | {thr} | {pct}%{(' ' + note) if note else ''} |")

def g1(pat, root):
    h = glob.glob(str(root / pat), recursive=True)
    return h[0] if h else None

tape("手動vs規則", REPO / "TMFtrader-src" / "data" / "manual_close_vs_rule_tape.csv", 10)
tape("chips滑價", REPO / "TMFtrader-src" / "data" / "chips_live_slippage.csv", 30)
tape("2330撤單", g1("data/**/*pull2330*.csv", LAB), 100)
tape("三段式底部", g1("data/**/*bottom3*.csv", LAB), 15, "(另歷史n=5)")
tape("釣魚魚況log", LAB / "data" / "opt" / "fishing_night_log.csv", 60, "(棲地統計)")
tape("釣魚v3出場shadow", g1("data/opt/fishing_exit_shadow*.csv", LAB), 1, "(有料即算;7/28 部署,持倉期才記)")
hmm_n = len(glob.glob(str(LAB / "data" / "hmm" / "states_*.csv")))
L.append(f"| HMM盤中狀態 | {hmm_n} 交易日 | 30(8/25健檢) | {min(100, int(hmm_n/30*100))}% |")

# ── ③ 一致性素材(晨盤情境 sidecar vs 最新收盤) ──
L.append("\n## ③ 一致性素材(供敘事判讀,非警報)")
try:
    scs = sorted(glob.glob(str(VAULT / "TXO晨盤情境" / "*_scenario_claims.json")))
    if scs:
        import json
        sc = json.loads(Path(scs[-1]).read_text(encoding="utf-8"))
        import csv as _csv
        close = None
        with open(LAB / "data" / "taiex_daily.csv", encoding="utf-8-sig") as f:
            for r in _csv.DictReader(f):
                close = float(r["Close"])
        pw, cw, mp = sc.get("put_wall"), sc.get("call_wall"), sc.get("mp_val")
        L.append(f"- 最新晨盤情境({Path(scs[-1]).name[:10]}): put牆 {pw} / call牆 {cw} / maxpain {mp}"
                 f"(stale={sc.get('mp_stale')})")
        if close and pw and cw:
            L.append(f"- 最新現貨收盤 {close:,.0f}: 距 put 牆 {(close-pw)/close*100:+.1f}% / "
                     f"距 call 牆 {(cw-close)/close*100:+.1f}%(牆離價 >8% = 挑牆可疑)")
except Exception as e:
    L.append(f"- (一致性素材產生失敗: {str(e)[:60]})")

out = OUT_DIR / "digest_latest.md"
out.write_text("\n".join(L) + "\n", encoding="utf-8")
print(f"digest -> {out} ({len(L)} lines)")
