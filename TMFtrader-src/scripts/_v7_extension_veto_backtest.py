"""v7(BreakoutDualSlope)過度延伸 veto 回測 — 真實小台 MXF 2020-2026 日盤。

回應 2026-06-26 live:price 距 EMA200 ~8×ATR + adx 57 還做空 → 5 分鐘反彈停損 −13877。
測「加一道過度延伸閘」會少賠還是把好單也濾掉:
  A. 先把 baseline v7 的每筆交易依「進場時 |price−EMA200|/ATR(延伸度)」「adx」分桶,看延伸/高adx 桶是不是淨虧。
  B. 再實跑幾個 veto 變體(距 EMA200 K×ATR 上限 / adx 上限),比 PF/淨/筆數/DD vs baseline。
口徑同 backtest_breakout_mxf_real(MXF 50元/點、本金 625K、tmf_3x、intrabar 硬停損)。
"""
import sys
from pathlib import Path
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import warnings; warnings.filterwarnings("ignore")
import pandas as pd
from core.logger import setup_logger; setup_logger(console_level="CRITICAL")
from core.instrument_config import InstrumentSpec, INSTRUMENT_SPECS
INSTRUMENT_SPECS["MXF"] = InstrumentSpec(code="MXF", name="MXF", point_value=50.0, margin=56000,
    maintenance_margin=43000, commission=18.0, tax_rate_pct=0.00002, strategy_type="breakout",
    default_initial_price=20000.0)
from core.gpu_indicators import precompute_all
from backtest.fast_engine import FastBacktestEngine
from strategy.breakout_dualslope import BreakoutDualSlopeStrategy
from scripts.optimize_strategy import _calc_metrics

DATA = ROOT / "data" / "vwap_fade" / "MXF_day_5m.parquet"
INIT_BAL = 625_000.0


def make_strat(dist_k=None, adx_cap=None, ctx=None):
    """v7 + 可選 veto。ctx(dict)非 None 時記錄每筆進場 snapshot 脈絡(供 baseline 歸因)。"""
    s = BreakoutDualSlopeStrategy(state_path=None, max_loss_twd=20000.0)
    orig = s.on_kbar
    def hooked(kbar, snapshot, **kw):
        sig = orig(kbar, snapshot, **kw)
        if sig is None:
            return None
        atr = snapshot.atr if snapshot.atr > 0 else 1.0
        ext = abs(kbar.close - snapshot.ema200) / atr          # 延伸度(離 EMA200 幾個 ATR)
        if ctx is not None:
            ctx[kbar.datetime.isoformat()] = dict(ext=ext, adx=snapshot.adx,
                                                  side=sig.direction.value)
        if dist_k is not None and ext > dist_k:                # 過度延伸 → 不追
            return None
        if adx_cap is not None and snapshot.adx > adx_cap:     # adx 過高(climax)→ 不追
            return None
        return sig
    s.on_kbar = hooked
    return s


def run(df, dist_k=None, adx_cap=None, ctx=None):
    df = df.sort_values("datetime").reset_index(drop=True)
    res = FastBacktestEngine(initial_balance=INIT_BAL, instrument="MXF",
                             intrabar_hard_exits=True).run(
        df, precompute_all(df, verbose=False), make_strat(dist_k, adx_cap, ctx), "tmf_3x")
    tr = list(res.trades)
    m = _calc_metrics(res)
    m["net"] = sum(t["pnl"] for t in tr)
    return m, tr


def line(label, m):
    print(f"  {label:26} | {m['n']:>3} 筆 | WR {m['wr']:>5.1f}% | PF {m['pf']:>6.3f} | "
          f"Ret {m['ret']:>+7.2f}% | DD {m['dd']:>5.1f}% | 淨 {m['net']:>+12,.0f}")


def grp(items):
    """items: list of (bucket, pnl) → bucket: (n, net, wr, pf)。"""
    out = {}
    for b, p in items:
        out.setdefault(b, []).append(p)
    rows = {}
    for b, v in out.items():
        n = len(v); gp = sum(x for x in v if x > 0); gl = abs(sum(x for x in v if x <= 0))
        rows[b] = (n, sum(v), sum(1 for x in v if x > 0) / n * 100, gp / gl if gl else 999)
    return rows


