# ORB B2 Volume Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `max_breakout_vol_ratio=2.0` filter to `labels_orb.py`, retrain B2 ML model, and deploy to `deployed_strategies/tmf_orb_night/`.

**Architecture:** Single new parameter in `_MXF_ORB_CFG` blocks high-volume false breakouts (stop_loss vol_ratio=2.74 vs trail_stop=1.99). Pipeline runs Stage 3→5→6→7 in sequence using existing `models.py`/`optuna_search.py`/`robustness.py` called via a new orchestration script. B2 artifacts are renamed from `breakout_filter.pkl` → `orb_filter_b2.pkl` to coexist with B1.

**Tech Stack:** Python 3.11, pandas, XGBoost + LightGBM + RandomForest (existing models.py ensemble), `optimizer/results_orb/` for artifacts.

**Spec:** `docs/superpowers/specs/2026-04-18-orb-b2-vol-filter-design.md`

---

## File Map

| Action | File |
|--------|------|
| Modify | `optimizer/ml/labels_orb.py` — add param + filter logic |
| Create | `scripts/run_orb_pipeline_b2.py` — B2 pipeline orchestrator |
| Generated | `optimizer/results_orb/orb_filter_b2.pkl` |
| Generated | `optimizer/results_orb/selected_features_b2.txt` |
| Generated | `optimizer/results_orb/best_params_b2.json` |
| Generated | `optimizer/results_orb/robustness_report_b2.md` |
| Copy | `deployed_strategies/tmf_orb_night/orb_filter_b2.pkl` |
| Copy | `deployed_strategies/tmf_orb_night/selected_features_b2.txt` |
| Copy | `deployed_strategies/tmf_orb_night/best_params_b2.json` |
| Modify | `deployed_strategies/tmf_orb_night/策略說明.md` |
| Modify | `deployed_strategies/tmf_orb_night/回測數據比對.md` |
| Modify | `deployed_strategies/tmf_orb_night/ML過濾器訓練報告.md` |
| Modify | `deployed_strategies/tmf_orb_night/_backtest_data.json` |
| Modify | `scripts/paper_night_orb.py` — update model path to B2 |

---

## Task 1: Add `max_breakout_vol_ratio` to labels_orb.py

**Files:**
- Modify: `optimizer/ml/labels_orb.py`

- [ ] **Step 1: Add param to `_MXF_ORB_CFG`**

In `labels_orb.py` at line 62–66, after `"max_orb_width_atr": 5.0,` add:

```python
"max_breakout_vol_ratio": 2.0,   # B2：突破量過濾（高量假突破排除）
# stop_loss trades avg vol_ratio=2.74，trail_stop=1.99，門檻 2.0 最佳
```

`_US_ORB_CFG` 不加此參數（只針對 TMF 夜盤）。

- [ ] **Step 2: Read param in `detect_and_label_orb()`**

In `detect_and_label_orb()` at line 269 area (where other cfg params are read), add:

```python
max_breakout_vol_ratio = cfg.get("max_breakout_vol_ratio", None)  # B2
```

- [ ] **Step 3: Add filter logic at LONG breakout detection**

In the LONG breakout block (after `if close_val > orb_high:`, around line 484), **before** setting `in_trade = True`, add:

```python
# B2: 突破量過濾 — 高量突破為假突破（量縮才是真突破）
if max_breakout_vol_ratio is not None:
    vol_ratio_val = 1.0  # 預設不過濾
    if ts in feat_lookup.index and "f_vol_ratio" in feat_lookup.columns:
        v = feat_lookup.at[ts, "f_vol_ratio"]
        if pd.notna(v):
            vol_ratio_val = float(v)
    else:
        pos = feat_lookup.index.searchsorted(ts)
        if 0 < pos <= len(feat_lookup) and "f_vol_ratio" in feat_lookup.columns:
            v = feat_lookup.iloc[pos - 1].get("f_vol_ratio", 1.0)
            if pd.notna(v):
                vol_ratio_val = float(v)
    if vol_ratio_val > max_breakout_vol_ratio:
        session_trade_done = True
        continue
```

- [ ] **Step 4: Add identical filter logic at SHORT breakout detection**

In the SHORT breakout block (after `elif close_val < orb_low:`, around line 510), add the identical vol_ratio check block before `in_trade = True`.

- [ ] **Step 5: Verify manually**

