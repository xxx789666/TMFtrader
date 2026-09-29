"""引擎忠實回放 —— 直接驅動 live core/engine.py 的 TradingEngine。

目的:盡可能貼近 live 行為(非逐筆一致,fill 為近似)。重現:
- live 的 IndicatorEngine(非回測 gpu_indicators)
- live 的 TickAggregator 5分 bar 建構(把 1分資料合成 tick 餵進去 → 同一套右緣 wall-clock 切法)
- live 的 _process_kbar 決策路徑 + SessionManager(24h 日盤+夜盤)
- live 的 RiskManager(tmf_3x 動態口數)
mock 掉:TG、position_lock、data_collector、reconcile、anomaly。
fill 近似:paper 路徑在 snapshot.price(5分bar收盤)成交 + tick 層硬停損用觸價。

用法:
  python scripts/replay_live_engine.py --start 2026-05-28 --end 2026-05-28
  python scripts/replay_live_engine.py --start 2024-07-29 --end 2026-05-29 --balance 125000
"""
import os, sys, argparse
from pathlib import Path
from datetime import datetime, date

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import warnings; warnings.filterwarnings("ignore")
import pandas as pd

# ── 回放時鐘(取代 datetime.now())───────────────────────────────────
class _Clock:
    now_dt = datetime(2024, 1, 1)
_CLOCK = _Clock()

class FakeDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return _CLOCK.now_dt

def _install_fake_clock():
    """把所有已載入的專案模組裡 `datetime` 名稱換成 FakeDatetime(統一回放時鐘)。"""
    for name, mod in list(sys.modules.items()):
        if not name.startswith(("core", "strategy", "risk")):
            continue
        if getattr(mod, "datetime", None) is datetime:
            try:
                mod.datetime = FakeDatetime
            except Exception:
                pass


def build_1min(start: date, end: date, session: str) -> pd.DataFrame:
    """從 TMFR1 月檔組 1分K。session: 'day'(08:45-13:45) / '24h'(全時段)。"""
    from glob import glob
    files = sorted(glob(str(ROOT / "data" / "history" / "TMFR1_1min_*.parquet")))
    parts = []
    for f in files:
        d = pd.read_parquet(f); d["ts"] = pd.to_datetime(d["ts"])
        parts.append(d)
    df = pd.concat(parts).rename(columns={"Open": "open", "High": "high", "Low": "low",
                                          "Close": "close", "Volume": "volume"})
    df = df[["ts", "open", "high", "low", "close", "volume"]].sort_values("ts")
    df = df[(df["ts"].dt.date >= start) & (df["ts"].dt.date <= end)]
    if session == "day":
        t = df["ts"].dt.time
        df = df[(t >= pd.Timestamp("08:45").time()) & (t < pd.Timestamp("13:45").time())]
    return df.reset_index(drop=True)


