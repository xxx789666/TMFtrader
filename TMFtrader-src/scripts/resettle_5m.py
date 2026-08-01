"""把 chips / maxpain 的日K 歷史 tape,用 lab TXF 日盤 5m 重新結算(+1%加碼/−2%停損 的順序+跳空),
輸出 5m 版 tape(較貼近現實:解了日K 不知順序、假設剛好成交在停損價 的盲點)。仍無 bid/ask 滑價。

用法:python scripts/resettle_5m.py <chips|maxpain> <in_daily.csv> <out_5m.csv>
"""
import sys
import pandas as pd

PARQUET = r"C:\Users\xx\Desktop\tmf-strategy-lab-main\tmf-strategy-lab-main\data\day\TXF_day_5m.parquet"
PV = 50.0          # 小台
SCALE = 0.01
STOP = 0.02
NOTE = "5m日內重結算(順序+跳空)、小台pv50、無bid/ask滑價"


def load_5m():
    df = pd.read_parquet(PARQUET)
    df["d"] = pd.to_datetime(df["datetime"]).dt.strftime("%Y-%m-%d")
    bars = {}
    for d, g in df.sort_values("datetime").groupby("d"):
        bars[d] = list(zip(g["open"], g["high"], g["low"], g["close"]))
    return bars, sorted(bars)


def maxpain_5m(entry_d, ed_d, bars, dates, be_after_scale=False):
    """be_after_scale=True:加第2口後把停損移到成本價(均價 S1×1.005)。"""
    hold = [d for d in dates if entry_d <= d <= ed_d]
    if not hold or entry_d not in bars:
        return None
    S1 = bars[entry_d][0][0]
    lvl, stp = S1 * (1 + SCALE), S1 * (1 - STOP)
    be = S1 * (1 + SCALE / 2)                 # 加碼後成本價(兩口均價)
    added, S2 = False, None
    last_c, last_d = S1, entry_d
    for d in hold:
        if d not in bars:
            continue
        for (o, h, l, c) in bars[d]:
            last_c, last_d = c, d
            cur = be if (added and be_after_scale) else stp
            if o <= cur:                       # 跳空穿(當前)停損
                return _mk(S1, S2 if added else None, o, "stop_gap", added, d)
            if not added and h >= lvl:         # 同根加碼 → 停損即刻收緊到成本價
                added, S2 = True, lvl
                if be_after_scale:
                    cur = be
            if l <= cur:
                rs = "be_stop" if (added and be_after_scale) else "stop"
                return _mk(S1, S2 if added else None, cur, rs, added, d)
    return _mk(S1, S2 if added else None, last_c, "settle", added, last_d)


def _mk(S1, S2, exit_px, reason, added, exit_d):
    pts = (exit_px - S1) + ((exit_px - S2) if added else 0.0)
    return dict(S1=round(S1, 1), S2=(round(S2, 1) if S2 else ""), exit_px=round(exit_px, 1),
                reason=reason, added=added, exit_d=exit_d,
                pts=round(pts, 1), pnl=round(pts * PV, 0), lots=2 if added else 1)


def chips_5m(trade_d, side, bars):
    if trade_d not in bars:
        return None
    barz = bars[trade_d]
    S1 = barz[0][0]
    long = side == "long"
    stp = S1 * (1 - STOP) if long else S1 * (1 + STOP)
    last_c = S1
    for (o, h, l, c) in barz:
        last_c = c
        if (long and o <= stp) or (not long and o >= stp):     # 跳空穿損
            ex, rs = o, "stop_gap"
            break
        if (long and l <= stp) or (not long and h >= stp):     # 盤中觸損
            ex, rs = stp, "stop"
            break
    else:
        ex, rs = last_c, "收盤平倉"
    pts = (ex - S1) if long else (S1 - ex)
    return dict(S1=round(S1, 1), exit=round(ex, 1), reason=rs,
                pts=round(pts, 1), pnl=round(pts * PV, 0), ret=round(pts / S1 * 100, 3))


def run(strategy, in_csv, out_csv):
    tape = pd.read_csv(in_csv)
    bars, dates = load_5m()
    rows = []
    d_pnl, m_pnl = [], []
    for _, r in tape.iterrows():
        d_pnl.append(float(r["pnl"]))
        if strategy == "maxpain":
            o = maxpain_5m(str(r["entry_t1"]), str(r["expiry_ed"]), bars, dates)
            if o is None:
                rows.append(r.to_dict()); m_pnl.append(float(r["pnl"])); continue
            m_pnl.append(o["pnl"])
            rows.append(dict(signal_t=r["signal_t"], expiry_ed=r["expiry_ed"], maxpain=r["maxpain"],
                             S_close=r["S_close"], dist=r["dist"], entry_t1=r["entry_t1"],
                             S1=o["S1"], added=o["added"], S2=o["S2"], exit_reason=o["reason"],
                             exit_date=o["exit_d"], exit_px=o["exit_px"], pnl_pts=o["pts"],
                             pnl=o["pnl"], lots=o["lots"], note=NOTE))
        else:  # chips
            o = chips_5m(str(r["trade_date"]), str(r["side"]), bars)
            if o is None:
                rows.append(r.to_dict()); m_pnl.append(float(r["pnl"])); continue
            m_pnl.append(o["pnl"])
            rows.append(dict(trade_date=r["trade_date"], signal_date=r["signal_date"], combo=r["combo"],
                             z_flow=r["z_flow"], z_lt=r["z_lt"], side=r["side"], entry=o["S1"],
                             exit=o["exit"], exit_reason=o["reason"], pnl_pts=o["pts"], pnl=o["pnl"],
                             ret_pct=o["ret"], slippage_note=NOTE))
    pd.DataFrame(rows).to_csv(out_csv, index=False, encoding="utf-8-sig")

    def pf(p):
        gl = -sum(x for x in p if x < 0)
        return sum(x for x in p if x > 0) / gl if gl > 0 else 99
    print(f"{strategy}: {len(rows)}筆 | 日K 淨{sum(d_pnl):+,.0f} PF{pf(d_pnl):.2f} "
          f"→ 5m 淨{sum(m_pnl):+,.0f} PF{pf(m_pnl):.2f} | 寫 {out_csv}")


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2], sys.argv[3])