Run quick check:
```bash
cd C:/Users/xx/Desktop/永豐-自動化交易/TMFtrader-src
python -c "
from optimizer.ml.labels_orb import _MXF_ORB_CFG
print('max_breakout_vol_ratio:', _MXF_ORB_CFG.get('max_breakout_vol_ratio'))
assert _MXF_ORB_CFG['max_breakout_vol_ratio'] == 2.0
print('OK')
"
```

Expected output: `max_breakout_vol_ratio: 2.0` then `OK`

---

## Task 2: Re-run Stage 3 (Labels)

**Files:**
- Run: `optimizer/ml/labels_orb.py`
- Output: `data/historical/ml/ml_orb_dataset.parquet`

- [ ] **Step 1: Run labels_orb.py**

```bash
cd C:/Users/xx/Desktop/永豐-自動化交易/TMFtrader-src
python optimizer/ml/labels_orb.py
```

Expected output includes:
```
[Stage 3 ORB] Processing TMF ...
  [TMF] N trades | win_rate=~55% | avg_R=+0.1xx
[Stage 3 ORB] Pooled dataset: ~1600-1900 trades ...
  Overall win_rate=~51-52%
```

- [ ] **Step 2: Verify B2 dataset statistics**

```bash
python -c "
import pandas as pd
df = pd.read_parquet('data/historical/ml/ml_orb_dataset.parquet')
tmf = df[df['instrument']=='TMF']
print(f'Total: {len(df)}, TMF: {len(tmf)}, TMF WR: {tmf[\"win\"].mean():.1%}')
assert tmf['win'].mean() > 0.53, f'TMF WR {tmf[\"win\"].mean():.1%} < 53% — filter may not be applied'
print('Gate PASS')
"
```

Expected: `TMF WR ~55.6%`, `Gate PASS`

---

## Task 3: Create B2 Pipeline Orchestration Script

**Files:**
- Create: `scripts/run_orb_pipeline_b2.py`

- [ ] **Step 1: Write the script**

```python
"""
B2 ORB ML Pipeline Orchestrator — Stages 5→7
Trains B2 model on ml_orb_dataset.parquet (already generated by labels_orb.py).
Saves artifacts as orb_filter_b2.pkl / selected_features_b2.txt / best_params_b2.json.
"""
import sys, shutil
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

ROOT        = Path(__file__).parent.parent
DATA_DIR    = ROOT / "data" / "historical" / "ml"
RESULTS_ORB = ROOT / "optimizer" / "results_orb"
DATASET     = DATA_DIR / "ml_orb_dataset.parquet"

def stage5_train():
    print("\n=== Stage 5: 訓練 B2 模型 ===")
    from optimizer.ml.models import train_and_eval
    model, feats = train_and_eval(
        dataset_path=DATASET,
        results_dir=RESULTS_ORB,
        test_start="2025-07-01",
        use_cv=True,
    )
    # Rename output files → b2 suffix
    for src, dst in [
        ("breakout_filter.pkl",  "orb_filter_b2.pkl"),
        ("selected_features.txt","selected_features_b2.txt"),
        ("feature_importance.csv","feature_importance_b2.csv"),
    ]:
        src_p = RESULTS_ORB / src
        dst_p = RESULTS_ORB / dst
        if src_p.exists():
            shutil.copy2(src_p, dst_p)
            print(f"  Saved: {dst_p.name}")
    return model

def stage6_optuna(n_trials=300):
    print("\n=== Stage 6: Optuna B2 ===")
    from optimizer.ml.optuna_search import run_optuna_search
    study = run_optuna_search(
        dataset_path=DATASET,
        results_dir=RESULTS_ORB,
        n_trials=n_trials,
        test_start="2025-07-01",
    )
    # Rename
    for src, dst in [
        ("best_params.json", "best_params_b2.json"),
        ("pareto_front.csv", "pareto_front_b2.csv"),
    ]:
        src_p = RESULTS_ORB / src
        dst_p = RESULTS_ORB / dst
        if src_p.exists():
            shutil.copy2(src_p, dst_p)
            print(f"  Saved: {dst_p.name}")
    return study

def stage7_robustness():
    print("\n=== Stage 7: 穩健性 B2 ===")
    from optimizer.ml.robustness import run_all_robustness_tests
    result = run_all_robustness_tests(
        dataset_path=DATASET,
        results_dir=RESULTS_ORB,
        model_path=RESULTS_ORB / "orb_filter_b2.pkl",
        test_start="2025-07-01",
    )
    # Rename report
    src = RESULTS_ORB / "robustness_report.md"
    dst = RESULTS_ORB / "robustness_report_b2.md"
    if src.exists():
        shutil.copy2(src, dst)
        print(f"  Saved: {dst.name}")
    return result

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-stage", type=int, default=5)
    parser.add_argument("--trials", type=int, default=300)
    args = parser.parse_args()

    if not DATASET.exists():
        print(f"[ERROR] {DATASET} not found. Run labels_orb.py first.")
        sys.exit(1)

    if args.from_stage <= 5:
        stage5_train()
    if args.from_stage <= 6:
        stage6_optuna(args.trials)
    if args.from_stage <= 7:
        stage7_robustness()

    print("\n=== B2 Pipeline Done ===")
    print(f"Artifacts in: {RESULTS_ORB}")
    for f in ["orb_filter_b2.pkl", "selected_features_b2.txt", "best_params_b2.json", "robustness_report_b2.md"]:
        status = "✓" if (RESULTS_ORB / f).exists() else "✗ MISSING"
        print(f"  {status}  {f}")
```

