"""
Phase 4：MAE / MFE 出場機制分析
逐筆還原進場後每根 K 棒的路徑，計算：
  MFE = 進場後最大順向距離（點數）
  MAE = 進場後最大逆向距離（點數）
並診斷：
  - trail_stop 在 MFE 的幾%位置觸發？
  - 停利是否太早（小贏）？
  - 停損是否太晚/太寬（大輸）？
  - 最佳出場時機在哪裡？

用法：python scripts/analyze_mae_mfe.py
"""
import sys, warnings
warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')

import pandas as pd
import numpy as np
from pathlib import Path

# ─── 載入資料 ─────────────────────────────────────────────────────
df_5m = pd.read_parquet('data/historical/ml/TMF_night_5m.parquet')
df_5m['datetime'] = pd.to_datetime(df_5m['datetime'])
df_5m = df_5m.reset_index(drop=True)

df_sig = pd.read_parquet('data/historical/ml/TMF_night_orb_signals.parquet')
df_sig['entry_time'] = pd.to_datetime(df_sig['entry_time'])
df_sig['exit_time']  = pd.to_datetime(df_sig['exit_time'])

# 只分析 OOS 期（較有代表性）
OOS_START = '2025-01-01'
sig_oos = df_sig[df_sig['entry_time'] >= OOS_START].copy().reset_index(drop=True)
sig_all = df_sig.copy().reset_index(drop=True)

print("=" * 65)
print("  Phase 4：MAE/MFE 出場機制分析")
print("=" * 65)

# ─── 核心計算函數 ─────────────────────────────────────────────────
def calc_mae_mfe(signals, df_bars, label):
    records = []
    for _, row in signals.iterrows():
        i_entry = int(row['entry_bar'])
        i_exit  = int(row['exit_bar'])
        direction = int(row['direction'])
        ep = float(row['entry_price'])
        xp = float(row['exit_price'])
        sp = float(row['stop_price'])
        atr = float(row['atr_at_entry'])

        # 取進場到出場的 K 棒
        bars = df_bars.iloc[i_entry:i_exit+1]
        if len(bars) == 0:
            continue

        if direction == 1:   # LONG
            highs = bars['high'].values
            lows  = bars['low'].values
            mfe_pts = float(np.max(highs) - ep)
            mae_pts = float(ep - np.min(lows))
            exit_pts = xp - ep
        else:                # SHORT
            highs = bars['high'].values
            lows  = bars['low'].values
            mfe_pts = float(ep - np.min(lows))
            mae_pts = float(np.max(highs) - ep)
            exit_pts = ep - xp

        mfe_pts = max(mfe_pts, 0.0)
        mae_pts = max(mae_pts, 0.0)

        # trail_stop 在 MFE 的幾%位置觸發
        trail_capture = exit_pts / mfe_pts if mfe_pts > 1e-6 else 0.0

        records.append({
            'entry_time':    row['entry_time'],
            'direction':     direction,
            'exit_reason':   row['exit_reason'],
            'win':           int(row['win']),
            'ep':            ep,
            'xp':            xp,
            'sp':            sp,
            'atr':           atr,
            'sl_dist':       abs(ep - sp),
            'exit_pts':      exit_pts,
            'mfe_pts':       mfe_pts,
            'mae_pts':       mae_pts,
            'trail_capture': trail_capture,
            'bars_held':     i_exit - i_entry,
        })

    df = pd.DataFrame(records)
    return df

# ─── 計算 ────────────────────────────────────────────────────────
print(f"\n計算 OOS（{OOS_START}+，n={len(sig_oos)}）和全期（n={len(sig_all)}）MAE/MFE...")
df_oos = calc_mae_mfe(sig_oos, df_5m, 'OOS')
df_all = calc_mae_mfe(sig_all, df_5m, 'All')

# ─── 分析 1：整體 MFE/MAE 分布 ───────────────────────────────────
print(f"\n{'─'*65}")
print("  1. MFE / MAE 整體分布（OOS）")
print(f"{'─'*65}")

wins = df_oos[df_oos['win']==1]
loss = df_oos[df_oos['win']==0]

