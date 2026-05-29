"""
補齊夜盤 ORB 資料（2026-04-10 後）並重新生成信號
用法：python scripts/update_night_orb_data.py
"""
import sys, warnings
warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')

import pandas as pd
import numpy as np
import pickle
from datetime import time, datetime
from pathlib import Path

ML_DIR = Path('data/historical/ml')

# ════════════════════════════════════════════════════════
# Step 1: 從 1m parquet 補 TMF_night_5m.parquet
# ════════════════════════════════════════════════════════
print("[Step 1] 補齊 TMF_night_5m.parquet ...")

df_old = pd.read_parquet(ML_DIR / 'TMF_night_5m.parquet')
last_dt = df_old['datetime'].max()
print(f"  現有最後一筆: {last_dt}")

df_1m = pd.read_parquet('data/historical/tmf_5y_1m.parquet')
df_1m['datetime'] = pd.to_datetime(df_1m['datetime'])
df_5m = (
    df_1m.set_index('datetime')
    .resample('5min')
    .agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'})
    .dropna()
    .reset_index()
)

# 夜盤時段：>= 15:00 或 <= 05:00
night_mask = df_5m['datetime'].dt.time.apply(lambda t: t >= time(15, 0) or t <= time(5, 0))
new_bars = df_5m[night_mask & (df_5m['datetime'] > last_dt)].copy()
print(f"  新增 {len(new_bars)} 根 K棒（{new_bars['datetime'].min() if len(new_bars) else 'N/A'} ~ {new_bars['datetime'].max() if len(new_bars) else 'N/A'}）")

if len(new_bars) == 0:
    print("  無新資料，退出。")
    sys.exit(0)

df_night_5m = pd.concat([df_old, new_bars], ignore_index=True)
df_night_5m.to_parquet(ML_DIR / 'TMF_night_5m.parquet', index=False)
print(f"  已儲存：TMF_night_5m.parquet（共 {len(df_night_5m):,} 根）")

# ════════════════════════════════════════════════════════
# Step 2: 重算 TMF_night_features.parquet
# ════════════════════════════════════════════════════════
print("\n[Step 2] 重算 TMF_night_features.parquet ...")
from core.gpu_indicators import precompute_all
from optimizer.ml.features import build_feature_matrix

ind = precompute_all(df_night_5m, verbose=False)
feat_df = build_feature_matrix(df_night_5m, ind, 'TMF_night')
feat_df.to_parquet(ML_DIR / 'TMF_night_features.parquet', index=False)
print(f"  已儲存：TMF_night_features.parquet（{len(feat_df):,} 行 x {len(feat_df.columns)} 欄）")

# ════════════════════════════════════════════════════════
# Step 3: 重新偵測 ORB 信號
# ════════════════════════════════════════════════════════
print("\n[Step 3] 重新偵測 ORB 信號 ...")
from optimizer.ml.labels_orb import detect_and_label_orb, INSTRUMENT_ORB_CONFIGS

cfg = INSTRUMENT_ORB_CONFIGS['TMF']
signals_df = detect_and_label_orb(df_night_5m, feat_df, 'TMF_night', cfg)
signals_df.to_parquet(ML_DIR / 'TMF_night_orb_signals.parquet', index=False)
print(f"  已儲存：TMF_night_orb_signals.parquet（{len(signals_df):,} 筆信號）")

# ════════════════════════════════════════════════════════
# Step 4: 套用 B2 過濾並確認統計
# ════════════════════════════════════════════════════════
print("\n[Step 4] 套用 B2 過濾（vol<=2.0 + ML>=0.40）...")
with open('optimizer/results_orb/orb_filter_b2.pkl', 'rb') as f:
    model = pickle.load(f)
features = [
    ln.strip()
    for ln in open('optimizer/results_orb/selected_features_b2.txt').readlines()
    if ln.strip()
]

b2_l1 = signals_df[signals_df['f_vol_ratio'] <= 2.0].copy()
probs = model.predict_proba(b2_l1[features])
b2_l1['ml_prob'] = probs
night_b2 = b2_l1[b2_l1['ml_prob'] >= 0.40].copy().sort_values('entry_time').reset_index(drop=True)

night_b2['pnl_twd'] = np.where(
    night_b2['direction'] == 1,
    night_b2['exit_price'] - night_b2['entry_price'],
    night_b2['entry_price'] - night_b2['exit_price']
) * 10

n = len(night_b2)
wins = (night_b2['pnl_twd'] > 0).sum()
gp = night_b2[night_b2['pnl_twd'] > 0]['pnl_twd'].sum()
gl = abs(night_b2[night_b2['pnl_twd'] < 0]['pnl_twd'].sum())
net = night_b2['pnl_twd'].sum()
eq = np.concatenate([[200000.], 200000 + np.cumsum(night_b2['pnl_twd'].values)])
peak = np.maximum.accumulate(eq)
max_dd = ((peak - eq) / peak * 100).max()

print(f"\n{'='*60}")
print(f"夜盤 ORB B2 更新後結果：")
print(f"  Raw 信號：{len(signals_df)} 筆")
print(f"  B2 過濾後：{n} 筆")
print(f"  WR={wins/n*100:.1f}%  PF={gp/gl:.3f}  Net={net:+,.0f}  MaxDD={max_dd:.2f}%")
print(f"  資料期間：{night_b2['entry_time'].iloc[0].strftime('%Y-%m-%d')} ~ {night_b2['entry_time'].iloc[-1].strftime('%Y-%m-%d')}")
print(f"{'='*60}")
