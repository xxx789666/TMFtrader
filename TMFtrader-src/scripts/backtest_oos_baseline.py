"""
Phase 0 — Step B：OOS 基準回測
測試期：2025-01-01 ~ 2026-04-30（完全 OOS）

四路對比：
  A. 純 ORB（無任何過濾）
  B. vol_ratio <= 2.0（無 ML）
  C. OOS ML（2024 年訓練的 orb_filter_oos_v1.pkl，threshold=0.40）
  D. 現有 B2（全期訓練的 orb_filter_b2.pkl，threshold=0.40）← 基準

Gate 0 判斷：C 方案 OOS PF > 1.3 且 MaxDD < 8%

用法：python scripts/backtest_oos_baseline.py
"""
import sys, warnings
warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')

import pandas as pd
import numpy as np
import pickle
from pathlib import Path

OOS_START     = '2025-01-01'
RISK_PCT      = 0.04
MAX_CONTRACTS = 3
POINT_VALUE   = 10.0
INIT_EQUITY   = 200_000.0

# ─── 載入資料 ─────────────────────────────────────────────────────
print("=" * 65)
print("  Phase 0 Step B：OOS 基準回測（測試期 >= 2025-01-01）")
print("=" * 65)

df_all = pd.read_parquet('data/historical/ml/TMF_night_orb_signals.parquet')
df_all['entry_time'] = pd.to_datetime(df_all['entry_time'])
df_all['exit_time']  = pd.to_datetime(df_all['exit_time'])

df_oos = df_all[df_all['entry_time'] >= OOS_START].copy().reset_index(drop=True)

print(f"\nTMF 夜盤 ORB 總信號：{len(df_all)} 筆")
print(f"OOS 期（{OOS_START}+）：{len(df_oos)} 筆")
print(f"  Win（raw）：{df_oos['win'].sum()}/{len(df_oos)}  "
      f"({df_oos['win'].mean()*100:.1f}%)")

# ─── 載入特徵 ─────────────────────────────────────────────────────
features_b2  = [ln.strip() for ln in
                open('optimizer/results_orb/selected_features_b2.txt').readlines()
                if ln.strip()]
oos_feat_path = Path('optimizer/results_orb/selected_features_oos_v1.txt')
if oos_feat_path.exists():
    features_oos = [ln.strip() for ln in oos_feat_path.read_text(encoding='utf-8').splitlines() if ln.strip()]
else:
    features_oos = features_b2  # fallback

# ─── 載入模型 ─────────────────────────────────────────────────────
with open('optimizer/results_orb/orb_filter_b2.pkl', 'rb') as f:
    model_b2 = pickle.load(f)

oos_model_path = Path('optimizer/results_orb/orb_filter_oos_v1.pkl')
if oos_model_path.exists():
    with open(oos_model_path, 'rb') as f:
        model_oos = pickle.load(f)
    print(f"\nOOS 模型載入：{oos_model_path}")
else:
    model_oos = None
    print(f"\n[WARN] OOS 模型未找到，C 方案跳過。先執行 train_oos_model.py")

# ─── 回測函數 ─────────────────────────────────────────────────────
def run_backtest(trades_df, label):
    """動態口數回測，回傳月別與整體統計"""
    if len(trades_df) == 0:
        return None

    equity = INIT_EQUITY
    pnl_list, qty_list = [], []

    for _, row in trades_df.iterrows():
        stop_dist = abs(float(row['entry_price']) - float(row['stop_price']))
        if stop_dist < 1e-6:
            stop_dist = float(row['atr_at_entry']) * 2.0
        qty  = min(MAX_CONTRACTS, max(1, int(equity * RISK_PCT / (stop_dist * POINT_VALUE))))
        pnl  = (float(row['exit_price']) - float(row['entry_price'])) * int(row['direction']) * qty * POINT_VALUE
        pnl_list.append(pnl)
        qty_list.append(qty)
        equity += pnl

    trades_df = trades_df.copy()
    trades_df['qty']     = qty_list
    trades_df['pnl_twd'] = pnl_list

    n    = len(trades_df)
    wins = int((trades_df['pnl_twd'] > 0).sum())
    gp   = float(trades_df[trades_df['pnl_twd'] > 0]['pnl_twd'].sum())
    gl   = float(abs(trades_df[trades_df['pnl_twd'] < 0]['pnl_twd'].sum()))
    net  = float(trades_df['pnl_twd'].sum())
    wr   = wins / n * 100
    pf   = gp / gl if gl > 1e-6 else 999.0

    eq   = np.concatenate([[INIT_EQUITY], INIT_EQUITY + np.cumsum(trades_df['pnl_twd'].values)])
    peak = np.maximum.accumulate(eq)
    mdd  = float(((peak - eq) / peak * 100).max())

    # 逐月
    trades_df['ym'] = trades_df['entry_time'].dt.to_period('M').astype(str)
    monthly = {}
    for ym, grp in trades_df.groupby('ym'):
        monthly[ym] = {
            'n':    len(grp),
            'wins': int((grp['pnl_twd'] > 0).sum()),
            'pnl':  float(grp['pnl_twd'].sum()),
        }

    return {
        'label': label, 'n': n, 'wins': wins, 'wr': wr,
        'pf': pf, 'net': net, 'mdd': mdd, 'monthly': monthly,
        'trades': trades_df,
    }