for grp, lbl in [(df_oos, '全部'), (wins, '獲利交易'), (loss, '虧損交易')]:
    n = len(grp)
    if n == 0: continue
    print(f"\n  【{lbl}（n={n}）】")
    print(f"  MFE pts：mean={grp['mfe_pts'].mean():.0f}  "
          f"median={grp['mfe_pts'].median():.0f}  "
          f"p25={grp['mfe_pts'].quantile(.25):.0f}  "
          f"p75={grp['mfe_pts'].quantile(.75):.0f}  "
          f"max={grp['mfe_pts'].max():.0f}")
    print(f"  MAE pts：mean={grp['mae_pts'].mean():.0f}  "
          f"median={grp['mae_pts'].median():.0f}  "
          f"p25={grp['mae_pts'].quantile(.25):.0f}  "
          f"p75={grp['mae_pts'].quantile(.75):.0f}  "
          f"max={grp['mae_pts'].max():.0f}")
    print(f"  出場pts：mean={grp['exit_pts'].mean():.0f}  "
          f"median={grp['exit_pts'].median():.0f}")
    if lbl != '虧損交易':
        print(f"  Trail擷取率：mean={grp['trail_capture'].mean()*100:.0f}%  "
              f"（實際出場 / MFE 峰值）")

# ─── 分析 2：trail_stop 效率 ─────────────────────────────────────
print(f"\n{'─'*65}")
print("  2. trail_stop 出場效率（OOS）")
print(f"{'─'*65}")

trail_wins = wins[wins['exit_reason']=='trail_stop']
print(f"\n  trail_stop 獲利交易（n={len(trail_wins)}）：")
print(f"  MFE 峰值   ：median={trail_wins['mfe_pts'].median():.0f}pts")
print(f"  實際出場   ：median={trail_wins['exit_pts'].median():.0f}pts")
print(f"  擷取率     ：median={trail_wins['trail_capture'].median()*100:.0f}%  "
      f"（擷取 MFE 的幾%？）")

trail_loss = loss[loss['exit_reason']=='trail_stop']
if len(trail_loss) > 0:
    print(f"\n  trail_stop 虧損交易（n={len(trail_loss)}）：")
    print(f"  MFE 峰值   ：median={trail_loss['mfe_pts'].median():.0f}pts（曾獲利多少）")
    print(f"  MAE（最大虧損）：median={trail_loss['mae_pts'].median():.0f}pts")
    print(f"  實際出場   ：median={trail_loss['exit_pts'].median():.0f}pts")

# ─── 分析 3：stop_loss 出場分析 ───────────────────────────────────
print(f"\n{'─'*65}")
print("  3. stop_loss 出場分析（OOS）")
print(f"{'─'*65}")

sl_trades = df_oos[df_oos['exit_reason']=='stop_loss']
print(f"  stop_loss 筆數：{len(sl_trades)}  "
      f"（佔 {len(sl_trades)/len(df_oos)*100:.0f}%）")
if len(sl_trades) > 0:
    print(f"  SL 距離 pts：{sl_trades['sl_dist'].values}")
    print(f"  出場虧損 pts：{sl_trades['exit_pts'].values}")
    print(f"  MFE（曾獲利）：{sl_trades['mfe_pts'].values.round(0)}")
    print(f"  在 SL 前最高獲利：mean={sl_trades['mfe_pts'].mean():.0f}  "
          f"max={sl_trades['mfe_pts'].max():.0f}")

# ─── 分析 4：最佳固定出場點搜尋 ──────────────────────────────────
print(f"\n{'─'*65}")
print("  4. 最佳固定 TP 點數搜尋（若改用固定 TP 出場）")
print(f"{'─'*65}")

def sim_fixed_tp(df, tp_pts):
    """模擬固定 TP 出場：若 MFE >= tp_pts 則以 tp 出場，否則以原始結果"""
    pnl = []
    for _, row in df.iterrows():
        if row['mfe_pts'] >= tp_pts:
            pnl.append(tp_pts)
        else:
            pnl.append(row['exit_pts'])
    arr = np.array(pnl)
    wins_n = (arr > 0).sum()
    gp = arr[arr > 0].sum()
    gl = abs(arr[arr < 0].sum())
    pf = gp / gl if gl > 0 else 999.0
    net = arr.sum()
    return wins_n, pf, net

print(f"\n  {'TP點數':>8}  {'交易數':>6}  {'WR':>6}  {'PF':>7}  {'淨利pts':>9}")
print(f"  {'─'*50}")
for tp in [20, 30, 40, 50, 60, 80, 100, 120, 150, 200]:
    w, pf, net = sim_fixed_tp(df_oos, tp)
    n = len(df_oos)
    print(f"  TP={tp:>3}pts   {n:>6}  {w/n*100:>5.1f}%  {pf:>6.3f}  {net:>+9.0f}")

# ─── 分析 5：trail_stop 延遲啟動測試 ─────────────────────────────
print(f"\n{'─'*65}")
print("  5. 改變 trail_stop 啟動門檻測試")
print(f"     現況：trail_trigger_atr=0.8（ATR≈60pts，約 48pts 就啟動追蹤）")
print(f"{'─'*65}")