---

## Task 4: Run B2 Pipeline (Stages 5→7)

- [ ] **Step 1: Run Stage 5 (model training) — takes ~5 min**

```bash
cd C:/Users/xx/Desktop/永豐-自動化交易/TMFtrader-src
python scripts/run_orb_pipeline_b2.py --from-stage 5 --trials 300
```

Watch for:
- Stage 5 output: `WF AUC mean`, `TMF test WR`
- Stage 6 output: best threshold and WR from Optuna
- Stage 7 output: 穩健性 gate result

- [ ] **Step 2: Check B2 artifacts exist**

```bash
python -c "
from pathlib import Path
results = Path('optimizer/results_orb')
for f in ['orb_filter_b2.pkl','selected_features_b2.txt','best_params_b2.json','robustness_report_b2.md']:
    p = results / f
    print(f'{'OK' if p.exists() else 'MISSING'}: {f}')
"
```

- [ ] **Step 3: Read Optuna best result**

```bash
python -c "
import json
from pathlib import Path
p = Path('optimizer/results_orb/best_params_b2.json')
if p.exists():
    d = json.load(open(p))
    print(json.dumps(d, indent=2, ensure_ascii=False))
"
```

Note the `best_threshold` value — will be used in deployment.

- [ ] **Step 4: Read robustness report gate**

```bash
python -c "
p = open('optimizer/results_orb/robustness_report_b2.md').read()
import re
passes = p.count('| PASS |')
fails  = p.count('| FAIL |')
print(f'Gate: {passes}/4 PASS, {fails}/4 FAIL')
print('DEPLOY OK' if passes >= 2 else 'WARN: gate low')
"
```

---

## Task 5: Deploy B2 Artifacts

**Files:**
- Copy to `deployed_strategies/tmf_orb_night/`

- [ ] **Step 1: Copy model files**

```bash
cd C:/Users/xx/Desktop/永豐-自動化交易/TMFtrader-src
python -c "
import shutil
from pathlib import Path
src = Path('optimizer/results_orb')
dst = Path('../deployed_strategies/tmf_orb_night')
for f in ['orb_filter_b2.pkl','selected_features_b2.txt','best_params_b2.json']:
    shutil.copy2(src/f, dst/f)
    print(f'Copied: {f}')
"
```

- [ ] **Step 2: Update `paper_night_orb.py` model path**

In `scripts/paper_night_orb.py`, find the line loading the B1 model:
```python
# Find: orb_filter_b1.pkl
# Replace with: orb_filter_b2.pkl
```

Also update the threshold if the Optuna best threshold differs from 0.45.

- [ ] **Step 3: Verify paper_night_orb.py loads B2 model**

```bash
python -c "
import sys; sys.path.insert(0,'.')
# just check import works
from scripts.paper_night_orb import OrbMLFilter
print('OrbMLFilter import OK')
" 2>&1 | head -5
```

---

## Task 6: Update Documentation

**Files:**
- Modify: `deployed_strategies/tmf_orb_night/策略說明.md`
- Modify: `deployed_strategies/tmf_orb_night/回測數據比對.md`
- Modify: `deployed_strategies/tmf_orb_night/ML過濾器訓練報告.md`
- Modify: `deployed_strategies/tmf_orb_night/_backtest_data.json`

