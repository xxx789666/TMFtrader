"""v2 vol_mult 專項診斷：量能濾網到底有沒有把 PF 推過 1.2？
不靠 wf_score 選誰，直接比對每個 base 組合「不過濾(0.0) vs 各 vol_mult」的 test_pf。
讀 ultra-trader-src/data/donchian 的最新 4 個 grid CSV。"""
import pandas as pd
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data" / "donchian"
BASE = ["entry_n", "exit_k", "sl_atr", "max_bars", "cooldown"]

def newest(symbol, variant):
    fs = sorted(DATA.glob(f"grid_donchian_{symbol}_{variant}_*.csv"))
    return fs[-1] if fs else None

def analyze(path, label):
    df = pd.read_csv(path)
    elig = df[df["test_n"] >= 100].copy()
    print(f"\n{'='*72}\n{label}  ({path.name})  合格 {len(elig)}/{len(df)}")

    # 各 vol_mult 值的 test_pf 分佈
    print("\n-- 各 vol_mult 的 test_pf（合格組）--")
    g = elig.groupby("vol_mult")["test_pf"].agg(["count", "median", "max", "mean"])
    for vm, r in g.iterrows():
        flag = "  <- baseline(off)" if vm == 0.0 else ""
        print(f"  vol_mult={vm:<4}: n={int(r['count']):>2}  median={r['median']:.3f}  "
              f"max={r['max']:.3f}  mean={r['mean']:.3f}{flag}")

    # PF>1.2 有沒有出現、各 vol_mult 各幾組
    pass12 = elig[elig["test_pf"] > 1.2]
    print(f"\n-- PF>1.2: {len(pass12)} 組 --")
    if len(pass12):
        print(pass12.groupby("vol_mult").size().to_string())

    # 配對比較：固定 base 5 參數，看 vol_mult>0 相對 0.0 的 test_pf 變化
    print("\n-- 配對 Δ：同 base 下 best(vol_mult>0) - unfiltered(0.0) 的 test_pf --")
    deltas = []
    for keys, sub in elig.groupby(BASE):
        off = sub[sub["vol_mult"] == 0.0]["test_pf"]
        on = sub[sub["vol_mult"] > 0.0]["test_pf"]
        if len(off) and len(on):
            d = on.max() - off.iloc[0]
            deltas.append((d, keys, off.iloc[0], on.max()))
    if deltas:
        improved = [x for x in deltas if x[0] > 0]
        print(f"  base 組合可比對 {len(deltas)} 個；filtered 勝出 {len(improved)} 個")
        deltas.sort(reverse=True)
        print(f"  best Δ = {deltas[0][0]:+.3f}  (base={dict(zip(BASE, deltas[0][1]))})")
        print(f"           off_pf={deltas[0][2]:.3f} -> best_on_pf={deltas[0][3]:.3f}")
        print(f"  worst Δ = {deltas[-1][0]:+.3f}")
        import statistics
        print(f"  median Δ = {statistics.median(d[0] for d in deltas):+.3f}")

for sym, var in [("MXF", "longonly"), ("TXF", "longonly"),
                 ("MXF", "biside"), ("TXF", "biside")]:
    p = newest(sym, var)
    if p:
        analyze(p, f"{sym} {var}")
    else:
        print(f"\n(no grid for {sym} {var})")
