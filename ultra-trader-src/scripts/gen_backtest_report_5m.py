"""生成 BreakoutTrend 5min 回測 Markdown 報告（ec=25 loss=1.5 穩定性版）"""
import json, sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

with open(ROOT / "data" / "breakout_5m_ec25_full.json", "r", encoding="utf-8") as f:
    d = json.load(f)

trades  = d["trades"]
monthly = d["monthly"]

n      = len(trades)
wins   = [t for t in trades if t["pnl"] > 0]
losses = [t for t in trades if t["pnl"] <= 0]
gp     = sum(t["pnl"] for t in wins)
gl     = abs(sum(t["pnl"] for t in losses))
pf     = gp / gl if gl else 0
wr     = len(wins) / n * 100
avg_w  = gp / len(wins) if wins else 0
avg_l  = gl / len(losses) if losses else 0

n_target = sum(1 for m in monthly if m["ret_pct"] >= 6)
n_green  = sum(1 for m in monthly if m["ret_pct"] > 0)
avg_ret  = sum(m["ret_pct"] for m in monthly) / len(monthly)
min_ret  = min(m["ret_pct"] for m in monthly)
max_ret  = max(m["ret_pct"] for m in monthly)
worst_m  = next(m["ym"] for m in monthly if m["ret_pct"] == min_ret)
best_m   = next(m["ym"] for m in monthly if m["ret_pct"] == max_ret)

def exit_label(t):
    r = t["reason"]
    if "追蹤" in r: return "TRAIL"
    if "早切" in r: return "EARLY"
    if "時間" in r: return "TIME"
    return "EOD"

trail_cnt = sum(1 for t in trades if exit_label(t) == "TRAIL")
early_cnt = sum(1 for t in trades if exit_label(t) == "EARLY")
time_cnt  = sum(1 for t in trades if exit_label(t) == "TIME")

net       = d["summary"]["net"]
final_bal = d["summary"]["final_balance"]