def print_result(r, show_monthly=True):
    if r is None:
        print("  （無資料）")
        return
    gate_pf  = '✓' if r['pf']  > 1.3 else '✗'
    gate_mdd = '✓' if r['mdd'] < 8.0 else '✗'
    print(f"  交易筆數：{r['n']:3d}  "
          f"WR：{r['wr']:5.1f}%  "
          f"PF：{r['pf']:6.3f} {gate_pf}  "
          f"淨利：{r['net']:+9,.0f}  "
          f"MaxDD：{r['mdd']:5.2f}% {gate_mdd}")
    if show_monthly:
        bal = INIT_EQUITY
        print(f"  {'月份':<9} {'筆':>3} {'勝/敗':>6} {'損益':>9} {'報酬':>7} {'累積資金':>10}")
        print(f"  {'─'*55}")
        for ym in sorted(r['monthly']):
            m   = r['monthly'][ym]
            ret = m['pnl'] / bal * 100
            bal += m['pnl']
            flag = 'O' if m['pnl'] > 0 else 'X'
            print(f"  {ym:<9} {m['n']:>3}  {m['wins']:>2}/{m['n']-m['wins']:<2}  "
                  f"{m['pnl']:>+9,.0f}  {ret:>+6.1f}%  {bal:>10,.0f} {flag}")


# ─── A：純 ORB（無過濾）─────────────────────────────────────────
print(f"\n{'─'*65}")
print("  A. 純 ORB（無任何過濾）")
r_A = run_backtest(df_oos, 'A_raw')
print_result(r_A)

# ─── B：vol_ratio <= 2.0（無 ML）────────────────────────────────
print(f"\n{'─'*65}")
print("  B. vol_ratio <= 2.0（無 ML）")
df_B = df_oos[df_oos['f_vol_ratio'] <= 2.0].copy().reset_index(drop=True)
print(f"  過濾後：{len(df_B)} 筆（原 {len(df_oos)} 筆）")
r_B = run_backtest(df_B, 'B_vol_ratio')
print_result(r_B)

# ─── C：OOS ML 模型（2024 年訓練）──────────────────────────────
print(f"\n{'─'*65}")
print("  C. OOS ML（2024 訓練）threshold=0.40")
if model_oos is not None:
    df_C_base = df_oos[df_oos['f_vol_ratio'] <= 2.0].copy().reset_index(drop=True)
    probs_oos = model_oos.predict_proba(df_C_base[features_oos].fillna(0).values)
    df_C_base['ml_prob'] = probs_oos
    df_C = df_C_base[df_C_base['ml_prob'] >= 0.40].copy().reset_index(drop=True)
    print(f"  ML 過濾後：{len(df_C)} 筆（原 {len(df_C_base)} 筆通過 vol_ratio）")
    r_C = run_backtest(df_C, 'C_oos_ml')
    print_result(r_C)
else:
    r_C = None
    print("  [SKIP] OOS 模型不存在")

