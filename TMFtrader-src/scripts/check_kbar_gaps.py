"""1min 歷史 K 線完整性檢查器 — 迴圈 #3b 的裁判。

對照 scripts/market_holidays.txt 與交易時段,掃 data/history/{SYM}_1min_YYYYMM.parquet
列出「該有而缺」的交易日。判準(從事故學來):
  - 日盤 0 bar = GAP(硬判);覆蓋率 <50% = WARN(薄量或半殘)
  - 夜盤 0 bar = WARN 不硬判(假日前夕夜盤可能不開,硬判會誤報)
  - 非交易日出現日盤 bar = WARN(synth/事故訊號,參 nontrading_day_entry_guard)
  - 月檔整個缺 = 該月所有交易日 GAP
注意:repo parquet 只到 2026-05,06 起是 tick CSV,先跑 _resample_ticks_to_1min.py 落地
月檔再檢查(這正是迴圈的補洞動作)。

用法:python scripts/check_kbar_gaps.py TMFR1 --from 2026-01-01 --to 2026-05-29
exit code: 0 = GAPS: 0 / 1 = 有 GAP / 2 = 用法錯誤。最後一行固定 GAPS: N 供迴圈 grep。
"""
import sys, argparse
from datetime import date, datetime, time, timedelta
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DAY_START, DAY_END = time(8, 46), time(13, 46)     # 右邊界標籤
NIGHT_START, NIGHT_TAIL_END = time(15, 1), time(5, 1)
DAY_FULL = 300          # 日盤滿載約 300 根
LOW_FRAC = 0.5


def load_holidays():
    p = ROOT / "scripts" / "market_holidays.txt"
    days = set()
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            days.add(date.fromisoformat(line))
    return days


def month_iter(d0: date, d1: date):
    y, m = d0.year, d0.month
    while (y, m) <= (d1.year, d1.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sym")
    ap.add_argument("--from", dest="d0", required=True)
    ap.add_argument("--to", dest="d1", required=True)
    ap.add_argument("--dir", default=str(ROOT / "data" / "history"))
    a = ap.parse_args()
    d0, d1 = date.fromisoformat(a.d0), date.fromisoformat(a.d1)
    if d1 < d0:
        print("--to 早於 --from"); sys.exit(2)

    holidays = load_holidays()
    frames, missing_months = [], []
    for y, m in month_iter(d0, d1 + timedelta(days=1)):   # 多讀一格月:夜盤尾巴跨月
        p = Path(a.dir) / f"{a.sym}_1min_{y}{m:02d}.parquet"
        if p.exists():
            frames.append(pd.read_parquet(p, columns=["ts"]))
        else:
            missing_months.append((y, m))
    if not frames:
        print(f"範圍內完全無 {a.sym} 月檔於 {a.dir}"); sys.exit(2)
    ts = pd.concat(frames)["ts"]
    d, t = ts.dt.date, ts.dt.time
    missing_set = set(missing_months[:-1]) if missing_months and missing_months[-1] == (
        (d1 + timedelta(days=1)).year, (d1 + timedelta(days=1)).month) and (
        d1.year, d1.month) != missing_months[-1] else set(missing_months)

    gaps, warns = [], []
    cur = d0
    while cur <= d1:
        is_trading = cur.weekday() < 5 and cur not in holidays
        if (cur.year, cur.month) in missing_set:
            if is_trading:
                gaps.append(f"{cur} day: 月檔缺({cur.year}-{cur.month:02d})")
            cur += timedelta(days=1)
            continue
        day_mask = (d == cur) & (t >= DAY_START) & (t <= DAY_END)
        n_day = int(day_mask.sum())
        if is_trading:
            if n_day == 0:
                gaps.append(f"{cur} day: 0 bars")
            elif n_day < DAY_FULL * LOW_FRAC:
                warns.append(f"{cur} day: 覆蓋率低 {n_day}/{DAY_FULL}")
            nxt = cur + timedelta(days=1)
            night_mask = ((d == cur) & (t >= NIGHT_START)) | ((d == nxt) & (t <= NIGHT_TAIL_END))
            if int(night_mask.sum()) == 0:
                warns.append(f"{cur} night: 0 bars(假日前夕夜盤不開屬正常,人眼判)")
        else:
            if n_day > 0:
                warns.append(f"{cur} 非交易日卻有 {n_day} 根日盤bar(synth/事故?)")
        cur += timedelta(days=1)

    print(f"=== check_kbar_gaps {a.sym} {d0}..{d1} ===")
    for g in gaps:
        print(f"  [GAP]  {g}")
    for w in warns:
        print(f"  [WARN] {w}")
    print(f"GAPS: {len(gaps)}  (WARN: {len(warns)})")
    sys.exit(1 if gaps else 0)


if __name__ == "__main__":
    main()
