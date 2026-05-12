"""
TMF 長期回測腳本
從 Shioaji 拉取 2024-01-01 ~ 今日 1 分鐘 K 棒，
合併既有 CSV，以優化後策略回測，輸出 Markdown 報告。

用法：
    python scripts/backtest_longterm.py
    python scripts/backtest_longterm.py --start 2024-01-01 --balance 200000
"""

import sys
import os
import argparse
from pathlib import Path
from datetime import datetime, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from dotenv import load_dotenv
load_dotenv()


# ─────────────────────────────────────────────────────────
# 1. 從 Shioaji 抓資料
# ─────────────────────────────────────────────────────────

def fetch_kbars_range(start_date: datetime, end_date: datetime, instrument: str = "TMF"):
    """分批從 Shioaji 拉取指定區間的 1 分鐘 K 棒，回傳 list[dict]"""
    import shioaji as sj
    import pandas as pd

    api = sj.Shioaji()
    print(f"🔗 登入 Shioaji...")
    api.login(
        api_key=os.environ["SHIOAJI_API_KEY"],
        secret_key=os.environ["SHIOAJI_SECRET_KEY"],
        receive_window=300000,
    )

    # 使用連續近月合約 (MXFR1/TGFR1)，可查長期歷史
    if instrument == "TMF":
        contract = api.Contracts.Futures.MXF.MXFR1
    else:
        contract = api.Contracts.Futures.TGF.TGFR1
    print(f"📦 合約: {contract.code} (連續近月)")

    all_bars = []
    batch_days = 5
    current_end = end_date
    total_days = max((end_date - start_date).days, 1)
    fetched_days = 0
    zero_count = 0  # 連續空批次計數

    while current_end > start_date:
        batch_start = max(current_end - timedelta(days=batch_days), start_date)
        start_str = batch_start.strftime("%Y-%m-%d")
        end_str = current_end.strftime("%Y-%m-%d")

        try:
            kbars = api.kbars(contract=contract, start=start_str, end=end_str)
            if kbars and hasattr(kbars, 'Close') and len(kbars.Close) > 0:
                zero_count = 0
                for i in range(len(kbars.Close)):
                    raw_ts = kbars.ts[i]
                    if isinstance(raw_ts, (int, float)):
                        epoch_sec = raw_ts / 1e9 if raw_ts > 1e12 else raw_ts
                        # utcfromtimestamp：Shioaji epoch 已預移 +8h
                        from datetime import timezone
                        ts = datetime.fromtimestamp(epoch_sec, tz=timezone.utc).replace(tzinfo=None)
                    elif hasattr(raw_ts, 'to_pydatetime'):
                        ts = raw_ts.to_pydatetime().replace(tzinfo=None)
                    else:
                        ts = raw_ts
                    all_bars.append({
                        "datetime": ts,
                        "open":   float(kbars.Open[i]),
                        "high":   float(kbars.High[i]),
                        "low":    float(kbars.Low[i]),
                        "close":  float(kbars.Close[i]),
                        "volume": int(kbars.Volume[i]),
                    })
                count = len(kbars.Close)
            else:
                count = 0
                zero_count += 1
        except Exception as e:
            count = 0
            zero_count += 1
            print(f"    ⚠️  {start_str}~{end_str}: {e}")

        fetched_days += batch_days
        pct = min(fetched_days / total_days * 100, 100)
        print(f"  [{pct:5.1f}%] {start_str} ~ {end_str}  {count} bars", flush=True)

        # 連續 10 批都沒資料 → API 已達歷史上限，停止
        if zero_count >= 10:
            print(f"  ⚠️  連續 {zero_count} 批無資料，Shioaji 歷史上限到此，停止拉取")
            break

        current_end = batch_start - timedelta(days=1)

    api.logout()
    return all_bars


# ─────────────────────────────────────────────────────────
# 2. 合併 + 存 CSV
# ─────────────────────────────────────────────────────────