def synth_ticks(bar):
    """1分 bar -> (O,H,L,C) 4 個 tick;依紅/黑K決定先觸高或先觸低。"""
    ts = bar.ts.to_pydatetime()
    o, h, l, c = float(bar.open), float(bar.high), float(bar.low), float(bar.close)
    seq = [(o, ts.replace(second=1))]
    if c >= o:   # 紅K:先低後高
        seq += [(l, ts.replace(second=20)), (h, ts.replace(second=40))]
    else:        # 黑K:先高後低
        seq += [(h, ts.replace(second=20)), (l, ts.replace(second=40))]
    seq += [(c, ts.replace(second=58))]
    v = max(int(bar.volume), 1)
    return [(p, t, v) for p, t in seq]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument("--session", default="24h", choices=["day", "24h"])
    ap.add_argument("--balance", type=float, default=222890.0)
    ap.add_argument("--strategy", default="", help="STRATEGY_TYPE 覆寫(如 night_v7);空=用 spec 預設")
    ap.add_argument("--tf", default="5", help="TIMEFRAME(night_v7=30)")
    ap.add_argument("--ticks-glob", default="", help="history 之後接真 tick CSV(如 data/ticks/TMF_2026060*.csv)")
    args = ap.parse_args()

    # 必須在 import engine 前設好環境
    os.environ["TRADING_MODE"] = "simulation"   # → MockBroker、不登入 shioaji
    os.environ["INSTRUMENTS"] = "TMF"
    os.environ["CONTRACT_CODE"] = "TMF"
    os.environ["RISK_PROFILE"] = "tmf_3x"
    os.environ["TIMEFRAME"] = str(args.tf)
    os.environ["INITIAL_BALANCE"] = str(int(args.balance))
    os.environ["RECORD_TICKS"] = "0"
    if args.strategy:
        os.environ["STRATEGY_TYPE"] = args.strategy

    from core.logger import setup_logger; setup_logger(console_level="CRITICAL")
    import core.engine as engmod
    from core.engine import TradingEngine, EngineState
    from core.market_data import Tick
    from core.position import Side
    _install_fake_clock()

    eng = TradingEngine()
    ok = eng.initialize()
    if ok is False:
        print("initialize failed"); sys.exit(1)
    _install_fake_clock()   # initialize 載入 strategy/risk 後再統一換時鐘

    # ── mock 副作用 ───────────────────────────────────────────────
    engmod.notify_entry = lambda *a, **k: None
    engmod.notify_exit = lambda *a, **k: None
    class _NoLock:
        def is_blocked(self, *a, **k): return None
        def acquire(self, *a, **k): return None
        def release(self, *a, **k): return None
    engmod.position_lock = _NoLock()
    eng.data_collector = None
    eng._check_price_anomaly = lambda *a, **k: None
    eng._reconcile_positions = lambda *a, **k: None
    eng._heartbeat = lambda *a, **k: None
    eng._broadcast = lambda *a, **k: None

    # 翻成 paper 路徑:_execute_entry/_exit 走 paper 分支(snapshot.price 成交、無真實下單)
    eng.trading_mode = "paper"
    for pipe in eng.pipelines.values():
        pipe.aggregator.use_wall_clock = False   # 回放用 tick.datetime,不用系統時間

    # _on_kbar_complete 改同步呼叫 _process_kbar(避免 thread/queue)
    def _sync_kbar(instrument, kbar):
        eng._process_kbar(instrument, kbar)
    eng._on_kbar_complete = _sync_kbar
    for code, pipe in eng.pipelines.items():
        pipe.aggregator._callbacks[eng.timeframe] = [lambda kbar, inst=code: _sync_kbar(inst, kbar)]

    eng.state = EngineState.RUNNING

    # 抓成交:hook position_manager 的開/平倉
    pm = eng.position_manager
    trades = []
    _orig_close = pm.close_position
    def _close_hook(instrument, price, reason, ts=None, quantity=0):
        tr = _orig_close(instrument, price, reason, ts or _CLOCK.now_dt, quantity)
        if tr:
            trades.append(tr)
        return tr
    pm.close_position = _close_hook

    # ── 餵資料 ────────────────────────────────────────────────────
    df = build_1min(date.fromisoformat(args.start), date.fromisoformat(args.end), args.session)
    print(f"資料 {args.session}: {len(df)} 根 1分K | {df['ts'].min()} ~ {df['ts'].max()}")
    inst = "TMF"
    for bar in df.itertuples(index=False):
        for price, ts, vol in synth_ticks(bar):
            _CLOCK.now_dt = ts
            # tick 層硬停損(複製 _on_tick is_urgent:觸價立即平倉)
            pos = pm.positions.get(inst)
            if pos and not pos.is_flat and pos.stop_loss > 0:
                if (pos.side == Side.LONG and price <= pos.stop_loss) or \
                   (pos.side == Side.SHORT and price >= pos.stop_loss):
                    eng._execute_exit(inst, _mk_hardstop_signal(pos), pos.stop_loss)
                    continue
            eng.pipelines[inst].aggregator.on_tick(Tick(datetime=ts, price=price, volume=vol, instrument=inst))

    # ── 接真 tick(06-01+,比 synth-from-1min 更貼近 live)──────────
    if args.ticks_glob:
        from glob import glob as _glob
        tfiles = sorted(_glob(str(ROOT / args.ticks_glob)))
        tk = pd.concat([pd.read_csv(f) for f in tfiles], ignore_index=True)
        tk["ts"] = pd.to_datetime(tk["ts"]); tk = tk.sort_values("ts")
        print(f"真 tick: {len(tk)} 筆 | {tk['ts'].min()} ~ {tk['ts'].max()}")
        for row in tk.itertuples(index=False):
            ts = row.ts.to_pydatetime(); price = float(row.price); vol = max(int(row.volume), 1)
            _CLOCK.now_dt = ts
            pos = pm.positions.get(inst)
            if pos and not pos.is_flat and pos.stop_loss > 0:
                if (pos.side == Side.LONG and price <= pos.stop_loss) or \
                   (pos.side == Side.SHORT and price >= pos.stop_loss):
                    eng._execute_exit(inst, _mk_hardstop_signal(pos), pos.stop_loss)
                    continue
            eng.pipelines[inst].aggregator.on_tick(Tick(datetime=ts, price=price, volume=vol, instrument=inst))

    # ── 報告 ──────────────────────────────────────────────────────
    _report(trades, args)


