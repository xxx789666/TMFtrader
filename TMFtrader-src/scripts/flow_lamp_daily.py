# -*- coding: utf-8 -*-
"""資金流順風燈 — VPS 每日自動餵數 + 跑燈 + 燈訊入日報(2026-08-26 裝機)。

規格權威=lab docs/handoff_flow_tailwind_lamp_2026_08_25.md + 操作交接單 2026_08_26。
cron 22:00 TST(14:00 UTC;f1 FinMind 融資 ~21:00 後可得)。跑在 lampenv(獨立 venv,
hmmlearn 0.3.3 鎖版,不碰交易 .venv)。

每日流程:
  1) 抓三特徵 raw:f1=FinMind 全市場融資金額 TodayBalance、f2=chips 管線 history.csv 的
     all_ratio(=(t10b−t10s)/oi,同口徑直接重用)、f3=FinMind USD/TWD 即期中價。
  2) 轉換 Δlog/Δ → append 進 data/flow/flow_hmm_feature_table.csv(只 append 新日期,
     歷史列永不修改;z 欄用 canonical 窗(2020-01-02~2022-12-30)在表內算出的 mu/sd)。
  3) 跑 flow_tailwind_lamp_2026_08_25.py → 燈訊一行:
     a) append 到今日報告 data/reports/chips_combo/YYYY-MM-DD.md(冪等:已有就跳過)
     b) append 到 data/flow/tailwind_lamp_forward_log.txt(forward tape,獨立留檔)

故障政策(fail-open 顯示、絕不猜值):任一特徵當日抓不到 → 該日不 append、不插值,
燈照跑(顯示最後可得日+stale 警告)、日報照登含警告;補到資料當天自動補齊缺列
(每次跑會回填 last_table_date 之後所有三源齊備的日期)。FinMind 異常 → 停更+TG,不換源。

紅線:不下單、不重訓、不改門檻(0.55)。燈值不得進任何引擎。
"""
import json
import os
import sys
import time as _time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

os.environ.setdefault("TZ", "Asia/Taipei")
try:
    _time.tzset()
except AttributeError:
    pass

ROOT = Path(__file__).resolve().parent.parent
FEAT = ROOT / "data" / "flow" / "flow_hmm_feature_table.csv"
FWD_LOG = ROOT / "data" / "flow" / "tailwind_lamp_forward_log.txt"
HIST = ROOT / "data" / "chips_combo" / "history.csv"
REPORT_DIR = ROOT / "data" / "reports" / "chips_combo"
LAMP = ROOT / "scripts" / "flow_tailwind_lamp_2026_08_25.py"
FINMIND = "https://api.finmindtrade.com/api/v4/data"
CANON = ("2020-01-02", "2022-12-30")   # z 參數 canonical fit 窗(凍結)

sys.stdout.reconfigure(encoding="utf-8")


def _env(key):
    """讀 .env(lampenv 無 python-dotenv → 手動 parse;cp950/BOM 陷阱走 utf-8 帶錯誤容忍)。"""
    v = os.getenv(key, "").strip()
    if v:
        return v
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""


def tg(msg):
    """TG 告警(失敗吞掉,絕不擋主流程)。"""
    tok, chat = _env("TG_BOT_TOKEN"), _env("TG_CHAT_ID")
    if not tok or not chat:
        return
    try:
        data = urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode()
        urllib.request.urlopen(
            urllib.request.Request(f"https://api.telegram.org/bot{tok}/sendMessage", data=data),
            timeout=30)
    except Exception:
        pass


