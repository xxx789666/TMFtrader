"""maxpain_v2 + 追蹤止盈(trailing TP)變體 — 5m 回測。

對「凍結 maxpain_v2(無止盈、抱到結算)」加一道追蹤止盈:進場後獲利「曾」漲過 +arm%(武裝),
之後從持有期最高點回落 trail(% 或 K×ATR)即出場鎖利。其餘規則完全不動(dist>0 做多、t+1 進、
+1% 加第2口、−2% 停損、否則抱到週選結算)。標的小台 MXF(pv50)。

⚠️ 資料保真度:用 lab 日盤 5m(逐根還原加碼/停損/trail 的盤中順序)。日 K 不可用(無法還原順序)。
⚠️ 驗證狀態:僅 2020-2025 in-sample(lab 5m 從 2020-03-22 起);2015-2019 無 5m → 無 OOS。
   in-sample 各 trail 值皆優於死抱,但無 OOS、且對 trail 值非單調 → 上線前須 forward(真 tick)驗證。

資料依賴(本機跑):
  - 5m:  data/day/TXF_day_5m.parquet(lab repo,預設下方 PARQUET 路徑,2020-03~)
  - setups: maxpain 歷史 setups tape(entry_t1/expiry_ed),預設 D 槽歷史回測檔

用法:python scripts/maxpain_trail_backtest.py [trail %, 例 0.015] [可選 atr 用 ATR 倍數模式]
  python scripts/maxpain_trail_backtest.py            # 掃描 %、ATR、死抱 完整表
  python scripts/maxpain_trail_backtest.py 0.015      # 只跑 trail -1.5%
"""
import sys
import pandas as pd

PARQUET = r"C:\Users\xx\Desktop\tmf-strategy-lab-main\tmf-strategy-lab-main\data\day\TXF_day_5m.parquet"
TAPE = r"D:\vps自動化交易每日籌碼分析報告\歷史回測\maxpain_2020_2025.csv"
PV, SCALE, STOP, ARM = 50.0, 0.01, 0.02, 0.01     # 小台、+1%加碼、-2%停損、+1%武裝


def load():
    df = pd.read_parquet(PARQUET)
    df["d"] = pd.to_datetime(df["datetime"]).dt.strftime("%Y-%m-%d")
    bars = {d: list(zip(g["open"], g["high"], g["low"], g["close"]))
            for d, g in df.sort_values("datetime").groupby("d")}
    dates = sorted(bars)
    daybar = {d: (b[0][0], max(x[1] for x in b), min(x[2] for x in b), b[-1][3]) for d, b in bars.items()}
    ds, trs, atr = sorted(daybar), [], {}
    for i, d in enumerate(ds):
        o, h, l, c = daybar[d]
        pc = daybar[ds[i - 1]][3] if i else c
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        atr[d] = sum(trs[-14:]) / min(len(trs), 14)
    return bars, dates, atr


def settle(entry_d, ed_d, bars, dates, atr, kind, p):
    hold = [d for d in dates if entry_d <= d <= ed_d]
    if not hold or entry_d not in bars:
        return None
    S1 = bars[entry_d][0][0]; lvl = S1 * (1 + SCALE); stp = S1 * (1 - STOP)
    A = atr.get(entry_d, S1 * 0.01)
    added, S2, hi, armed = False, None, S1, False
    for d in hold:
        if d not in bars:
            continue
        for (o, h, l, c) in bars[d]:
            if o <= stp:
                return _x(S1, S2 if added else None, o), "stop"
            if not added and h >= lvl:
                added, S2 = True, lvl
            hi = max(hi, h)
            if hi >= S1 * (1 + ARM):
                armed = True
            if armed and p:
                tl = (hi - p * A) if kind == "atr" else hi * (1 - p)
                if l <= tl:
                    return _x(S1, S2 if added else None, tl), "trail"
            if l <= stp:
                return _x(S1, S2 if added else None, stp), "stop"
    return _x(S1, S2 if added else None, bars[hold[-1]][-1][3]), "settle"


def _x(S1, S2, ex):
    return ((ex - S1) + ((ex - S2) if S2 else 0.0)) * PV


def run(tape, bars, dates, atr, kind, p, lab):
    pnls, worst, tn = [], 0, 0
    for _, r in tape.iterrows():
        o = settle(str(r["entry_t1"]), str(r["expiry_ed"]), bars, dates, atr, kind, p)
        if o is None:
            continue
        pnl, rs = o
        pnls.append(pnl); worst = min(worst, pnl); tn += (rs == "trail")
    n = len(pnls); w = [v for v in pnls if v > 0]; gl = -sum(v for v in pnls if v < 0)
    pf = sum(w) / gl if gl > 0 else 99
    eq = peak = dd = 0
    for v in pnls:
        eq += v; peak = max(peak, eq); dd = min(dd, eq - peak)
    print(f"  {lab:14}: {n}筆 淨{sum(pnls):>+9,.0f} 勝{len(w)}/{n}({len(w)/n*100:.0f}%) "
          f"PF{pf:.2f} 最大單{worst:>+8,.0f} MDD{dd:>+9,.0f} trail出{tn}")


def main():
    bars, dates, atr = load()
    tape = pd.read_csv(TAPE).sort_values("entry_t1")
    print(f"maxpain_v2 + 追蹤止盈 回測(5m、小台pv50、2口、-2%停損、+1%加碼;setups={len(tape)})")
    if len(sys.argv) > 1:
        p = float(sys.argv[1]); kind = "atr" if (len(sys.argv) > 2 and sys.argv[2] == "atr") else "pct"
        run(tape, bars, dates, atr, None, None, "死抱結算(基準)")
        run(tape, bars, dates, atr, kind, p, f"trail {p}{'xATR' if kind=='atr' else ' (%)'}")
    else:
        run(tape, bars, dates, atr, None, None, "死抱結算(基準)")
        for t in (0.01, 0.015, 0.02, 0.025):
            run(tape, bars, dates, atr, "pct", t, f"trail -{t*100:.1f}%")
        for k in (1.0, 1.5, 2.0):
            run(tape, bars, dates, atr, "atr", k, f"trail {k}xATR")


if __name__ == "__main__":
    main()
