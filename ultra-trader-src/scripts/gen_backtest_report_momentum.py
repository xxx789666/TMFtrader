"""生成 BreakoutTrend v3 動能評分 5min 回測 Markdown 報告"""
import json, sys
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

with open(ROOT / "data" / "breakout_5m_momentum_full.json", "r", encoding="utf-8") as f:
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
    "# BreakoutTrend Strategy v3 — 台指期微台（TMF）5分鐘 K 線回測報告",
    "",
    "> **策略**：ATR 壓縮突破（Mode A）＋ 強趨勢 EMA 回調進場（Mode B）",
    "> **趨勢過濾**：EMA200 宏觀方向 ＋ RSI_MA5 動能評分 ＋ 盤中漲幅方向（三維動能過濾）",
    "> **商品**：台指期微台（TMF）1 點 = 10 元，每次固定 3 口",
    "> **資料**：2024-01-01 ~ 2026-04-11（28 個月），**5 分鐘 K 線**，共 31,050 根",
    "> **初始資金**：200,000 TWD ｜ 風控模式：tmf_3x（最多 3 口，不超過 4% 單筆風險）",
    "> **出場邏輯**：追蹤出場（獲利 > 1.0×ATR 啟動，距離 1.25×ATR）/ 早切止損（持倉 ≥ 25 根且虧損 ≥ 1.5×ATR）/ 時間出場（80 根）",
    "> **動能評分**：EMA200(×2) + RSI_MA5>52(+1)/<48(-1) + 盤中漲幅>0.5×ATR(+1)/<−0.5×ATR(−1)，score≥+2 封鎖做空，≤−2 封鎖做多",
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
    f"| 出場：追蹤出場 | {trail_cnt} 筆（{trail_cnt/n*100:.0f}%）|",
    f"| 出場：早切止損 | {early_cnt} 筆（{early_cnt/n*100:.0f}%）|",
    f"| 出場：時間出場 | {time_cnt} 筆（{time_cnt/n*100:.0f}%）|",
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
    "## 與前版本比較",
    "",
    "| 版本 | 過濾方式 | 筆數 | WR | PF | 淨利 | 報酬 | ≥6%月 | 最差月 |",
    "|---|---|---|---|---|---|---|---|---|",
    "| v1（無過濾）| — | 133 | 56.4% | 1.498 | +121,680 | +60.8% | 6/28 | -8.3% |",
    "| v2（EMA200-only）| EMA200 宏觀方向 | 106 | 56.6% | 1.630 | +127,380 | +63.7% | 6/28 | -8.2% |",
    f"| **v3（EMA200+動能）**| EMA200 + RSI + 盤中方向 | **{n}** | **{wr:.1f}%** | **{pf:.3f}** | **+{net:,.0f}** | **+{net/200_000*100:.1f}%** | **{n_target}/28** | **{min_ret:.1f}%** |",
    "",
    "---",
    "",
    "## 策略參數（v3 動能評分版）",
    "",
    "| 參數 | 值 | 說明 |",
    "|---|---|---|",
    "| `squeeze_ratio` | **0.90** | ATR < avg×0.90 = 壓縮中 |",
    "| `expand_ratio` | 1.08 | ATR > avg×1.08 = 突破擴張 |",
    "| `min_vol_ratio` | **1.0** | 放量門檻 |",
    "| `min_adx` | 20.0 | 早盤 ADX 門檻 |",
    "| `afternoon_min_adx` | 32.0 | 11:00 後 ADX 門檻 |",
    "| `sl_atr` | 2.5 | 停損距離（ATR 倍數）|",
    "| `trail_trigger_atr` | **1.0** | 獲利超過 1.0×ATR 後啟動追蹤 |",
    "| `trail_dist_atr` | **1.25** | 追蹤出場距離 |",
    "| `max_bars` | **80** | 時間出場（80根×5min=400min）|",
    "| `early_cut_bars` | **25** | 早切止損啟動根數（25根=125min）|",
    "| `early_cut_loss_atr` | **1.5** | 早切止損門檻 |",
    "| `ema200_margin_atr` | 0.0 | EMA200 緩衝帶（嚴格模式）|",
    "| `momentum_rsi_bull` | **52** | RSI_MA5 > 52 → 動能偏多（+1）|",
    "| `momentum_rsi_bear` | **48** | RSI_MA5 < 48 → 動能偏空（-1）|",
    "| `momentum_session_atr` | **0.5** | 盤中漲幅 > 0.5×ATR → 盤中偏多（+1）|",
    "| 口數 | **3 口** | tmf_3x 風控模式 |",
    "",
    "### 動能評分邏輯",
    "",
    "**合成分數 = EMA200_vote × 2 + RSI_vote + session_vote**",
    "",
    "| 分量 | 條件 | 分值 |",
    "|---|---|---|",
    "| EMA200 | price > EMA200 | +2（牛市）|",
    "| EMA200 | price < EMA200 | -2（熊市）|",
    "| RSI_MA5 | RSI_MA5 > 52 | +1（動能偏多）|",
    "| RSI_MA5 | RSI_MA5 < 48 | -1（動能偏空）|",
    "| 盤中方向 | 今日漲幅 > 0.5×ATR | +1（盤中偏多）|",
    "| 盤中方向 | 今日漲幅 < -0.5×ATR | -1（盤中偏空）|",
    "",
    "**決策規則**：score ≥ +2 → 封鎖做空；score ≤ -2 → 封鎖做多；否則雙向皆可",
    "",
    "> **設計理念**：EMA200 權重加倍作為宏觀主導，RSI 和盤中方向可修正 EMA200 的滯後性。",
    "> 當 EMA200 偏空（-2）但 RSI 回升（+1）且盤中上漲（+1）時，score = 0 → 允許做多，",
    "> 捕捉市場反轉初期機會。關鍵改善：2025-11 月從 -7% → -1.8%，2026-03 從 +5% → +6.8%（達標）。",
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
    f"*報告生成：{datetime.now().strftime('%Y-%m-%d %H:%M')} | 資料：永豐 TMF 5 分鐘 K 線 | 策略：BreakoutTrendStrategy v3*",
]

out = "\n".join(lines)
out_path = ROOT / "data" / "backtest_results" / f"backtest_5m_momentum_{datetime.now().strftime('%Y%m%d')}.md"
out_path.parent.mkdir(parents=True, exist_ok=True)
out_path.write_text(out, encoding="utf-8")
print(f"Written {len(lines)} lines → {out_path}")
