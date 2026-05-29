"""
Phase 5：穩健性測試
  5-1 Monte Carlo（500次隨機打亂）
  5-2 參數敏感度（trail_dist ±、max_sl_pts ±、orb_width ±）
  5-3 近期 OOS（最近 9 個月）績效
  5-4 最終 Gate 判斷

用法：python scripts/phase5_robustness.py
"""
import sys, warnings
warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')

import pandas as pd
import numpy as np
import pickle
from pathlib import Path

# ─── 載入 OOS 資料（純 ORB，無 ML） ──────────────────────────────
df_sig = pd.read_parquet('data/historical/ml/TMF_night_orb_signals.parquet')
df_sig['entry_time'] = pd.to_datetime(df_sig['entry_time'])
oos = df_sig[df_sig['entry_time'] >= '2025-01-01'].copy().reset_index(drop=True)

INIT_EQ   = 200_000.0
RISK_PCT  = 0.04
MAX_QTY   = 3
PV        = 10.0

def run_trades(trades_df):
    eq = INIT_EQ
    pnl_list = []
    for _, row in trades_df.iterrows():
        sl_d = abs(float(row['entry_price']) - float(row['stop_price']))
        if sl_d < 1e-6:
            sl_d = float(row['atr_at_entry']) * 2.0
        qty  = min(MAX_QTY, max(1, int(eq * RISK_PCT / (sl_d * PV))))
        pnl  = (float(row['exit_price']) - float(row['entry_price'])) * int(row['direction']) * qty * PV
        pnl_list.append(pnl)
        eq  += pnl
    arr  = np.array(pnl_list)
    wins = (arr > 0).sum()
    gp   = arr[arr > 0].sum()
    gl   = abs(arr[arr < 0].sum())
    pf   = gp / gl if gl > 1e-6 else 999.0
    eq_c = np.concatenate([[INIT_EQ], INIT_EQ + np.cumsum(arr)])
    pk   = np.maximum.accumulate(eq_c)
    mdd  = float(((pk - eq_c) / pk * 100).max())
    return float(wins / len(arr) * 100), pf, float(arr.sum()), mdd

# 基準
base_wr, base_pf, base_net, base_mdd = run_trades(oos)

print("=" * 65)
print("  Phase 5：穩健性測試（OOS 2025-01 ~ 2026-04，n=75）")
print("=" * 65)
print(f"\n  基準績效：WR={base_wr:.1f}%  PF={base_pf:.3f}  "
      f"淨利={base_net:+,.0f}  MaxDD={base_mdd:.2f}%")

# ═══════════════════════════════════════════════════════════════
# 5-1 Monte Carlo
# ═══════════════════════════════════════════════════════════════
print(f"\n{'─'*65}")
print("  5-1 Monte Carlo（500 次隨機打亂交易順序）")
print(f"{'─'*65}")

N_SIM = 500
mc_pf   = []
mc_mdd  = []
mc_net  = []

rng = np.random.default_rng(42)
for i in range(N_SIM):
    shuffled = oos.sample(frac=1.0, random_state=int(rng.integers(0, 99999)))
    shuffled = shuffled.reset_index(drop=True)
    _, pf, net, mdd = run_trades(shuffled)
    mc_pf.append(pf)
    mc_mdd.append(mdd)
    mc_net.append(net)

mc_pf  = np.array(mc_pf)
mc_mdd = np.array(mc_mdd)
mc_net = np.array(mc_net)

pf_p5   = np.percentile(mc_pf,  5)
mdd_p95 = np.percentile(mc_mdd, 95)
net_p5  = np.percentile(mc_net, 5)

print(f"\n  PF   分布：mean={mc_pf.mean():.3f}  p5={pf_p5:.3f}  p95={np.percentile(mc_pf,95):.3f}")
print(f"  MDD  分布：mean={mc_mdd.mean():.2f}%  p5={np.percentile(mc_mdd,5):.2f}%  p95={mdd_p95:.2f}%")
print(f"  淨利 分布：mean={mc_net.mean():+,.0f}  p5={net_p5:+,.0f}  p95={np.percentile(mc_net,95):+,.0f}")

gate_mc_pf  = pf_p5  > 1.0
gate_mc_mdd = mdd_p95 < 12.0
gate_mc_net = net_p5  > 0

print(f"\n  Gate 5-1：")
print(f"    PF p5 > 1.0    ：{pf_p5:.3f}  → {'✓ PASS' if gate_mc_pf  else '✗ FAIL'}")
print(f"    MDD p95 < 12%  ：{mdd_p95:.2f}%  → {'✓ PASS' if gate_mc_mdd else '✗ FAIL'}")
print(f"    淨利 p5 > 0    ：{net_p5:+,.0f}  → {'✓ PASS' if gate_mc_net else '✗ FAIL'}")