def _mk_hardstop_signal(pos):
    from strategy.base import Signal, SignalDirection
    return Signal(direction=SignalDirection.CLOSE, strength=1.0,
                  stop_loss=0.0, take_profit=0.0, reason="硬停損(tick觸價)")


def _metrics(pnls):
    n = len(pnls)
    if n == 0:
        return dict(n=0, wr=0, pf=0, net=0, avg_w=0, avg_l=0)
    wins = [p for p in pnls if p > 0]; losses = [p for p in pnls if p <= 0]
    gp = sum(wins); gl = abs(sum(losses)) or 1e-9
    return dict(n=n, wr=len(wins)/n*100, pf=gp/gl, net=sum(pnls),
                avg_w=(gp/len(wins) if wins else 0), avg_l=(sum(losses)/len(losses) if losses else 0))


def _report(trades, args):
    import json
    if not trades:
        print("\n零成交"); return
    recs = []
    for t in trades:
        side = t.side.value if hasattr(t.side, "value") else t.side
        recs.append(dict(
            entry_time=t.entry_time.isoformat() if hasattr(t.entry_time, "isoformat") else str(t.entry_time),
            exit_time=t.exit_time.isoformat() if hasattr(t.exit_time, "isoformat") else str(t.exit_time),
            side=side, entry_price=float(t.entry_price), exit_price=float(t.exit_price),
            quantity=int(getattr(t, "quantity", 0)), pnl=float(getattr(t, "net_pnl", 0)),
            reason=getattr(t, "reason", ""),
            sess=("夜盤" if (t.entry_time.hour >= 15 or t.entry_time.hour < 8) else "日盤"),
            yr=t.entry_time.year))

    m = _metrics([r["pnl"] for r in recs])
    print(f"\n##### 引擎忠實回放 24h | {args.session} | tmf_3x | 本金 {args.balance:,.0f} #####")
    print(f"總計 {m['n']} 筆 | WR {m['wr']:.1f}% | PF {m['pf']:.3f} | 淨 {m['net']:+,.0f} | 均盈 {m['avg_w']:+,.0f} 均虧 {m['avg_l']:+,.0f}")
    # 日盤/夜盤拆
    for s in ("日盤", "夜盤"):
        ms = _metrics([r["pnl"] for r in recs if r["sess"] == s])
        print(f"  {s}: {ms['n']:>3} 筆 | WR {ms['wr']:>5.1f}% | PF {ms['pf']:>6.3f} | 淨 {ms['net']:>+10,.0f}")
    # 逐年
    print("  逐年:")
    for yr in sorted(set(r["yr"] for r in recs)):
        my = _metrics([r["pnl"] for r in recs if r["yr"] == yr])
        print(f"    {yr}: {my['n']:>3} 筆 | WR {my['wr']:>5.1f}% | PF {my['pf']:>6.3f} | 淨 {my['net']:>+10,.0f}")
    out = ROOT / "data" / f"replay_live_engine_{args.session}_{args.start}_{args.end}.json"
    out.write_text(json.dumps(recs, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"逐筆已存: {out.name}")


if __name__ == "__main__":
    main()