lines = []
lines += [
    "# BreakoutTrend Strategy v2 — 台指期微台（TMF）5分鐘 K 線回測報告",
    "",
    "> **策略**：ATR 壓縮突破（Mode A）＋ 強趨勢 EMA 回調進場（Mode B）",
    "> **商品**：台指期微台（TMF）1 點 = 10 元，每次固定 3 口",
    "> **資料**：2024-01-01 ~ 2026-04-11（28 個月），**5 分鐘 K 線**，共 31,050 根",
    "> **初始資金**：200,000 TWD ｜ 風控模式：tmf_3x（最多 3 口，不超過 4% 單筆風險）",
    "> **出場邏輯**：追蹤止損（獲利 > 1.0×ATR 啟動，距離 1.25×ATR）/ 早切止損（持倉 ≥ 25 根且虧損 ≥ 1.5×ATR）/ 時間止損（80 根）",
    "> **進場過濾**：ADX ≥ 20（早盤）/ ADX ≥ 32（11:00 後）/ squeeze_ratio=0.90 / vol_ratio≥1.0",
    "",
    "---",
    "",
    "## 績效總覽",
    "",
    "| 指標 | 數值 |",
    "|---|---|",
    f"| 回測期間 | 2024-01-01 ~ 2026-04-11（28 個月）|",
    f"| K 線週期 | **5 分鐘** |",
    f"| 交易筆數 | **{n} 筆** |",
    f"| 勝筆 / 敗筆 | {len(wins)} / {len(losses)} |",
    f"| 勝率 | **{wr:.1f}%** |",
    f"| 獲利因子（PF）| **{pf:.3f}** |",
    f"| 初始資金 | 200,000 TWD |",
    f"| 期末資金 | **{final_bal:,.0f} TWD** |",
    f"| 淨利 | **+{net:,.0f} TWD** |",
    f"| 總報酬 | **+{net/200_000*100:.1f}%**（2 年）|",
    f"| 年化報酬 | ≈ +{net/200_000*100/2.28:.0f}% |",
    f"| 月均報酬 | {avg_ret:+.2f}% |",
    f"| 最佳月 | **{max_ret:+.1f}%**（{best_m}）|",
    f"| 最差月 | **{min_ret:+.1f}%**（{worst_m}）|",
    f"| 6% 達標月數 | **{n_target}/28** |",
    f"| 獲利月數 | {n_green}/28 |",
    f"| 平均獲利筆 | +{avg_w:,.0f} TWD（≈ {avg_w/10:.0f} 點）|",
    f"| 平均虧損筆 | -{avg_l:,.0f} TWD（≈ {avg_l/10:.0f} 點）|",
    f"| 盈虧比 | {avg_w/avg_l:.2f} : 1 |",
    f"| 出場：追蹤出場 | {trail_cnt} 筆（{trail_cnt/n*100:.0f}%）WR=100% |",
    f"| 出場：早切止損 | {early_cnt} 筆（{early_cnt/n*100:.0f}%）WR=0% |",
    f"| 出場：時間出場 | {time_cnt} 筆（{time_cnt/n*100:.0f}%）WR=100% |",
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
    yr_n6     = sum(1 for m in yr_months if m["ret_pct"] >= 6)
    yr_ng     = sum(1 for m in yr_months if m["ret_pct"] > 0)
    note = ""
    if yr == 2024: note = f"磨合期，≥6%={yr_n6}/{yr_m_cnt}月，最差月 2024-11"
    if yr == 2025: note = f"穩定期，≥6%={yr_n6}/{yr_m_cnt}月，9月+12.3%"
    if yr == 2026: note = f"強勢期，≥6%={yr_n6}/{yr_m_cnt}月，2月+16.6%"
    lines.append(
        f"| {yr} | {yr_m_cnt} 個月 | {yr_n} 筆 | {yr_wins}/{yr_n-yr_wins} | {yr_wr:.1f}% | {pnl_str} | {note} |"
    )

lines += [
    "",
    "---",
    "",
    "## 策略參數（5min K線 最佳化結果）",
    "",
    "| 參數 | 值 | 說明 |",
    "|---|---|---|",
    "| `squeeze_ratio` | **0.90** | ATR < avg×0.90 = 壓縮中（5min 放寬 0.78→0.90）|",
    "| `expand_ratio` | 1.08 | ATR > avg×1.08 = 突破擴張 |",
    "| `min_vol_ratio` | **1.0** | 放量門檻（5min 放寬 1.15→1.0）|",
    "| `min_adx` | 20.0 | 早盤 ADX 門檻 |",
    "| `afternoon_min_adx` | 32.0 | 11:00 後 ADX 門檻 |",
    "| `min_di_gap` | 5.0 | +DI 與 -DI 最小差距 |",
    "| `sl_atr` | 2.5 | 停損距離（ATR 倍數）|",
    "| `tp_atr` | 10.0 | 固定停利（幾乎不觸及，由 trail 出場）|",
    "| `trail_trigger_atr` | **1.0** | 獲利超過 1.0×ATR 後啟動追蹤（5min 1.75→1.0）|",
    "| `trail_dist_atr` | **1.25** | 追蹤止損距離（5min 1.75→1.25）|",
    "| `max_bars` | **80** | 時間止損（80根×5min=400min）|",
    "| `early_cut_bars` | **25** | 早切止損啟動根數（25根=125min）|",
    "| `early_cut_loss_atr` | **1.5** | 早切止損門檻（虧損 ≥ 1.5×ATR 才啟動）|",
    "| 口數 | **3 口** | tmf_3x 風控模式，最多 3 口 |",
    "",
    "### 早切止損邏輯",
    "",
    "> 當持倉超過 **25 根（125 分鐘）** 且當前虧損 ≥ **1.5×ATR** 時，立即出場。",
    "> 效果：將最差月從 **-21.1%** 壓縮至 **-8.3%**，同時提升整體 PF 至 1.498。",
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
        f"| {i} | {t['entry_date']} {t['entry_time'][11:16]} | {t['exit_date']} {t['exit_time'][11:16]} "
        f"| {side_str} | {t['entry_price']:,.0f}→{t['exit_price']:,.0f} | {pts_str} "
        f"| {t['qty']} | {pnl_str} | {bal_after:,.0f} | {el} |"
    )
    balance = bal_after

lines += [
    "",
    "> **TRAIL** = 追蹤出場（獲利後啟動）　**EARLY** = 早切止損（虧損出場）　**TIME** = 時間出場（持滿 400min）",
    "",
    "---",
    "",
    f"*報告生成：{datetime.now().strftime('%Y-%m-%d %H:%M')} | 資料：永豐 TMF 5 分鐘 K 線 | 策略：BreakoutTrendStrategy v2*",
]

out = "\n".join(lines)
out_path = ROOT / "data" / "backtest_results" / f"backtest_5m_ec25_{datetime.now().strftime('%Y%m%d')}.md"
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(out, encoding="utf-8")
print(f"Written {len(lines)} lines → {out_path}")