def sim_trail_delay(df, min_mfe_before_trail):
    """
    改良 trail_stop：MFE 需超過 min_mfe_before_trail pts 才啟動追蹤
    啟動後，保留 MFE - trail_dist_pts 的出場點
    """
    trail_dist_pts = 50  # 追蹤距離固定 50pts（≈ ATR×0.8）
    pnl = []
    for _, row in df.iterrows():
        mfe = row['mfe_pts']
        mae = row['mae_pts']
        ep  = row['exit_pts']

        if mfe < min_mfe_before_trail:
            # trail 未啟動，用原始出場
            pnl.append(ep)
        else:
            # trail 啟動後，出場點 = MFE - trail_dist
            simulated_exit = max(mfe - trail_dist_pts, -mae)
            # 若在 trail 啟動前就先觸碰 stop_loss（MAE 判斷）
            if mae >= row['sl_dist']:
                pnl.append(-row['sl_dist'])
            else:
                pnl.append(simulated_exit)
    arr = np.array(pnl)
    wins_n = (arr > 0).sum()
    gp = arr[arr > 0].sum()
    gl = abs(arr[arr < 0].sum())
    pf = gp / gl if gl > 0 else 999.0
    return wins_n, pf, arr.sum()

print(f"\n  {'啟動門檻':>10}  {'WR':>6}  {'PF':>7}  {'淨利pts':>9}")
print(f"  {'─'*40}")
for trigger in [0, 20, 30, 40, 50, 60, 80, 100]:
    w, pf, net = sim_trail_delay(df_oos, trigger)
    n = len(df_oos)
    label = '（現況≈）' if trigger == 40 else ''
    print(f"  MFE>{trigger:>3}pts才啟動  {w/n*100:>5.1f}%  {pf:>6.3f}  {net:>+9.0f}  {label}")

# ─── 分析 6：全期 vs OOS 一致性確認 ─────────────────────────────
print(f"\n{'─'*65}")
print("  6. 全期 MAE/MFE 分布（含 2024，確認一致性）")
print(f"{'─'*65}")
print(f"\n  全期（n={len(df_all)}）：")
print(f"  MFE：mean={df_all['mfe_pts'].mean():.0f}  median={df_all['mfe_pts'].median():.0f}  "
      f"p75={df_all['mfe_pts'].quantile(.75):.0f}  max={df_all['mfe_pts'].max():.0f}")
print(f"  MAE：mean={df_all['mae_pts'].mean():.0f}  median={df_all['mae_pts'].median():.0f}  "
      f"p75={df_all['mae_pts'].quantile(.75):.0f}  max={df_all['mae_pts'].max():.0f}")
print(f"  Trail擷取率：mean={df_all['trail_capture'].mean()*100:.0f}%  "
      f"median={df_all['trail_capture'].median()*100:.0f}%")

# 出場原因分布
print(f"\n  出場原因（OOS）：{sig_oos['exit_reason'].value_counts().to_dict()}")
print(f"  出場原因（全期）：{df_sig['exit_reason'].value_counts().to_dict()}")

# ─── 關鍵結論輸出 ────────────────────────────────────────────────
print(f"\n{'='*65}")
print("  診斷結論")
print(f"{'='*65}")

avg_mfe_win  = wins['mfe_pts'].mean()
avg_exit_win = wins['exit_pts'].mean()
avg_mfe_loss = loss['mfe_pts'].mean()
avg_mae_loss = loss['mae_pts'].mean()
trail_eff    = wins['trail_capture'].mean() * 100

print(f"\n  獲利交易：平均 MFE={avg_mfe_win:.0f}pts，實際出場={avg_exit_win:.0f}pts")
print(f"           → trail_stop 僅擷取 MFE 的 {trail_eff:.0f}%")
print(f"\n  虧損交易：平均 MFE={avg_mfe_loss:.0f}pts（曾是獲利狀態）")
print(f"           → 平均 MAE={avg_mae_loss:.0f}pts（最終承受虧損）")

if trail_eff < 50:
    print(f"\n  ⚠ trail_stop 擷取率 {trail_eff:.0f}% 偏低 → 出場太早，留不住利潤")
    print(f"    建議：提高 trail_trigger_atr 或加入最小持倉時間")
if avg_mae_loss > avg_mfe_loss * 1.5:
    print(f"\n  ⚠ 虧損交易 MAE({avg_mae_loss:.0f}) >> 曾有MFE({avg_mfe_loss:.0f})")
    print(f"    建議：在 trail_stop 啟動前加 early-cut 保護")

print(f"\n{'='*65}")