def ext_bucket(e):
    for lo, hi in [(0, 2), (2, 3), (3, 4), (4, 5)]:
        if lo <= e < hi:
            return f"{lo}-{hi}xATR"
    return "5+xATR"


def adx_bucket(a):
    return "<40" if a < 40 else ("40-50" if a < 50 else ("50-55" if a < 55 else "55+"))


def main():
    df = pd.read_parquet(DATA); df["datetime"] = pd.to_datetime(df["datetime"])
    print("=" * 100)
    print("  v7(BreakoutDualSlope)過度延伸 veto 回測 | MXF 50元/點 | 本金 625K | tmf_3x | intrabar 硬停損")
    print(f"  資料 {df['datetime'].min()} ~ {df['datetime'].max()} | {df['datetime'].dt.date.nunique()} 交易日")
    print("=" * 100)

    # ---- baseline v7 + 歸因脈絡 ----
    ctx = {}
    base, tr = run(df, ctx=ctx)
    for t in tr:
        c = ctx.get(t["entry_time"], {})
        t["ext"] = c.get("ext"); t["adx"] = c.get("adx"); t["sidev"] = c.get("side")
    print("\n[Baseline v7(無 veto)]")
    line("全期 2020-2026", base)

    have = [t for t in tr if t.get("ext") is not None]
    print(f"\n[歸因:依 延伸度(進場時 |price−EMA200|/ATR)]  (n={len(have)} 可歸因)")
    for b in ["0-2xATR", "2-3xATR", "3-4xATR", "4-5xATR", "5+xATR"]:
        r = grp([(ext_bucket(t["ext"]), t["pnl"]) for t in have]).get(b)
        if r:
            print(f"  {b:10} | {r[0]:>3} 筆 | 淨 {r[1]:>+11,.0f} | WR {r[2]:>5.1f}% | PF {r[3]:>6.2f}")
    print("\n[歸因:依 進場 ADX]")
    for b in ["<40", "40-50", "50-55", "55+"]:
        r = grp([(adx_bucket(t["adx"]), t["pnl"]) for t in have]).get(b)
        if r:
            print(f"  {b:10} | {r[0]:>3} 筆 | 淨 {r[1]:>+11,.0f} | WR {r[2]:>5.1f}% | PF {r[3]:>6.2f}")
    print("\n[歸因:延伸 ≥4xATR 的空單(今天那種) vs 其餘]")
    for b, sel in [("空·延伸≥4", lambda t: t["sidev"] == "SELL" and t["ext"] >= 4),
                   ("空·延伸<4", lambda t: t["sidev"] == "SELL" and t["ext"] < 4),
                   ("多·全部", lambda t: t["sidev"] == "BUY")]:
        v = [t["pnl"] for t in have if sel(t)]
        if v:
            gp = sum(x for x in v if x > 0); gl = abs(sum(x for x in v if x <= 0))
            print(f"  {b:10} | {len(v):>3} 筆 | 淨 {sum(v):>+11,.0f} | "
                  f"WR {sum(1 for x in v if x>0)/len(v)*100:>5.1f}% | PF {gp/gl if gl else 999:>6.2f}")

    # ---- veto 變體 ----
    print("\n[Veto 變體 vs baseline(全期)]")
    line("baseline(無 veto)", base)
    for dk in [5, 4, 3]:
        m, _ = run(df, dist_k=dk)
        line(f"距EMA200 ≤{dk}xATR", m)
    for ac in [55, 50]:
        m, _ = run(df, adx_cap=ac)
        line(f"adx ≤{ac}", m)
    for dk, ac in [(4, 55), (4, 50)]:
        m, _ = run(df, dist_k=dk, adx_cap=ac)
        line(f"距≤{dk}xATR + adx≤{ac}", m)

    # ---- 逐年(baseline vs 一個代表 veto)----
    print("\n[逐年:baseline vs 距≤4xATR]")
    for yr in sorted(df["datetime"].dt.year.unique()):
        sub = df[df["datetime"].dt.year == yr]
        if len(sub) < 200:
            continue
        b, _ = run(sub); v, _ = run(sub, dist_k=4)
        print(f"  {yr} | base: n{b['n']:>3} PF{b['pf']:>5.2f} 淨{b['net']:>+10,.0f}"
              f"   |  距≤4xATR: n{v['n']:>3} PF{v['pf']:>5.2f} 淨{v['net']:>+10,.0f}")


if __name__ == "__main__":
    main()
