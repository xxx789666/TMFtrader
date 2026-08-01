"""maxpain precursor 穩健性追查(一次性):ret1/updays5/dist 訊號逐年是否穩定、大單依賴度。ASCII 輸出。"""
import pandas as pd
import statistics

df = pd.read_csv(r"D:\vps自動化交易每日籌碼分析報告\歷史回測\maxpain_precursors.csv")
df["year"] = df["signal_t"].str[:4]


def perf(g, label):
    if len(g) == 0:
        print(f"    {label:<28}: 0 trades")
        return
    w = g[g.pnl > 0]
    gl = -g[g.pnl < 0].pnl.sum()
    pf = g[g.pnl > 0].pnl.sum() / gl if gl > 0 else 99
    print(f"    {label:<28}: n={len(g):>3} WR={len(w)/len(g)*100:3.0f}% "
          f"avg={g.pnl.mean():>+9,.0f} PF={pf:4.2f} net={g.pnl.sum():>+10,.0f}")


print("=== A. ret1 (signal-day return) split at -0.8% / 0, by year ===")
for cond, lab in [(df.ret1 <= -0.8, "ret1<=-0.8 (big down day)"),
                  ((df.ret1 > -0.8) & (df.ret1 < 0), "-0.8<ret1<0"),
                  (df.ret1 >= 0, "ret1>=0 (flat/up day)")]:
    perf(df[cond], lab)
print("  -- ret1<=-0.8 by year --")
for y, g in df[df.ret1 <= -0.8].groupby("year"):
    perf(g, f"ret1<=-0.8 {y}")
print("  -- ret1>=0 by year --")
for y, g in df[df.ret1 >= 0].groupby("year"):
    perf(g, f"ret1>=0 {y}")

print("\n=== B. top-trade dependence of ret1<=-0.8 bucket ===")
g = df[df.ret1 <= -0.8].sort_values("pnl", ascending=False)
print("    top5 pnl:", [f"{v:+,.0f}({d})" for v, d in zip(g.pnl.head(5), g.signal_t.head(5))])
print(f"    net={g.pnl.sum():+,.0f}  net-ex-top3={g.pnl.iloc[3:].sum():+,.0f}")

print("\n=== C. updays5<=2 by year ===")
for y, gg in df[df.updays5 <= 2].groupby("year"):
    perf(gg, f"updays5<=2 {y}")

print("\n=== D. dist mid band 1.16~2.43 by year ===")
m = df[(df.dist >= 1.16) & (df.dist <= 2.43)]
for y, gg in m.groupby("year"):
    perf(gg, f"dist-mid {y}")

print("\n=== E. combo: ret1<0 AND dist 1.0~3.0 ===")
c = df[(df.ret1 < 0) & (df.dist >= 1.0) & (df.dist <= 3.0)]
perf(c, "combo")
for y, gg in c.groupby("year"):
    perf(gg, f"combo {y}")
inv = df[~df.index.isin(c.index)]
perf(inv, "rest (excluded)")

print("\n=== F. correlation sanity (point-biserial vs win, spearman vs pnl) ===")
for k in ["ret1", "ret5", "updays5", "vol5", "dd20", "flow1", "flow5", "z_flow", "z_netoi", "dist"]:
    sub = df[[k, "pnl"]].dropna()
    pb = sub[k].corr((sub.pnl > 0).astype(float))
    sp = sub[k].corr(sub.pnl, method="spearman")
    print(f"    {k:<10}: corr(win)={pb:+.3f}  spearman(pnl)={sp:+.3f}  n={len(sub)}")