def merge_and_save(new_bars: list, existing_csv: Path, out_csv: Path) -> "pd.DataFrame":
    import pandas as pd

    frames = []
    if existing_csv and existing_csv.exists():
        existing_df = pd.read_csv(existing_csv, parse_dates=["datetime"])
        frames.append(existing_df)
        print(f"  ✅ 既有資料: {len(existing_df)} 根 ({existing_df['datetime'].iloc[0]} ~ {existing_df['datetime'].iloc[-1]})")

    if new_bars:
        new_df = pd.DataFrame(new_bars)
        frames.append(new_df)

    if not frames:
        raise ValueError("沒有任何 K 棒資料")

    df = (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates(subset=["datetime"])
        .sort_values("datetime")
        .reset_index(drop=True)
    )
    df.to_csv(out_csv, index=False)
    print(f"  💾 合併後: {len(df)} 根 ({df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]})")
    return df


# ─────────────────────────────────────────────────────────
# 3. 執行回測
# ─────────────────────────────────────────────────────────

def run_backtest(df, initial_balance: float, risk_profile: str = "balanced"):
    from core.logger import setup_logger
    setup_logger(console_level="ERROR")
    from backtest.engine import BacktestEngine
    from backtest.report import BacktestReport
    from strategy.momentum import AdaptiveMomentumStrategy

    engine = BacktestEngine(
        initial_balance=initial_balance,
        slippage=1,
        commission=18.0,
        instrument="TMF",
    )
    result = engine.run(df, AdaptiveMomentumStrategy(), risk_profile)
    report = BacktestReport(result)
    return result, report


# ─────────────────────────────────────────────────────────
# 4. 生成 Markdown 報告
# ─────────────────────────────────────────────────────────