- [ ] **Step 1: Collect B2 OOS metrics**

Run this after Task 4 to get the actual B2 OOS numbers:

```bash
cd C:/Users/xx/Desktop/永豐-自動化交易/TMFtrader-src
python -c "
import pickle, json, pandas as pd, numpy as np
from pathlib import Path

# Load B2 model + dataset
model_path = Path('optimizer/results_orb/orb_filter_b2.pkl')
dataset_path = Path('data/historical/ml/ml_orb_dataset.parquet')
feat_path = Path('optimizer/results_orb/selected_features_b2.txt')

with open(model_path, 'rb') as f:
    model = pickle.load(f)
with open(feat_path) as f:
    feat_cols = [l.strip() for l in f if l.strip()]

df = pd.read_parquet(dataset_path)
df['entry_time'] = pd.to_datetime(df['entry_time'])
oos = df[df['entry_time'] >= '2025-07-01']
tmf_oos = oos[oos['instrument']=='TMF']

# Load best threshold
bp_path = Path('optimizer/results_orb/best_params_b2.json')
best_thr = json.load(open(bp_path)).get('best_threshold', 0.45)

# Evaluate at recommended threshold (0.45)
for thr in [0.45, best_thr]:
    X = tmf_oos[feat_cols].fillna(0)
    probs = model.predict_proba(X)
    mask = probs >= thr
    filtered = tmf_oos[mask]
    n = len(filtered); months = 9.5
    wr = filtered['win'].mean() if n>0 else 0
    avg_r = filtered['r_multiple'].mean() if n>0 else 0
    print(f'threshold={thr:.3f}: N={n} N/m={n/months:.1f} WR={wr:.1%} avg_R={avg_r:.3f}')
"
```

- [ ] **Step 2: Update `_backtest_data.json`**

Add a `b2_ml_filter` block (same structure as the existing `b1_ml_filter` block) with the actual B2 OOS metrics from Step 1.

- [ ] **Step 3: Update `策略說明.md`**

In the Layer 2 section, change:
- Model file: `orb_filter_b1.pkl` → `orb_filter_b2.pkl`
- Features: `selected_features_b1.txt` → `selected_features_b2.txt`
- Add the new param: `max_breakout_vol_ratio: 2.0`
- Update OOS performance table with B2 numbers

- [ ] **Step 4: Update `ML過濾器訓練報告.md`**

Fully replace with B2 report (same structure as B1 report but with B2 numbers):
- Version: B2
- Date: 2026-04-18
- Note: B2 adds `max_breakout_vol_ratio=2.0` (高量假突破過濾)
- Training data: same instruments, new filtered labels (TMF: 225→135)
- OOS results: use numbers from Step 1
- Robustness gate: from Task 4 Step 4

- [ ] **Step 5: Update `回測數據比對.md`**

Add B2 OOS comparison table below the B1 table.

---

## Task 7: Final Verification

- [ ] **Step 1: Confirm all deployed files exist**

```bash
python -c "
from pathlib import Path
base = Path('C:/Users/xx/Desktop/永豐-自動化交易/deployed_strategies/tmf_orb_night')
files = ['orb_filter_b2.pkl','selected_features_b2.txt','best_params_b2.json']
for f in files:
    p = base / f
    print(f'{'OK' if p.exists() else 'MISSING'}: {f} ({p.stat().st_size if p.exists() else 0} bytes)')
"
```

- [ ] **Step 2: Smoke test paper_night_orb.py**

```bash
cd C:/Users/xx/Desktop/永豐-自動化交易/TMFtrader-src
python scripts/paper_night_orb.py --no-ml --help 2>&1 | head -5
```

Expected: prints help text without import error.

- [ ] **Step 3: Update `_backtest_data.json` deployment status**

Set `b2_ml_filter.deployment.status` to `"paper_trading_pending"`.

---

## Summary

After all tasks complete:

| 項目 | 值 |
|------|-----|
| 新過濾器 | max_breakout_vol_ratio=2.0 |
| TMF Layer 1 WR | 48.4% → **~55.6%** |
| 新模型 | orb_filter_b2.pkl |
| paper_night_orb.py | 已更新為 B2 |
| 文件 | 全部更新 |
