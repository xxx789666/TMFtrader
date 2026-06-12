"""maxpain 出場變體回測 — 全時段 5m(含夜盤)修正版。

❗ 為什麼要這版:maxpain 抱倉到週選結算、天天過夜,但先前所有回測(maxpain_trail_backtest.py)
用 TXF_day_5m(只日盤 08:45-13:45)→ 停損/加碼/trail 在夜盤的動作全是盲區 = 回測沒忠實執行策略
(2026-06-12 user 指正)。本版用 TXF_full_5m(日+夜盤、時序連續)逐 bar 走完整持有路徑,
與 live 引擎(24h tick 盯停損/trail)同口徑。

變體:frozen(−2%停損死抱)/ noSL(無停損死抱)/ trail −1.0/−1.25/−1.5%(24h)/ trail1.25be(地板=成本)
另附 dayStop(停損只在日盤評估)供量化「舊回測夜盤盲區」的影響方向。

用法:python scripts/maxpain_fullsession_backtest.py
"""
import pandas as pd

PARQUET = r"C:\Users\xx\Desktop\tmf-strategy-lab-main\tmf-strategy-lab-main\data\vwap_fade\TXF_full_5m.parquet"
TAPE_2025 = r"D:\vps自動化交易每日籌碼分析報告\歷史回測\maxpain_2020_2025.csv"
TAPE_2026 = r"D:\vps自動化交易每日籌碼分析報告\歷史回測\maxpain_2026.csv"
PV, SCALE, STOP, ARM = 50.0, 0.01, 0.02, 0.01


def load():
    df = pd.read_parquet(PARQUET)
    df = df.sort_values("datetime").reset_index(drop=True)
    return df


def is_day(ts):
    t = ts.time()
    return pd.Timestamp("08:45").time() <= t <= pd.Timestamp("13:45").time()


def run_trade(df, entry_d, ed_d, variant):
    """variant: dict(trail=0或%, be=bool, stop='24h'|'day'|'none')。
    回 (pnl, reason) 或 None(無資料)。"""
    e_ts = pd.Timestamp(f"{entry_d} 08:45")
    s_ts = pd.Timestamp(f"{ed_d} 13:30")
    w = df[(df["datetime"] >= e_ts) & (df["datetime"] <= s_ts)]
    if w.empty or w.iloc[0]["datetime"].date().isoformat() != entry_d:
        return None
    S1 = float(w.iloc[0]["open"])
    lvl, stp = S1 * (1 + SCALE), S1 * (1 - STOP)
    trail, be, stop_mode = variant["trail"], variant["be"], variant["stop"]
    added, hi, armed = False, S1, False
    last_c = S1
    for o, h, l, c, ts in zip(w["open"], w["high"], w["low"], w["close"], w["datetime"]):
        last_c = c
        stop_active = (stop_mode == "24h") or (stop_mode == "day" and is_day(ts))
        if stop_active and o <= stp:                      # 跳空穿停損 → 開盤價出
            return _x(S1, lvl if added else None, o), "stop_gap"
        if not added and h >= lvl:
            added = True
        if h > hi:
            hi = h
        if not armed and hi >= S1 * (1 + ARM):
            armed = True
        if trail > 0 and armed:
            tl = hi * (1 - trail)
            if l <= tl:
                cost = S1 * (1 + SCALE / 2) if added else S1
                if not (be and tl < cost):                # be 地板:trail 線低於成本不出
                    return _x(S1, lvl if added else None, tl), "trail"
        if stop_active and l <= stp:
            return _x(S1, lvl if added else None, stp), "stop"
    return _x(S1, lvl if added else None, last_c), "settle"


def _x(S1, S2, ex):
    return ((ex - S1) + ((ex - S2) if S2 else 0.0)) * PV


def main():
    df = load()
    t1 = pd.read_csv(TAPE_2025)[["entry_t1", "expiry_ed"]]
    t2 = pd.read_csv(TAPE_2026)[["entry_t1", "expiry_ed"]]
    tape = pd.concat([t1, t2], ignore_index=True).sort_values("entry_t1")
    variants = [
        ("frozen(-2%死抱,24h)",  dict(trail=0.0,    be=False, stop="24h")),
        ("dayStop(-2%只日盤)",    dict(trail=0.0,    be=False, stop="day")),
        ("noSL(無停損死抱)",      dict(trail=0.0,    be=False, stop="none")),
        ("trail-1.0%(24h)",      dict(trail=0.010,  be=False, stop="24h")),
        ("trail-1.25%(24h,實際)", dict(trail=0.0125, be=False, stop="24h")),
        ("trail-1.5%(24h)",      dict(trail=0.015,  be=False, stop="24h")),
        ("trail-1.25be(24h)",    dict(trail=0.0125, be=True,  stop="24h")),
    ]
    print(f"maxpain 全時段 5m(含夜盤)修正回測 | setups={len(tape)} | 2口加碼/小台pv50")
    for lab, v in variants:
        pn, worst, reasons = [], 0, {}
        for _, r in tape.iterrows():
            o = run_trade(df, str(r["entry_t1"]), str(r["expiry_ed"]), v)
            if o is None:
                continue
            pnl, rs = o
            pn.append(pnl); worst = min(worst, pnl); reasons[rs] = reasons.get(rs, 0) + 1
        n = len(pn); wn = [x for x in pn if x > 0]; gl = -sum(x for x in pn if x < 0)
        pf = sum(wn) / gl if gl > 0 else 99
        eq = peak = dd = 0
        for x in pn:
            eq += x; peak = max(peak, eq); dd = min(dd, eq - peak)
        print(f"  {lab:22}: {n}筆 淨{sum(pn):>+10,.0f} 勝{len(wn)}/{n}({len(wn)/n*100:.0f}%) PF{pf:.2f} "
              f"最大單{worst:>+9,.0f} MDD{dd:>+9,.0f} | {reasons}")


if __name__ == "__main__":
    main()