def generate_markdown(result, report, data_range: str, total_bars: int,
                      initial_balance: float, out_path: Path):
    m = report.metrics
    trades = result.trades

    # 月度分析
    monthly = {}
    for t in trades:
        try:
            month = t["entry_time"][:7]  # "2024-03"
            monthly.setdefault(month, {"pnl": 0, "trades": 0, "wins": 0})
            monthly[month]["pnl"] += t["pnl"]
            monthly[month]["trades"] += 1
            if t["pnl"] > 0:
                monthly[month]["wins"] += 1
        except Exception:
            pass

    # 出場原因統計
    reasons = {}
    for t in trades:
        r = t.get("reason", "未知")
        # 簡化原因
        if "停損" in r or "stop" in r.lower():
            key = "硬停損"
        elif "追蹤" in r or "Chandelier" in r:
            key = "追蹤停利"
        elif "回吐" in r:
            key = "利潤回吐"
        elif "保本" in r:
            key = "保本停損"
        elif "時間" in r:
            key = "時間停損"
        elif "分段" in r or "TP" in r:
            key = "分段停利"
        elif "強制" in r or "收盤" in r:
            key = "收盤強制"
        else:
            key = "其他"
        reasons.setdefault(key, {"count": 0, "pnl": 0})
        reasons[key]["count"] += 1
        reasons[key]["pnl"] += t["pnl"]

    # 最佳/最差 10 筆
    sorted_trades = sorted(trades, key=lambda t: t["pnl"], reverse=True)
    top10 = sorted_trades[:10]
    bot10 = sorted_trades[-10:]

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = []
    lines.append(f"# TMF 微型台指期貨 — 長期回測報告")
    lines.append(f"")
    lines.append(f"> 生成時間：{now_str}  ")
    lines.append(f"> 策略：AdaptiveMomentumStrategy（優化 v1）  ")
    lines.append(f"> 資料區間：{data_range}  ")
    lines.append(f"> 總 K 棒數：{total_bars:,} 根（1 分鐘）  ")
    lines.append(f"")
    lines.append(f"---")
    lines.append(f"")

    # ── 績效摘要 ──
    lines.append(f"## 績效摘要")
    lines.append(f"")
    lines.append(f"| 指標 | 數值 |")
    lines.append(f"|------|------|")
    for k, v in m.items():
        if k.strip() in ("", "---"):
            continue
        lines.append(f"| {k} | {v} |")
    lines.append(f"")

    # ── 月度損益 ──
    lines.append(f"## 月度損益")
    lines.append(f"")
    lines.append(f"| 月份 | 損益（元） | 交易筆數 | 勝率 |")
    lines.append(f"|------|----------:|--------:|-----:|")
    total_month_pnl = 0
    for month in sorted(monthly.keys()):
        d = monthly[month]
        wr = d["wins"] / d["trades"] * 100 if d["trades"] > 0 else 0
        sign = "+" if d["pnl"] >= 0 else ""
        total_month_pnl += d["pnl"]
        lines.append(f"| {month} | {sign}{d['pnl']:,.0f} | {d['trades']} | {wr:.0f}% |")
    lines.append(f"")

    # ── 出場原因分析 ──
    lines.append(f"## 出場原因分析")
    lines.append(f"")
    lines.append(f"| 出場原因 | 次數 | 合計損益（元） | 平均損益（元） |")
    lines.append(f"|---------|----:|-------------:|-------------:|")
    for reason, d in sorted(reasons.items(), key=lambda x: -abs(x[1]["pnl"])):
        avg = d["pnl"] / d["count"] if d["count"] else 0
        sign = "+" if d["pnl"] >= 0 else ""
        avgsign = "+" if avg >= 0 else ""
        lines.append(f"| {reason} | {d['count']} | {sign}{d['pnl']:,.0f} | {avgsign}{avg:,.0f} |")
    lines.append(f"")

    # ── 最佳 10 筆 ──
    lines.append(f"## 最佳 10 筆交易")
    lines.append(f"")
    lines.append(f"| 進場時間 | 方向 | 進場 | 出場 | 損益（元） | 出場原因 |")
    lines.append(f"|---------|------|-----:|-----:|-----------:|--------|")
    for t in top10:
        side = "多" if t["side"] == "long" else "空"
        lines.append(f"| {t['entry_time'][:16]} | {side} | {t['entry_price']:.0f} | {t['exit_price']:.0f} | +{t['pnl']:,.0f} | {t['reason'][:25]} |")
    lines.append(f"")

    # ── 最差 10 筆 ──
    lines.append(f"## 最差 10 筆交易")
    lines.append(f"")
    lines.append(f"| 進場時間 | 方向 | 進場 | 出場 | 損益（元） | 出場原因 |")
    lines.append(f"|---------|------|-----:|-----:|-----------:|--------|")
    for t in bot10:
        side = "多" if t["side"] == "long" else "空"
        lines.append(f"| {t['entry_time'][:16]} | {side} | {t['entry_price']:.0f} | {t['exit_price']:.0f} | {t['pnl']:,.0f} | {t['reason'][:25]} |")
    lines.append(f"")

    # ── 策略參數 ──
    lines.append(f"## 策略參數（優化 v1）")
    lines.append(f"")
    lines.append(f"| 參數 | 值 | 說明 |")
    lines.append(f"|-----|---:|------|")
    lines.append(f"| min_signal_strength | 0.66 | 進場門檻（原 0.60） |")
    lines.append(f"| stop_loss_multiplier | 2.0× ATR | 停損倍數（原 2.5×） |")
    lines.append(f"| trailing_trigger | 1.0× ATR | 追蹤停利啟動（原 2.0×） |")
    lines.append(f"| 手續費 | 18 元/邊 | 含稅 |")
    lines.append(f"| 滑價 | 1 點 | 保守估計 |")
    lines.append(f"")

    lines.append(f"---")
    lines.append(f"*Generated by UltraTrader backtest_longterm.py*")

    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n  📄 MD 報告已儲存：{out_path}")


