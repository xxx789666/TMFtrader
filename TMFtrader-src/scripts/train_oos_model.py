"""
Phase 0 — Step A：用 2024 年以前的資料重新訓練 OOS ML 模型
訓練期：2020-01 ~ 2024-12（不含 2025+）
產出：optimizer/results_orb/orb_filter_oos_v1.pkl
      optimizer/results_orb/selected_features_oos_v1.txt

用法：python scripts/train_oos_model.py
"""
import sys, warnings
warnings.filterwarnings('ignore')
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, '.')

from core.logger import setup_logger
setup_logger(console_level='CRITICAL')

import pandas as pd
import numpy as np
from pathlib import Path

TRAIN_CUTOFF = '2025-01-01'   # 訓練期結束（不含當天）
N_FEATURES   = 30             # 特徵數量（與 B2 一致）
OUT_MODEL    = Path('optimizer/results_orb/orb_filter_oos_v1.pkl')
OUT_FEATURES = Path('optimizer/results_orb/selected_features_oos_v1.txt')

# ─── 載入多商品 ORB dataset ───────────────────────────────────────
print("=" * 60)
print("  Phase 0 Step A：訓練 OOS 模型（訓練期 < 2025-01-01）")
print("=" * 60)

df = pd.read_parquet('data/historical/ml/ml_orb_dataset.parquet')
df['entry_time'] = pd.to_datetime(df['entry_time'])

print(f"\n原始資料：{len(df):,} 筆  ({df['instrument'].nunique()} 商品)")
print(f"期間：{df['entry_time'].min().date()} ~ {df['entry_time'].max().date()}")

# ─── 切割訓練 / 測試 ─────────────────────────────────────────────
train_df = df[df['entry_time'] < TRAIN_CUTOFF].copy()
test_df  = df[df['entry_time'] >= TRAIN_CUTOFF].copy()

print(f"\n訓練集（< {TRAIN_CUTOFF}）：{len(train_df):,} 筆")
print(f"  商品分布：{dict(train_df['instrument'].value_counts())}")
print(f"  Win率：{train_df['win'].mean()*100:.1f}%")
print(f"\n測試集（>= {TRAIN_CUTOFF}）：{len(test_df):,} 筆（參考用）")

# ─── 特徵欄位 ─────────────────────────────────────────────────────
feature_cols_all = [c for c in df.columns if c.startswith('f_')]
print(f"\n特徵總數：{len(feature_cols_all)}")

# ─── 商品權重（與原始訓練一致）───────────────────────────────────
INSTRUMENT_WEIGHTS = {
    'SPXL': 8.0, 'TECL': 7.0, 'TQQQ': 7.0,
    'QQQM': 4.0, 'IVV':  4.0, 'XLK':  4.0,
    'SOXX': 3.0, 'IBB':  2.5, 'FNGS': 2.5,
    'TMF':  3.0, 'TX':   2.0,
}
DEFAULT_WEIGHT = 0.5

sw_train = train_df['instrument'].map(
    lambda x: INSTRUMENT_WEIGHTS.get(x, DEFAULT_WEIGHT)
).values.astype(float)

nasdaq_set = {'SPXL','TECL','TQQQ','QQQM','IVV','XLK','SOXX','IBB','FNGS','TMF'}
idx_mask = train_df['instrument'].isin(nasdaq_set)
print(f"  Nasdaq/TW 樣本比重：{sw_train[idx_mask].sum()/sw_train.sum()*100:.1f}%")

# ─── 特徵選擇（XGBoost importance，訓練集）───────────────────────
print("\n特徵選擇（weighted XGBoost importance）...")
from xgboost import XGBClassifier

X_all = train_df[feature_cols_all].fillna(0).values.astype('float32')
y_all = train_df['win'].astype(int).values

_quick = XGBClassifier(n_estimators=100, max_depth=4, random_state=42,
                       device='cpu', verbosity=0)
_quick.fit(X_all, y_all, sample_weight=sw_train)
importances = pd.Series(_quick.feature_importances_, index=feature_cols_all)
importances = importances.sort_values(ascending=False)

feature_cols = importances.head(N_FEATURES).index.tolist()
print(f"  選出 {len(feature_cols)} 個特徵")
print(f"  Top 5：{feature_cols[:5]}")

OUT_FEATURES.write_text('\n'.join(feature_cols))
print(f"  已儲存：{OUT_FEATURES}")

# ─── 訓練 ensemble ───────────────────────────────────────────────
X_train = train_df[feature_cols].fillna(0)
y_train = train_df['win'].astype(int)

val_split = int(len(X_train) * 0.85)
X_val = X_train.iloc[val_split:]
y_val = y_train.iloc[val_split:]
X_tr  = X_train.iloc[:val_split]
y_tr  = y_train.iloc[:val_split]
sw_tr = sw_train[:val_split]

print(f"\n訓練 BreakoutFilterModel (XGB+LGB+RF)...")
print(f"  Train: {len(X_tr):,}  Val: {len(X_val):,}")

from optimizer.ml.models import BreakoutFilterModel
model = BreakoutFilterModel(use_gpu=True)
model.fit(X_tr, y_tr, X_val=X_val, y_val=y_val, sample_weight=sw_tr)

# ─── 評估訓練集 AUC（過擬合參考）────────────────────────────────
from sklearn.metrics import roc_auc_score
train_proba = model.predict_proba(X_train.values)
train_auc = roc_auc_score(y_train.values, train_proba)
print(f"\n  Train AUC：{train_auc:.4f}")

# ─── 評估測試集（參考）───────────────────────────────────────────
if len(test_df) > 0:
    X_test = test_df[feature_cols].fillna(0)
    y_test = test_df['win'].astype(int)
    test_proba = model.predict_proba(X_test.values)
    test_auc = roc_auc_score(y_test.values, test_proba)
    print(f"  Test  AUC：{test_auc:.4f}  (n={len(test_df)})")
    print(f"  Overfit gap：{train_auc - test_auc:.4f}")

    # TMF only
    tmf_mask = test_df['instrument'] == 'TMF'
    if tmf_mask.sum() >= 3:
        X_tmf = test_df[tmf_mask][feature_cols].fillna(0)
        y_tmf = test_df[tmf_mask]['win'].astype(int)
        tmf_proba = model.predict_proba(X_tmf.values)
        tmf_auc = roc_auc_score(y_tmf.values, tmf_proba)
        print(f"  TMF only AUC：{tmf_auc:.4f}  (n={tmf_mask.sum()})")

# ─── 儲存模型 ────────────────────────────────────────────────────
model.save(OUT_MODEL)

print(f"\n{'='*60}")
print(f"  OOS 模型訓練完成")
print(f"  模型：{OUT_MODEL}")
print(f"  特徵：{OUT_FEATURES}")
print(f"  訓練樣本：{len(train_df):,}（< {TRAIN_CUTOFF}）")
print(f"  Train AUC：{train_auc:.4f}")
print(f"{'='*60}")
