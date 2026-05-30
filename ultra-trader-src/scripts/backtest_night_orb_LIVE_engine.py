"""忠實回測 live 夜盤 ORB —— 直接驅動 night_orb.py 的 NightORBEngine 本體。
不是用 strategy/orb.py(那套有量能過濾、是非 live 路徑)。
- 真實 B2 ML 模型(orb_filter_b2.pkl) + 無量能過濾 + trail 1.25 + 1筆/夜 + 22:15進場
- sizer=tmf_3x、INITIAL_BALANCE=95000(對齊 live night_orb.py)
- 餵 TMF 全天 5m bar 進 engine.on_bar()，讀它寫的 paper CSV 算績效
mock 掉 tg_night / position_lock(回測無 TG、無跨策略鎖)。
"""
import os, sys
os.environ.setdefault("TRADING_MODE", "paper")   # 確保 _is_live()=False
os.environ["INITIAL_BALANCE"] = "95000"           # 對齊 live night_orb.py sizing
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
# B2 模型 pickle 內含 optimizer.ml.models.BreakoutFilterModel；optimizer 套件只在冷備份。
# 加到 sys.path 尾端 → optimizer 可解析、但 core/strategy 仍用現行 tree(models.py 不 import 它們)。
_COLD = Path(r"C:\Users\xx\Desktop\永豐-自動化交易\ultra-trader-src")
if _COLD.exists():
    sys.path.append(str(_COLD))
import warnings; warnings.filterwarnings("ignore")
import pandas as pd, numpy as np
from core.logger import setup_logger; setup_logger(console_level="CRITICAL")

import scripts.night_orb as N

# ── mock 副作用 ───────────────────────────────────────────────
N.tg_night = lambda *a, **k: None
class _NoLock:
    def is_blocked(self, *a, **k): return None
    def acquire(self, *a, **k): return None
    def release(self, *a, **k): return None
N.position_lock = _NoLock()

from risk.position_sizing import PositionSizer

# 本機 model 是 BreakoutFilterModel(predict_proba 回 1-D 正類機率)、
# night_orb.py 的 [0,1] 是為 raw sklearn 2-D 寫的 → 版本不匹配。
# patch predict 同時相容 1-D/2-D，決策語意不變(prob >= threshold 放行)。
def _ml_predict(self, feat_dict):
    if not self.enabled:
        return True, 1.0
    if not feat_dict:
        return False, 0.0
    row = {f: feat_dict.get(f, 0.0) for f in self.features}
    X = pd.DataFrame([row])[self.features]
    pp = np.asarray(self.model.predict_proba(X)).ravel()
    prob = float(pp[1]) if pp.size >= 2 else float(pp[0])
    return (prob >= self.threshold), prob
N.OrbMLFilter.predict = _ml_predict

def run_once(df, trail_dist):
    N.TRAIL_DIST_ATR = trail_dist          # 覆蓋 trail 距離(1.25=live現況, 0.3=engine.py修正值)
    ml = N.OrbMLFilter(threshold=0.40, enabled=True)
    paper = N.PaperLogger()
    if paper.path.exists():
        paper.path.unlink()
    paper._write_header()
    sizer = PositionSizer("tmf_3x")
    eng = N.NightORBEngine(ml, paper, order_mgr=None, sizer=sizer)
    for r in df.itertuples(index=False):
        eng.on_bar({"time": r.datetime.to_pydatetime(), "open": float(r.open),
                    "high": float(r.high), "low": float(r.low),
                    "close": float(r.close), "volume": int(r.volume)})
    return pd.read_csv(paper.path)

def main():
    data_file = sys.argv[1] if len(sys.argv) > 1 else "TMF_full_5m.parquet"
    full = ROOT / "data" / "vwap_fade" / data_file
    print(f"  資料: {data_file}")
    df = pd.read_parquet(full)
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)
    print(f"  餵 {len(df):,} 根 TMF 全天 5m bar；對比 trail_dist 1.25(live) vs 0.3(engine修正)")

    for trail in [1.25, 0.30]:
        t = run_once(df, trail)
        _report(t, trail)

def _report(t, trail):
    if len(t) == 0:
        print(f"\n  trail_dist={trail}: 零交易"); return
    t["exit_time"] = pd.to_datetime(t["exit_time"])
    t["yr"] = t["exit_time"].dt.year

    def metrics(sub, label):
        n = len(sub)
        if n == 0:
            print(f"  {label:10} | 0 筆"); return
        net = sub["net_pnl"].sum()
        wins = sub[sub["net_pnl"] > 0]["net_pnl"]
        losses = sub[sub["net_pnl"] <= 0]["net_pnl"]
        wr = len(wins) / n * 100
        pf = wins.sum() / abs(losses.sum()) if losses.sum() != 0 else float("inf")
        qty = sub["quantity"].value_counts().to_dict()
        qs = " ".join(f"{int(k)}口x{v}" for k, v in sorted(qty.items()))
        print(f"  {label:10} | 筆數 {n:>3} | {qs:14} | WR {wr:>5.1f}% | PF {pf:>6.3f} | "
              f"淨損益 {net:>+10,.0f}")

    tag = "live現況" if abs(trail - 1.25) < 0.01 else "engine.py修正"
    print(f"\n##### live 夜盤 ORB 忠實回測  trail_dist={trail} ({tag})  ML on / tmf_3x / 95K #####")
    print(f"  期間 {t['exit_time'].min().date()} ~ {t['exit_time'].max().date()}")
    print("  " + "-" * 78)
    for yr in sorted(t["yr"].unique()):
        metrics(t[t["yr"] == yr], str(yr))
    metrics(t, "全期合計")
    # 出場原因分佈
    print("\n  出場原因分佈:", t["exit_reason"].value_counts().to_dict())

if __name__ == "__main__":
    main()
