# -*- coding: utf-8 -*-
"""throttle_hold 裸窗×偏離×熔斷覆蓋分析 + MAX_UPD_PER_MIN 校準推薦(2026-08-03)。

背景:TING 方 8/3 首次把節流的風險結構拆開(裸節流分鐘/偏離分佈/熔斷交叉),
結論「不是安全問題,是期望值訂錯」。本器把該分析常備化,供每個獵魚夜跑:

  ①裸窗:有節流的分鐘中,無熔斷/撤單保護的分鐘數(單以舊價留在市場=呆單風險窗)
  ②偏離:hold 當下 |want−quoted| 分佈;quoted>want=偏高(買貴,危險向)
  ③經濟核對:max 偏離 vs D 墊(偏離 < D−COST ⇒ 最壞仍為正=期望值折損非虧損)
  ④校準:以「總需求(成功改價+被擋)」重放各候選上限 L,印殘餘 hold 數
    → 配合裸窗偏離 p99 選 L(有經濟錨,不拍腦袋)

用法:python scripts/analyze_throttle_night.py <metrics.csv> [night|day|all(預設 night)] [YYYY-MM-DD]
night=15:00-05:00 / day=08:00-13:59;帶日期=只看該交易日(night 含次日凌晨)
"""
import sys
import datetime as dt
from collections import defaultdict

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

D_PTS = 97.0
COST_PTS = 11.0
CAND_LIMITS = (30, 45, 60, 90, 120)


def in_window(t, mode):
    hm = t.hour * 100 + t.minute
    if mode == "day":
        return 800 <= hm <= 1359
    if mode == "night":
        return hm >= 1500 or hm <= 500
    return True


def main():
    if len(sys.argv) < 2:
        sys.exit("用法:analyze_throttle_night.py <metrics.csv> [night|day|all]")
    fp = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "night"
    day_filter = sys.argv[3] if len(sys.argv) > 3 else None  # 交易日(night 模式含次日 00:00-05:00)

    holds = []            # (t, side, quoted, want)
    upd_min = defaultdict(int)    # (yyyymmddhhmm, side) -> 成功改價數
    hold_min = defaultdict(int)   # 同鍵 -> 被擋數
    protect_min = set()           # 有熔斷/搶跑撤單的分鐘
    for ln in open(fp, encoding="utf-8", errors="replace"):
        p = ln.rstrip("\n").split(",", 4)
        if len(p) < 4 or not p[0][:2] == "20":
            continue
        try:
            t = dt.datetime.fromisoformat(p[0])
        except ValueError:
            continue
        if not in_window(t, mode):
            continue
        if day_filter:
            d = t.date().isoformat()
            if mode == "night" and t.hour <= 5:
                d = (t.date() - dt.timedelta(days=1)).isoformat()
            if d != day_filter:
                continue
        kind, side, note = p[1], p[2], (p[4] if len(p) > 4 else "")
        mk = (t.strftime("%Y%m%d%H%M"), side)
        if kind == "update":
            upd_min[mk] += 1
        elif kind == "throttle_hold":
            hold_min[mk] += 1
            q = w = None
            for tok in note.split():
                if tok.startswith("quoted="):
                    q = float(tok[7:])
                elif tok.startswith("want="):
                    w = float(tok[5:])
            if q is not None and w is not None:
                holds.append((t, side, q, w))
        elif kind in ("freeze",) or (kind == "cancel" and "freeze_pull" in note):
            protect_min.add(t.strftime("%Y%m%d%H%M"))

    n_upd = sum(upd_min.values())
    n_hold = sum(hold_min.values())
    if n_upd + n_hold == 0:
        print(f"[{mode}] 窗內無改價活動")
        return

    hold_minutes = sorted({k[0] for k in hold_min})
    naked = [m for m in hold_minutes if m not in protect_min]
    devs = sorted(abs(w - q) for _, _, q, w in holds)
    n_hi = sum(1 for _, _, q, w in holds if q > w)   # 掛價偏高=買貴危險向
    n_lo = len(holds) - n_hi

    def pct(a, p):
        return a[min(len(a) - 1, int(len(a) * p))] if a else 0

    print(f"== throttle 分析[{mode}] {fp} ==")
    print(f"想改價 {n_upd + n_hold}(成功 {n_upd}/被擋 {n_hold},{n_hold / (n_upd + n_hold) * 100:.0f}%)")
    if hold_minutes:
        worst = max(hold_min.items(), key=lambda kv: kv[1])
        print(f"有節流分鐘 {len(hold_minutes)}(最慘 {worst[0][0][-4:]} {worst[0][1]}側 {worst[1]} 次)"
              f" | 有保護 {len(hold_minutes) - len(naked)} | 裸節流 {len(naked)} 分")
    if devs:
        mx = devs[-1]
        print(f"偏離:中位 {pct(devs, .5):.0f} p90 {pct(devs, .9):.0f} p99 {pct(devs, .99):.0f} max {mx:.0f} 點"
              f" | 方向:偏高(危險){n_hi}/偏低(少賺){n_lo}")
        edge_floor = D_PTS - mx - COST_PTS
        print(f"經濟核對:最壞偏離成交的淨邊際 = D{D_PTS:.0f} − {mx:.0f} − 成本{COST_PTS:.0f}"
              f" = {edge_floor:+.0f} 點 → {'✅ 期望值折損(仍正)' if edge_floor > 0 else '🔴 可能為負=安全問題!'}")

    # 校準:各候選上限的殘餘 hold(需求=成功+被擋,逐分鐘重放)
    demand = defaultdict(int)
    for mk, v in upd_min.items():
        demand[mk] += v
    for mk, v in hold_min.items():
        demand[mk] += v
    print("上限校準(殘餘被擋數@L):", end=" ")
    for L in CAND_LIMITS:
        resid = sum(max(0, v - L) for v in demand.values())
        print(f"L={L}→{resid}", end="  ")
    print("\n(選 L 原則:裸窗偏離 p99 需 < D−COST 的安全係數;夜盤資料到手後定案)")


if __name__ == "__main__":
    main()