def fetch_finmind(dataset, data_id, start):
    """FinMind v4 → list[dict]。異常丟例外(fail-loud,由呼叫端決定停更)。"""
    q = {"dataset": dataset, "start_date": start}
    if data_id:
        q["data_id"] = data_id
    tok = _env("FINMIND_TOKEN")
    if tok:
        q["token"] = tok
    req = urllib.request.Request(f"{FINMIND}?{urllib.parse.urlencode(q)}",
                                 headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        j = json.loads(r.read().decode("utf-8"))
    if j.get("status") != 200:
        raise RuntimeError(f"FinMind {dataset} status={j.get('status')} msg={j.get('msg')}")
    return j.get("data", [])


def get_f1_series(start):
    """date → 全市場融資金額 TodayBalance(f1 raw)。"""
    rows = fetch_finmind("TaiwanStockTotalMarginPurchaseShortSale", "", start)
    out = {}
    for r in rows:
        if r.get("name") == "MarginPurchaseMoney":
            try:
                v = float(r["TodayBalance"])
                if v > 0:
                    out[str(r["date"])[:10]] = v
            except (KeyError, TypeError, ValueError):
                pass
    return out


def get_f3_series(start):
    """date → USD/TWD 即期中價(f3 raw)。FinMind 缺值用 -99 → 濾掉。"""
    rows = fetch_finmind("TaiwanExchangeRate", "USD", start)
    out = {}
    for r in rows:
        try:
            b, s = float(r.get("spot_buy", -99)), float(r.get("spot_sell", -99))
            if b > 0 and s > 0:
                out[str(r["date"])[:10]] = (b + s) / 2
        except (TypeError, ValueError):
            pass
    return out


def get_f2_series():
    """date → all_ratio(f2 raw;chips 管線 history.csv 直接重用,同口徑 (t10b−t10s)/oi)。"""
    import csv
    out = {}
    with open(HIST, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                out[str(r["date"])[:10]] = float(r["all_ratio"])
            except (KeyError, TypeError, ValueError):
                pass
    return out


def main():
    import math
    import pandas as pd

    today = date.today()
    ft = pd.read_csv(FEAT)
    ft["date"] = pd.to_datetime(ft["date"])
    last_d = ft["date"].max().date()
    print(f"[lamp_daily] 特徵表最後日 {last_d} | 今天 {today}")

    appended = []
    if last_d < today:
        # 三源各抓「最後表日往前 10 天」起 → 能算出 last_d 之後每個新日期的 Δ(連前一可得日)
        start = (last_d - timedelta(days=10)).isoformat()
        try:
            f1s = get_f1_series(start)
            f3s = get_f3_series(start)
            f2s = get_f2_series()
        except Exception as e:
            print(f"🔴 資料源抓取失敗 → 今日停更(不插值、不換源): {e}")
            tg(f"🔴 [順風燈] 特徵抓取失敗、今日停更(明日自動補):{e}")
            f1s = f3s = f2s = None

        if f1s is not None:
            # canonical 窗 mu/sd(從凍結表算,全精度;handoff 印的凍結值=同一來源的截斷版)
            m = (ft["date"] >= CANON[0]) & (ft["date"] <= CANON[1])
            fcols = ["f1_dlog_margin", "f2_d_net_t10_oi", "f3_dlog_usdtwd_mid"]
            mus = ft.loc[m, fcols].mean(0)
            sds = ft.loc[m, fcols].std(0, ddof=0)

            def delta_for(series, d, logd=True):
                """series 內 d 與其前一可得日的 Δ(log);任一端缺 → None。"""
                ds = sorted(series)
                if d not in series:
                    return None
                i = ds.index(d)
                if i == 0:
                    return None
                a, b = series[ds[i - 1]], series[d]
                return (math.log(b) - math.log(a)) if logd else (b - a)

            # 逐一補 last_d 之後、三源齊備的日期(漏日自動回填)
            cand = sorted({d for d in (set(f1s) | set(f2s) | set(f3s))
                           if last_d.isoformat() < d <= today.isoformat()})
            if FEAT.read_bytes()[-1:] not in (b"\n", b""):   # 防呆:無尾換行時 append 會黏行
                with open(FEAT, "a", encoding="utf-8") as f:
                    f.write("\n")
            with open(FEAT, "a", encoding="utf-8", newline="") as f:
                for d in cand:
                    v1 = delta_for(f1s, d, logd=True)
                    v2 = delta_for(f2s, d, logd=False)
                    v3 = delta_for(f3s, d, logd=True)
                    if v1 is None or v2 is None or v3 is None:
                        miss = [n for n, v in (("f1融資", v1), ("f2大戶", v2), ("f3匯率", v3)) if v is None]
                        print(f"  {d}: 缺 {'/'.join(miss)} → 不 append(留待補齊)")
                        continue
                    z1 = (v1 - mus.iloc[0]) / sds.iloc[0]
                    z2 = (v2 - mus.iloc[1]) / sds.iloc[1]
                    z3 = (v3 - mus.iloc[2]) / sds.iloc[2]
                    # float() 拆 numpy 標量:z 是 pandas 算的 np.float64,直接 !r 會把
                    # "np.float64(...)" 字串寫進 CSV(2026-08-26~28 三列踩過,已修)
                    f.write(f"{d},{v1!r},{v2!r},{v3!r},"
                            f"{float(z1)!r},{float(z2)!r},{float(z3)!r}\n")
                    appended.append(d)
                    print(f"  {d}: append f1={v1:+.6f} f2={v2:+.6f} f3={v3:+.6f}")
    if not appended:
        print("[lamp_daily] 本次無新列(資料未齊或已最新)")

    # 跑燈(lampenv 同直譯器;cwd=ROOT 讓腳本內相對路徑成立)
    import subprocess
    r = subprocess.run([sys.executable, str(LAMP), str(FEAT)], cwd=str(ROOT),
                       capture_output=True, text=True, timeout=600)
    out_lines = (r.stdout or "").strip().splitlines()
    if r.returncode != 0 or not out_lines:
        print(f"🔴 燈腳本失敗 rc={r.returncode}\n{r.stderr[-500:] if r.stderr else ''}")
        tg(f"🔴 [順風燈] 燈腳本執行失敗 rc={r.returncode},今日日報無燈訊。")
        sys.exit(1)
    print("\n".join(out_lines))
    lamp_line = out_lines[0]
    warn_lines = [x for x in out_lines[1:] if x.startswith("⚠️")]
    if warn_lines:
        tg(f"⚠️ [順風燈] {warn_lines[0]}")

    # forward tape(獨立留檔;起算日=本檔首次自動產出日)
    FWD_LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%F %T")
    prev = FWD_LOG.read_text(encoding="utf-8") if FWD_LOG.exists() else ""
    if lamp_line not in prev:
        with open(FWD_LOG, "a", encoding="utf-8") as f:
            f.write(f"{stamp} | {lamp_line}" + ("".join("\n  " + w for w in warn_lines)) + "\n")

    # 月頻環境儀表(每月 1 日 21:50 cron 跑 gauge → drift_gauge_last.txt;36h 內算新鮮,隨燈訊入報)
    gauge_line = ""
    gf = ROOT / "data" / "flow" / "drift_gauge_last.txt"
    try:
        if gf.exists() and (_time.time() - gf.stat().st_mtime) < 36 * 3600:
            for ln in gf.read_text(encoding="utf-8").splitlines():
                if "[順風燈環境檢查]" in ln:
                    gauge_line = ln.strip()
                    break
    except Exception:
        pass

    # 燈訊入今日報告(冪等:已有該節就跳過;報告 15:14 已產,22:00 append)
    md = REPORT_DIR / f"{today.isoformat()}.md"
    if md.exists():
        txt = md.read_text(encoding="utf-8")
        if "[資金流順風燈]" not in txt:
            block = ["", "## 🔦 資金流順風燈(22:00 自動;純觀察、不觸發任何下單)", f"- {lamp_line}"]
            block += [f"- {w}" for w in warn_lines]
            if gauge_line:
                block.append(f"- {gauge_line}")
            with open(md, "a", encoding="utf-8") as f:
                f.write("\n".join(block) + "\n")
            print(f"燈訊已入 {md.name}")
    else:
        print(f"[warn] 今日報告不存在({md})→ 燈訊只留 forward log")


if __name__ == "__main__":
    main()
