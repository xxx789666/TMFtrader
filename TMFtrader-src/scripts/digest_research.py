"""把所有 WFO OOS 驗證產物(data/wfo_oos_*.json)濃縮成一張「策略研究體檢表」。

供 strategy-edge-research skill 在「讀歷史經驗」那步快速消化:每支策略印
8 維診斷 —— edge / 命中率 / 跨合約一致 / 參數穩定 / 最差窗 / regime 偏。
用法:python scripts/digest_research.py
"""
import sys, json, glob
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
import numpy as np


def digest(path):
    r = json.load(open(path, encoding="utf-8"))
    if not r:
        return None
    label = Path(path).stem.replace("wfo_oos_", "")
    out = {"label": label, "n_windows": len(r)}
    for c in ("mxf", "txf"):
        nets = [w[c].get("net") or 0 for w in r]
        pfs = [w[c]["pf"] for w in r if w[c].get("pf")]
        out[c] = dict(net=round(sum(nets)), pos=f"{sum(1 for x in nets if x>0)}/{len(nets)}",
                      pf=round(float(np.mean(pfs)), 2) if pfs else 0, worst=round(min(nets)) if nets else 0)
    # 參數穩定性(每窗最佳參數的 std / range,越大越過擬合)
    ks = list(r[0]["best"].keys())
    stab = {}
    for k in ks:
        vals = [w["best"][k] for w in r]
        rng = max(vals) - min(vals)
        stab[k] = round(rng, 2)
    out["param_range"] = stab
    # 跨合約一致:逐窗 MXF/TXF 同號比例
    agree = sum(1 for w in r if (w["mxf"].get("net") or 0) * (w["txf"].get("net") or 0) > 0)
    out["cross_contract_agree"] = f"{agree}/{len(r)}"
    return out


def main():
    files = sorted(glob.glob(str(ROOT / "data" / "wfo_oos_*.json")))
    if not files:
        print("(無 wfo_oos_*.json — 尚無驗證產物)"); return
    print("=" * 88)
    print("  策略研究體檢表(所有 WFO OOS 產物)")
    print("=" * 88)
    for f in files:
        d = digest(f)
        if not d:
            continue
        print(f"\n### {d['label']}  ({d['n_windows']} 窗)")
        for c in ("mxf", "txf"):
            x = d[c]
            verdict = "有 edge?" if x["pf"] > 1.05 and x["net"] > 0 else ("打平" if 0.95 <= x["pf"] <= 1.05 else "無 edge")
            print(f"  {c.upper()}: 淨 {x['net']:>+10,} | 正窗 {x['pos']} | 平均OOS_PF {x['pf']} | 最差窗 {x['worst']:>+10,} → {verdict}")
        print(f"  跨合約同號: {d['cross_contract_agree']}  (越高越穩;一半左右=背離=脆弱)")
        wide = [k for k, v in d["param_range"].items() if v > 0]
        print(f"  參數每窗range(越大越過擬合): " + " ".join(f"{k.split('_')[0]}:{v}" for k, v in d["param_range"].items()))
    print("\n" + "=" * 88)
    print("  判讀:PF~1 + 正窗約半數 + 跨合約背離 + 參數range大 = 過擬合/無 edge。要過關需 PF>1.1、正窗>=7/10、跨合約>=7/10、參數range收斂。")


if __name__ == "__main__":
    main()