# ═══════════════════════════════════════════════════════════════
# 5-2 參數敏感度
# ═══════════════════════════════════════════════════════════════
print(f"\n{'─'*65}")
print("  5-2 參數敏感度（±變化對績效影響）")
print(f"{'─'*65}")

df_5m  = pd.read_parquet('data/historical/ml/TMF_night_5m.parquet')
df_5m['datetime'] = pd.to_datetime(df_5m['datetime'])
df_5m  = df_5m.reset_index(drop=True)
df_all = pd.read_parquet('data/historical/ml/TMF_night_orb_signals.parquet')
df_all['entry_time'] = pd.to_datetime(df_all['entry_time'])
df_all['exit_time']  = pd.to_datetime(df_all['exit_time'])
oos_all = df_all[df_all['entry_time'] >= '2025-01-01'].reset_index(drop=True)

def calc_mfe_mae(signals):
    records = []
    for _, row in signals.iterrows():
        i0=int(row['entry_bar']); i1=int(row['exit_bar'])
        d=int(row['direction']); ep=float(row['entry_price'])
        sl_d=abs(ep-float(row['stop_price'])); atr=float(row['atr_at_entry'])
        bars=df_5m.iloc[i0:i1+1]
        if len(bars)==0: continue
        mfe=(bars['high'].max()-ep) if d==1 else (ep-bars['low'].min())
        mae=(ep-bars['low'].min()) if d==1 else (bars['high'].max()-ep)
        records.append({'atr':atr,'sl_dist':sl_d,
                        'mfe':max(float(mfe),0),'mae':max(float(mae),0),
                        'actual_exit':float(row['exit_price']-ep)*d})
    return pd.DataFrame(records)

df_path = calc_mfe_mae(oos_all)

def sim_v2(df, trigger_atr, dist_atr, sl_cap):
    """模擬不同參數組合"""
    pnl = []
    for _, r in df.iterrows():
        sl = min(r['sl_dist'], sl_cap) if sl_cap > 0 else r['sl_dist']
        trigger = trigger_atr * r['atr']
        dist    = dist_atr    * r['atr']
        if r['mae'] >= sl:
            pnl.append(-sl)
        elif r['mfe'] < trigger:
            pnl.append(r['actual_exit'])
        else:
            pnl.append(max(r['mfe'] - dist, r['actual_exit']))
    arr = np.array(pnl)
    w=(arr>0).sum(); gp=arr[arr>0].sum(); gl=abs(arr[arr<0].sum())
    pf=gp/gl if gl>1e-6 else 999.0
    return float(w/len(arr)*100), pf, float(arr.sum())

# 基準：trigger=0.8, dist=0.3, sl_cap=120
base_s = sim_v2(df_path, 0.8, 0.3, 120)
print(f"\n  {'參數組合':<35} {'WR':>6} {'PF':>7} {'淨利pts':>9}  vs基準PF")
print(f"  {'─'*65}")

combos = [
    ('基準（trig=0.8 dist=0.3 sl=120）',    0.8, 0.3, 120, True),
    ('trail_dist -0.1（dist=0.2）',          0.8, 0.2, 120, False),
    ('trail_dist +0.1（dist=0.4）',          0.8, 0.4, 120, False),
    ('trail_dist +0.2（dist=0.5）',          0.8, 0.5, 120, False),
    ('trail_trigger -0.1（trig=0.7）',       0.7, 0.3, 120, False),
    ('trail_trigger +0.2（trig=1.0）',       1.0, 0.3, 120, False),
    ('trail_trigger +0.4（trig=1.2）',       1.2, 0.3, 120, False),
    ('max_sl_pts=80  (-33%)' ,               0.8, 0.3,  80, False),
    ('max_sl_pts=150 (+25%)',                0.8, 0.3, 150, False),
    ('max_sl_pts=0（無上限）',               0.8, 0.3,   0, False),
]

pf_diffs = []
for label, ta, da, sl, is_base in combos:
    wr, pf, net = sim_v2(df_path, ta, da, sl)
    diff = pf - base_s[1]
    pf_diffs.append(abs(diff))
    mark = '' if is_base else f'  Δ={diff:+.3f}'
    print(f"  {label:<35} {wr:>5.1f}%  {pf:>6.3f}  {net:>+9.0f}{mark}")

# 敏感度判斷：任何相鄰變化 PF 差 < 0.5 → 穩健
max_pf_diff = max(pf_diffs[1:])  # 排除基準本身
gate_sensitivity = max_pf_diff < 0.5
print(f"\n  Gate 5-2：最大 PF 變動={max_pf_diff:.3f}  < 0.5 → "
      f"{'✓ PASS 穩健' if gate_sensitivity else '✗ FAIL 敏感'}")

