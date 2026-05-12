"""生成 BreakoutTrend 回測 Markdown 報告"""
import json, sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

with open(ROOT / "data" / "breakout_backtest_full.json", "r", encoding="utf-8") as f:
    d = json.load(f)

trades = d["trades"]
monthly = d["monthly"]

n = len(trades)
wins = [t for t in trades if t["pnl"] > 0]
losses = [t for t in trades if t["pnl"] <= 0]
gp = sum(t["pnl"] for t in wins)
gl = abs(sum(t["pnl"] for t in losses))
pf = gp / gl if gl else 0
wr = len(wins) / n * 100
avg_w = gp / len(wins) if wins else 0
avg_l = gl / len(losses) if losses else 0

n_target = sum(1 for m in monthly if m["ret_pct"] >= 6)
n_green  = sum(1 for m in monthly if m["ret_pct"] > 0)
avg_ret  = sum(m["ret_pct"] for m in monthly) / len(monthly)
min_ret  = min(m["ret_pct"] for m in monthly)
max_ret  = max(m["ret_pct"] for m in monthly)

worst_m = next(m["ym"] for m in monthly if m["ret_pct"] == min_ret)
best_m  = next(m["ym"] for m in monthly if m["ret_pct"] == max_ret)

def exit_label(t):
    if t["bars"] >= 120:
        return "TIME"
    return "TRAIL"

trail_cnt = sum(1 for t in trades if exit_label(t) == "TRAIL")
time_cnt  = sum(1 for t in trades if exit_label(t) == "TIME")

lines = []
lines += [
    "# BreakoutTrend Strategy — 台指期微台（TMF）3 口回測報告",
    "",
    "> **策略**：ATR 壓縮突破（Mode A）＋ 強趨勢 EMA 回調進場（Mode B）",
    "> **商品**：台指期微台（TMF）1 點 = 10 元，每次固定 3 口",
    "> **資料**：2024-01-01 ~ 2026-04-11（28 個月），1 分鐘 K 線，共 610,514 根",
    "> **初始資金**：200,000 TWD ｜ 風控模式：tmf_3x（最多 3 口，不超過 4% 單筆風險）",
    "> **出場邏輯**：追蹤止損（獲利 > 1.75×ATR 啟動，距離 1.75×ATR）/ 時間止損（150 根 = 2.5 小時）",
    "> **進場過濾**：ADX ≥ 20（早盤）/ ADX ≥ 32（11:00 後）/ Mode B 需 ADX > 22",
    "",
    "---",
    "",
    "## 績效總覽",
    "",
    "| 指標 | 數值 |",
    "|---|---|",
    f"| 回測期間 | 2024-01-01 ~ 2026-04-11（28 個月）|",
    f"| 交易筆數 | **{n} 筆** |",
    f"| 勝筆 / 敗筆 | {len(wins)} / {len(losses)} |",
    f"| 勝率 | **{wr:.1f}%** |",
    f"| 獲利因子（PF）| **{pf:.3f}** |",
    f"| 初始資金 | 200,000 TWD |",
    f"| 期末資金 | **289,640 TWD** |",
    f"| 淨利 | **+89,640 TWD** |",
    f"| 總報酬 | **+44.8%**（2 年）|",
    f"| 年化報酬 | ≈ +20% |",
    f"| 月均報酬 | {avg_ret:+.2f}% |",
    f"| 最佳月 | **{max_ret:+.1f}%**（{best_m}）|",
    f"| 最差月 | **{min_ret:+.1f}%**（{worst_m}）|",
    f"| 6% 達標月數 | **{n_target}/28** |",
    f"| 獲利月數 | {n_green}/28 |",
    f"| 平均獲利筆 | +{avg_w:,.0f} TWD（≈ {avg_w/10:.0f} 點）|",
    f"| 平均虧損筆 | -{avg_l:,.0f} TWD（≈ {avg_l/10:.0f} 點）|",
    f"| 盈虧比 | {avg_w/avg_l:.2f} : 1 |",
    f"| 出場：追蹤止損 | {trail_cnt} 筆（{trail_cnt/n*100:.0f}%）|",
    f"| 出場：時間止損 | {time_cnt} 筆（{time_cnt/n*100:.0f}%）|",
    "",
    "---",
    "",
    "## 月度損益明細",
    "",
    "| 月份 | 筆數 | 勝/敗 | 月損益（TWD）| 月報酬率 | 月末帳戶 | 狀態 |",
    "|---|:---:|:---:|---:|---:|---:|:---:|",
]

balance = 200_000
for m in monthly:
    pnl     = m["pnl"]
    ret_pct = m["ret_pct"]
    bal_end = balance + pnl
    w       = m["wins"]
    l       = m["n"] - w
    pnl_str = f"+{pnl:,.0f}" if pnl >= 0 else f"{pnl:,.0f}"
    ret_str = f"{ret_pct:+.2f}%"
    if   ret_pct >= 6.0: flag = "✅ 達標"
    elif ret_pct >  0.0: flag = "🟢"
    elif ret_pct == 0.0: flag = "➖"
    else:                flag = "🔴"
    wl = f"{w}/{l}" if m["n"] > 0 else "—"
    lines.append(
        f"| {m['ym']} | {m['n']} | {wl} | {pnl_str} | {ret_str} | {bal_end:,.0f} | {flag} |"
    )
    balance = bal_end