# ─────────────────────────────────────────────────────────
# 5. Main
# ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="TMF 長期回測（2024~今）")
    parser.add_argument("--start",   default="2024-01-01", help="資料起始日 (YYYY-MM-DD)")
    parser.add_argument("--end",     default=None,         help="資料結束日 (YYYY-MM-DD)，預設今日")
    parser.add_argument("--balance", type=float, default=200000.0, help="初始資金")
    parser.add_argument("--risk",    default="balanced",   help="風險等級")
    parser.add_argument("--no-fetch", action="store_true", help="跳過 API 拉取，直接用現有 CSV")
    args = parser.parse_args()

    start_dt = datetime.strptime(args.start, "%Y-%m-%d")
    end_dt   = datetime.strptime(args.end, "%Y-%m-%d") if args.end else datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    import pandas as pd
    data_dir = Path(__file__).parent.parent / "data" / "historical"
    data_dir.mkdir(parents=True, exist_ok=True)

    # 最新的既有 CSV
    existing_csvs = sorted(data_dir.glob("tmf_*_1m.csv"), reverse=True)
    existing_csv  = existing_csvs[0] if existing_csvs else None

    # 決定實際需要拉取的起訖
    if existing_csv and not args.no_fetch:
        existing_df = pd.read_csv(existing_csv, parse_dates=["datetime"])
        existing_start = existing_df["datetime"].iloc[0]
        # 只拉既有資料之前的部分
        fetch_end = existing_start - timedelta(days=1)
        fetch_start = start_dt
        if fetch_start >= fetch_end:
            print(f"  ℹ️  既有 CSV 已涵蓋 {start_dt.date()} 起，跳過拉取")
            args.no_fetch = True
        else:
            print(f"\n📥 需要補拉: {fetch_start.date()} ~ {fetch_end.date()}")
    else:
        fetch_start = start_dt
        fetch_end = end_dt

    # ── 拉資料 ──
    new_bars = []
    if not args.no_fetch:
        print(f"\n[1/3] 拉取 TMF 歷史 K 棒 {fetch_start.date()} ~ {fetch_end.date()}...")
        total_days = (fetch_end - fetch_start).days
        print(f"      共約 {total_days} 天，分 {(total_days // 5) + 1} 批次拉取\n")
        new_bars = fetch_kbars_range(fetch_start, fetch_end)
    else:
        print(f"\n[1/3] 跳過 API 拉取，使用既有 CSV")

    # ── 合併存檔 ──
    print(f"\n[2/3] 合併資料...")
    out_csv = data_dir / f"tmf_{end_dt.strftime('%Y%m%d')}_full_1m.csv"
    df = merge_and_save(new_bars, existing_csv, out_csv)

    # 篩選指定區間
    df = df[(df["datetime"] >= start_dt) & (df["datetime"] <= end_dt)].reset_index(drop=True)
    print(f"  📊 回測區間資料: {len(df)} 根 ({df['datetime'].iloc[0]} ~ {df['datetime'].iloc[-1]})")

    # ── 回測 ──
    print(f"\n[3/3] 執行回測（初始資金 {args.balance:,.0f} 元，風險: {args.risk}）...")
    result, report = run_backtest(df, args.balance, args.risk)
    report.print_report()

    # ── 輸出 MD ──
    data_range = f"{df['datetime'].iloc[0].strftime('%Y-%m-%d')} ~ {df['datetime'].iloc[-1].strftime('%Y-%m-%d')}"
    out_dir = Path(__file__).parent.parent / "data" / "backtest_results"
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"backtest_longterm_{end_dt.strftime('%Y%m%d')}.md"
    generate_markdown(result, report, data_range, len(df), args.balance, md_path)

    print(f"\n✅ 完成！")
    print(f"   資料檔: {out_csv}")
    print(f"   報告: {md_path}")


if __name__ == "__main__":
    main()