# ─── D：現有 B2 模型（全期訓練，基準）───────────────────────────
print(f"\n{'─'*65}")
print("  D. 現有 B2 模型（全期訓練）threshold=0.40  ← 原始基準")
df_D_base = df_oos[df_oos['f_vol_ratio'] <= 2.0].copy().reset_index(drop=True)
probs_b2 = model_b2.predict_proba(df_D_base[features_b2].fillna(0).values)
df_D_base['ml_prob'] = probs_b2
df_D = df_D_base[df_D_base['ml_prob'] >= 0.40].copy().reset_index(drop=True)
print(f"  ML 過濾後：{len(df_D)} 筆")
r_D = run_backtest(df_D, 'D_b2_full')
print_result(r_D)

# ─── Gate 0 判斷 ─────────────────────────────────────────────────
print(f"\n{'='*65}")
print("  Gate 0 判斷（基於方案 C OOS ML）")
print(f"{'='*65}")
if r_C is not None:
    pf_pass  = r_C['pf']  > 1.3
    mdd_pass = r_C['mdd'] < 8.0

    print(f"  PF > 1.3  ：{r_C['pf']:.3f}  →  {'✓ PASS' if pf_pass  else '✗ FAIL'}")
    print(f"  MaxDD < 8%：{r_C['mdd']:.2f}%  →  {'✓ PASS' if mdd_pass else '✗ FAIL'}")

    if pf_pass and mdd_pass:
        verdict = "✅ Gate 0 PASS → 進入 Phase 2 Walk-Forward"
    else:
        verdict = "❌ Gate 0 FAIL → 執行 Phase 1 修 Bug，再重跑 Phase 0"
    print(f"\n  {verdict}")
else:
    print("  [SKIP] C 方案未跑，請先執行 train_oos_model.py")

# ─── 四路對比摘要 ────────────────────────────────────────────────
print(f"\n{'─'*65}")
print("  四路對比摘要（OOS 期 2025-01 ~ 2026-04）")
print(f"{'─'*65}")
print(f"  {'方案':<6} {'筆數':>4} {'WR':>6} {'PF':>7} {'淨利':>10} {'MaxDD':>7}")
print(f"  {'─'*50}")
for r in [r_A, r_B, r_C, r_D]:
    if r is None:
        continue
    label_map = {
        'A_raw':     'A 純ORB',
        'B_vol_ratio':'B vol≤2',
        'C_oos_ml':  'C OOS-ML',
        'D_b2_full': 'D B2全期',
    }
    lbl = label_map.get(r['label'], r['label'])
    gate = '✓' if (r['pf'] > 1.3 and r['mdd'] < 8.0) else '✗'
    print(f"  {lbl:<8} {r['n']:>4}  {r['wr']:>5.1f}%  "
          f"{r['pf']:>6.3f}  {r['net']:>+10,.0f}  {r['mdd']:>6.2f}% {gate}")

print(f"{'='*65}")

# ─── 儲存報告 ────────────────────────────────────────────────────
out_dir = Path('docs')
out_dir.mkdir(exist_ok=True)
lines = []
lines.append("# Phase 0 OOS 基準回測報告")
lines.append(f"\n測試期：{OOS_START} ~ 2026-04-30")
lines.append(f"\n## 四路對比摘要\n")
lines.append("| 方案 | 筆數 | WR | PF | 淨利 | MaxDD | Gate |")
lines.append("|------|------|----|----|------|-------|------|")
for r in [r_A, r_B, r_C, r_D]:
    if r is None:
        continue
    gate = '✓' if (r['pf'] > 1.3 and r['mdd'] < 8.0) else '✗'
    lines.append(f"| {r['label']} | {r['n']} | {r['wr']:.1f}% | "
                 f"{r['pf']:.3f} | {r['net']:+,.0f} | {r['mdd']:.2f}% | {gate} |")

if r_C is not None:
    verdict = "PASS → Phase 2" if (r_C['pf'] > 1.3 and r_C['mdd'] < 8.0) else "FAIL → Phase 1"
    lines.append(f"\n## Gate 0：{verdict}")
    lines.append(f"- PF：{r_C['pf']:.3f}（門檻 > 1.3）")
    lines.append(f"- MaxDD：{r_C['mdd']:.2f}%（門檻 < 8%）")

report_path = out_dir / 'oos_baseline_report.md'
report_path.write_text('\n'.join(lines), encoding='utf-8')
print(f"\n報告儲存：{report_path}")