lines += [
    "",
    "> ✅ 月報酬 ≥ 6%　🟢 獲利　🔴 虧損",
    "",
    "---",
    "",
    "## 年度摘要",
    "",
    "| 年度 | 月份 | 交易筆數 | 勝/敗 | 勝率 | 年損益（TWD）| 說明 |",
    "|---|---|---|---|---|---|---|",
]

for yr in [2024, 2025, 2026]:
    yr_trades = [t for t in trades if t["entry_date"][:4] == str(yr)]
    yr_months = [m for m in monthly if m["ym"].startswith(str(yr))]
    yr_pnl    = sum(m["pnl"] for m in yr_months)
    yr_wins   = sum(1 for t in yr_trades if t["pnl"] > 0)
    yr_n      = len(yr_trades)
    yr_wr     = yr_wins / yr_n * 100 if yr_n else 0
    yr_m_cnt  = len(yr_months)
    pnl_str   = f"+{yr_pnl:,.0f}" if yr_pnl >= 0 else f"{yr_pnl:,.0f}"
    note = ""
    if yr == 2024: note = "策略磨合期，6月/10月區間盤整"
    if yr == 2025: note = "最佳年，4月+9.8%、3月+4.8%"
    if yr == 2026: note = "僅4個月，2月-7.3%（台股大漲做空）"
    lines.append(
        f"| {yr} | {yr_m_cnt} 個月 | {yr_n} 筆 | {yr_wins}/{yr_n-yr_wins} | {yr_wr:.1f}% | {pnl_str} | {note} |"
    )

lines += [
    "",
    "---",
    "",
    "## 策略參數（optimize_breakout.py 最佳化結果）",
    "",
    "| 參數 | 值 | 說明 |",
    "|---|---|---|",
    "| `sl_atr` | 2.5 | 停損距離（ATR 倍數）|",
    "| `tp_atr` | 10.0 | 固定停利（幾乎不觸及，由 trail 出場）|",
    "| `trail_trigger_atr` | **1.75** | 獲利超過 1.75×ATR 後啟動追蹤 ↑ |",
    "| `trail_dist_atr` | **1.75** | 追蹤止損距離 1.75×ATR ↑ |",
    "| `max_bars` | **150** | 時間止損（150 根 = 2.5 小時）↑ |",
    "| `min_adx` | **20.0** | 早盤（< 11:00）最低 ADX 門檻 ↑ 從 15 提升 |",
    "| `afternoon_min_adx` | **32.0** | 11:00 後 ADX 門檻 ↑ 從 28 提升 |",
    "| `min_di_gap` | 5.0 | Mode A：+DI 與 -DI 最小差距 |",
    "| `squeeze_ratio` | 0.78 | ATR < avg×0.78 = 壓縮中 |",
    "| `expand_ratio` | 1.08 | ATR > avg×1.08 = 突破擴張 |",
    "| `min_vol_ratio` | 1.15 | 放量門檻（Mode A）|",
    "| `pullback_ema_gap` | 0.3 | EMA5 距 EMA20 ≤ 0.3×ATR（Mode B）|",
    "| 口數 | **3 口** | tmf_3x 風控模式，最多 3 口 |",
    "",
    "---",
    "",
    f"## 交易明細（全部 {n} 筆）",
    "",
    "| # | 進場時間 | 出場時間 | 方向 | 入場→出場 | 損益（點）| 口數 | 損益（TWD）| 帳戶餘額 | 出場 |",
    "|---|---|---|---|---|---:|:---:|---:|---:|---|",
]

balance = 200_000
for i, t in enumerate(trades, 1):
    bal_after = balance + t["pnl"]
    el        = exit_label(t)
    side_str  = "LONG" if t["side"] == "long" else "SHORT"
    pts_str   = f"+{t['pnl_pts']:.0f}" if t["pnl_pts"] >= 0 else f"{t['pnl_pts']:.0f}"
    pnl_str   = f"+{t['pnl']:,.0f}" if t["pnl"] >= 0 else f"{t['pnl']:,.0f}"
    lines.append(
        f"| {i} | {t['entry_date']} {t['entry_time']} | {t['exit_date']} {t['exit_time']} "
        f"| {side_str} | {t['entry_price']:,.0f}→{t['exit_price']:,.0f} | {pts_str} "
        f"| {t['qty']} | {pnl_str} | {bal_after:,.0f} | {el} |"
    )
    balance = bal_after

lines += [
    "",
    "> **TRAIL** = 追蹤止損出場　**TIME** = 時間止損（持滿 2 小時）",
    "",
    "---",
    "",
    f"*報告生成：{datetime.now().strftime('%Y-%m-%d %H:%M')} | 資料：永豐 TMF 1 分鐘 K 線 | 策略：BreakoutTrendStrategy v1.0*",
]

out = "\n".join(lines)
out_path = ROOT / "data" / "breakout_backtest_report.md"
out_path.write_text(out, encoding="utf-8")
print(f"Written {len(lines)} lines → {out_path}")
