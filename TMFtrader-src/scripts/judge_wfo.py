"""WFO OOS 驗證產物的機械裁判 — 迴圈終止條件用。

門檻同 strategies_research/LESSONS.md 定案:
  1. 兩合約平均 OOS PF > 1.10
  2. 兩合約正窗 >= 70%
  3. 跨合約同號 >= 70%
  4. (可選 --smoke) 定版 PF 不崩: final_PF >= smoke_PF * 0.6
  5. 參數收斂: 有 --bounds 時 range <= 搜索空間 50% 為硬判;無則只 WARN(人眼)

用法:
  python scripts/judge_wfo.py data/wfo_oos_<label>.json
  python scripts/judge_wfo.py data/wfo_oos_<label>.json --smoke data/wfo_oos_<label>_smoke.json
  python scripts/judge_wfo.py <label>            # 自動找 data/wfo_oos_<label>.json

exit code: 0 = PASS(全硬門檻過) / 1 = FAIL / 2 = 用法或資料錯誤
最後一行固定印 VERDICT: PASS 或 VERDICT: FAIL,供迴圈 grep。
"""
import sys, json, argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PF_MIN = 1.10
POS_MIN = 0.70
AGREE_MIN = 0.70
SMOKE_KEEP = 0.60      # 定版 PF 至少要有 smoke 的 60%
BOUND_FRAC = 0.50      # 參數 range <= 搜索空間 50%


def load(path_or_label: str):
    p = Path(path_or_label)
    if not p.exists():
        p = ROOT / "data" / f"wfo_oos_{path_or_label}.json"
    if not p.exists():
        print(f"找不到 {path_or_label}(也不是 data/wfo_oos_<label>.json)"); sys.exit(2)
    r = json.load(open(p, encoding="utf-8"))
    if not r:
        print(f"{p} 是空的"); sys.exit(2)
    return p, r


PF_CAP = 10.0          # 零虧損窗 PF 會是天文數字,單窗封頂再平均


def avg_pf(r, c):
    pfs = [min(w[c]["pf"], PF_CAP) for w in r if w[c].get("pf")]
    return sum(pfs) / len(pfs) if pfs else 0.0


def pos_ratio(r, c):
    nets = [w[c].get("net") or 0 for w in r]
    return sum(1 for x in nets if x > 0) / len(nets)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="wfo_oos JSON 路徑或 label")
    ap.add_argument("--smoke", help="smoke(5-trial)產物路徑,用來判 5→60 不崩")
    ap.add_argument("--bounds", help='JSON 檔 {"param":[lo,hi],...} 搜索空間,啟用參數收斂硬判')
    a = ap.parse_args()

    p, r = load(a.target)
    n = len(r)
    checks = []  # (名稱, 通過?, 說明, 硬判?)

    for c in ("mxf", "txf"):
        pf = avg_pf(r, c)
        checks.append((f"{c.upper()} 平均OOS_PF>{PF_MIN}", pf > PF_MIN, f"{pf:.2f}", True))
        pr = pos_ratio(r, c)
        checks.append((f"{c.upper()} 正窗>={POS_MIN:.0%}", pr >= POS_MIN, f"{pr:.0%} ({n}窗)", True))

    agree = sum(1 for w in r if (w["mxf"].get("net") or 0) * (w["txf"].get("net") or 0) > 0) / n
    checks.append((f"跨合約同號>={AGREE_MIN:.0%}", agree >= AGREE_MIN, f"{agree:.0%}", True))

    if a.smoke:
        _, rs = load(a.smoke)
        for c in ("mxf", "txf"):
            f_pf, s_pf = avg_pf(r, c), avg_pf(rs, c)
            ok = s_pf <= 0 or f_pf >= s_pf * SMOKE_KEEP
            checks.append((f"{c.upper()} 5→60不崩(>= smoke*{SMOKE_KEEP})", ok,
                           f"smoke {s_pf:.2f} → final {f_pf:.2f}", True))

    ks = list(r[0]["best"].keys())
    bounds = json.load(open(a.bounds, encoding="utf-8")) if a.bounds else None
    for k in ks:
        vals = [w["best"][k] for w in r]
        rng = max(vals) - min(vals)
        if bounds and k in bounds:
            span = bounds[k][1] - bounds[k][0]
            ok = span <= 0 or rng <= span * BOUND_FRAC
            checks.append((f"參數收斂 {k}(range<=span*{BOUND_FRAC})", ok,
                           f"range {rng:.2f} / span {span:.2f}", True))
        else:
            checks.append((f"參數range {k}(無bounds,僅供人眼)", True, f"range {rng:.2f}", False))

    print(f"=== judge_wfo: {p.name} ({n} 窗) ===")
    hard_fail = False
    for name, ok, detail, hard in checks:
        tag = "PASS" if ok else ("FAIL" if hard else "WARN")
        if hard and not ok:
            hard_fail = True
        print(f"  [{tag}] {name}: {detail}")

    verdict = "FAIL" if hard_fail else "PASS"
    print(f"VERDICT: {verdict}")
    sys.exit(1 if hard_fail else 0)


if __name__ == "__main__":
    main()