# ═══════════════════════════════════════════════════════════════
# 5-3 近期 OOS（最近 9 個月：2025-07 ~ 2026-04）
# ═══════════════════════════════════════════════════════════════
print(f"\n{'─'*65}")
print("  5-3 近期 OOS 績效（最近 9 個月：2025-07 ~ 2026-04）")
print(f"{'─'*65}")

recent = oos[oos['entry_time'] >= '2025-07-01'].copy().reset_index(drop=True)
r_wr, r_pf, r_net, r_mdd = run_trades(recent)

print(f"\n  筆數：{len(recent)}  WR：{r_wr:.1f}%  PF：{r_pf:.3f}  "
      f"淨利：{r_net:+,.0f}  MaxDD：{r_mdd:.2f}%")

# 月別
recent['ym'] = recent['entry_time'].dt.to_period('M').astype(str)
bal = INIT_EQ
consec_loss = 0; max_consec = 0; cur_consec = 0
monthly_results = []
for ym, grp in recent.groupby('ym'):
    _, _, m_net, _ = run_trades(grp.reset_index(drop=True))
    ret = m_net / bal * 100
    bal += m_net
    flag = 'O' if m_net > 0 else 'X'
    monthly_results.append(m_net)
    if m_net < 0:
        cur_consec += 1
        max_consec = max(max_consec, cur_consec)
    else:
        cur_consec = 0
    print(f"  {ym}：{len(grp):2d}筆  淨利={m_net:+8,.0f}  {ret:+.1f}%  {flag}")

gate_recent_pf  = r_pf  > 1.2
gate_recent_mdd = r_mdd < 8.0
gate_recent_con = max_consec <= 2

print(f"\n  Gate 5-3：")
print(f"    PF > 1.2       ：{r_pf:.3f}  → {'✓ PASS' if gate_recent_pf  else '✗ FAIL'}")
print(f"    MaxDD < 8%     ：{r_mdd:.2f}%  → {'✓ PASS' if gate_recent_mdd else '✗ FAIL'}")
print(f"    連虧月 ≤ 2個月  ：{max_consec}個月  → {'✓ PASS' if gate_recent_con else '✗ FAIL'}")

# ═══════════════════════════════════════════════════════════════
# 總結判斷
# ═══════════════════════════════════════════════════════════════
all_gates = [gate_mc_pf, gate_mc_mdd, gate_mc_net,
             gate_sensitivity,
             gate_recent_pf, gate_recent_mdd, gate_recent_con]
gate_names = [
    'MC PF p5>1.0', 'MC MDD p95<12%', 'MC 淨利p5>0',
    '敏感度 ΔPF<0.5',
    '近期PF>1.2', '近期MDD<8%', '近期連虧≤2月',
]
all_pass = all(all_gates)

print(f"\n{'='*65}")
print("  Phase 5 總結")
print(f"{'='*65}")
for name, g in zip(gate_names, all_gates):
    print(f"  {'✓' if g else '✗'} {name}")

print(f"\n  {'✅ 全部通過 → 可進入 Paper Trading' if all_pass else '❌ 有項目未通過'}")

# 最終改善對比
print(f"\n{'─'*65}")
print("  優化前後完整對比（OOS 2025-01 ~ 2026-04）")
print(f"{'─'*65}")
print(f"  {'指標':<15} {'優化前':>12} {'優化後':>12} {'改善':>10}")
print(f"  {'─'*55}")
rows = [
    ('WR',      '54.7%', f'{base_wr:.1f}%',     f'+{base_wr-54.7:.1f}pp'),
    ('PF',      '1.618', f'{base_pf:.3f}',       f'+{base_pf-1.618:.3f}'),
    ('淨利TWD', '+25,849', f'{base_net:+,.0f}',  f'{base_net-25849:+,.0f}'),
    ('MaxDD',   '5.18%',f'{base_mdd:.2f}%',      f'{base_mdd-5.18:+.2f}pp'),
    ('ML',      '使用中','停用',                  '簡化'),
    ('trail_dist','1.25×ATR','0.30×ATR',          '已修正'),
    ('max_sl_pts','無限制','120pts上限',           '已修正'),
]
for r in rows:
    print(f"  {r[0]:<15} {r[1]:>12} {r[2]:>12} {r[3]:>10}")

print(f"\n{'='*65}")
print("  修改檔案清單")
print(f"{'─'*65}")
for f in ['strategy/orb.py', 'optimizer/ml/labels_orb.py', 'core/engine.py']:
    print(f"  ✓ {f}")
print(f"{'='*65}")
