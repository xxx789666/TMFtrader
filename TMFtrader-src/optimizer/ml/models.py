"""Stage 5: Multi-model ensemble for breakout signal filtering"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).parent.parent.parent

import numpy as np
import pandas as pd
import pickle

FEATURE_NAMES = None  # will be loaded from dataset


class BreakoutFilterModel:
    """
    Ensemble of XGBoost + LightGBM + RandomForest.
    Predicts probability that a breakout signal will succeed (win=1).
    Trained on multi-instrument data; applied to TMF.
    """

    def __init__(self, use_gpu=True, random_state=42):
        self.use_gpu = use_gpu
        self.random_state = random_state
        self.models = {}
        self.weights = {"xgb": 0.4, "lgb": 0.4, "rf": 0.2}
        self.feature_names = None
        self.threshold = 0.5  # default filter threshold

    def _make_xgb(self, params=None):
        """XGBClassifier with GPU if available"""
        defaults = {
            "n_estimators": 300,
            "max_depth": 6,
            "learning_rate": 0.05,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "min_child_weight": 5,
            "reg_alpha": 0.1,
            "reg_lambda": 1.0,
            "eval_metric": "logloss",
            "random_state": self.random_state,
            "device": "cuda" if self.use_gpu else "cpu",
        }
        if params:
            defaults.update(params)
        from xgboost import XGBClassifier
        return XGBClassifier(**defaults)

    def _make_lgb(self, params=None):
        """LGBMClassifier with GPU if available"""
        defaults = {
            "n_estimators": 300,
            "num_leaves": 31,
            "learning_rate": 0.05,
            "subsample": 0.8,
            "colsample_bytree": 0.8,
            "min_child_samples": 10,
            "reg_alpha": 0.1,
            "reg_lambda": 1.0,
            "device": "gpu" if self.use_gpu else "cpu",
            "random_state": self.random_state,
            "verbose": -1,
        }
        if params:
            defaults.update(params)
        from lightgbm import LGBMClassifier
        return LGBMClassifier(**defaults)

    def _make_rf(self, params=None):
        """RandomForestClassifier"""
        defaults = {
            "n_estimators": 200,
            "max_depth": 8,
            "min_samples_leaf": 5,
            "max_features": "sqrt",
            "random_state": self.random_state,
            "n_jobs": -1,
        }
        if params:
            defaults.update(params)
        from sklearn.ensemble import RandomForestClassifier
        return RandomForestClassifier(**defaults)

    def fit(self, X_train, y_train, X_val=None, y_val=None, params=None,
            sample_weight=None):
        """
        Train all models.
        params: dict with keys 'xgb', 'lgb', 'rf' for per-model overrides.
        sample_weight: optional per-sample weights array aligned with X_train rows.
        """
        params = params or {}
        self.feature_names = list(X_train.columns) if hasattr(X_train, "columns") else None

        makers = [
            ("xgb", self._make_xgb),
            ("lgb", self._make_lgb),
            ("rf",  self._make_rf),
        ]
        for name, maker in makers:
            print(f"  Training {name}...")
            model = maker(params.get(name))
            fit_kwargs = {}
            if X_val is not None and name in ("xgb", "lgb"):
                fit_kwargs["eval_set"] = [(X_val, y_val)]
                if name == "xgb":
                    fit_kwargs["verbose"] = False
            if sample_weight is not None:
                fit_kwargs["sample_weight"] = sample_weight
            if fit_kwargs:
                model.fit(X_train, y_train, **fit_kwargs)
            else:
                model.fit(X_train, y_train)
            self.models[name] = model
            print(f"    {name} done.")
        return self

    def predict_proba(self, X) -> np.ndarray:
        """Weighted ensemble probability. Returns array of P(win=1)."""
        probs = []
        total_w = 0.0
        for name, model in self.models.items():
            w = self.weights.get(name, 1.0)
            p = model.predict_proba(X)[:, 1]
            probs.append(p * w)
            total_w += w
        return np.array(probs).sum(axis=0) / total_w

    def predict(self, X, threshold=None) -> np.ndarray:
        """Binary prediction using threshold."""
        t = threshold if threshold is not None else self.threshold
        return (self.predict_proba(X) >= t).astype(int)

    def feature_importance(self) -> pd.DataFrame:
        """Aggregate normalised feature importance across models."""
        records = []
        feature_names = self.feature_names or []

        for name, model in self.models.items():
            if hasattr(model, "feature_importances_"):
                imp = model.feature_importances_
                total = imp.sum()
                if total > 0:
                    imp = imp / total
                for fname, val in zip(feature_names, imp):
                    records.append({"model": name, "feature": fname, "importance": val})

        if not records:
            return pd.DataFrame(columns=["feature", "importance"])

        df = pd.DataFrame(records)
        # Weighted mean across models
        weight_map = self.weights
        df["w"] = df["model"].map(weight_map).fillna(1.0)
        df["wi"] = df["importance"] * df["w"]
        agg = (
            df.groupby("feature")
            .apply(lambda g: g["wi"].sum() / g["w"].sum(), include_groups=False)
            .reset_index()
        )
        agg.columns = ["feature", "importance"]
        return agg.sort_values("importance", ascending=False).reset_index(drop=True)

    def save(self, path):
        """Save model to pickle."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump(self, f)
        print(f"  Model saved to {path}")

    @classmethod
    def load(cls, path):
        with open(path, "rb") as f:
            return pickle.load(f)


# ---------------------------------------------------------------------------
# Evaluation helpers
# ---------------------------------------------------------------------------

def _compute_pf(r_multiples: np.ndarray) -> float:
    """Profit factor from r-multiples."""
    wins = r_multiples[r_multiples > 0].sum()
    losses = abs(r_multiples[r_multiples < 0].sum())
    if losses == 0:
        return float("inf") if wins > 0 else 1.0
    return wins / losses


def _compute_max_drawdown(r_multiples: np.ndarray) -> float:
    """Max drawdown as a fraction of peak cumulative return."""
    cum = np.cumsum(r_multiples)
    peak = np.maximum.accumulate(cum)
    dd = (cum - peak)
    return float(-dd.min()) if len(dd) > 0 else 0.0


def evaluate_model(model, X_test, y_test, trades_df=None, threshold=0.5) -> dict:
    """
    Evaluate model performance.

    trades_df: optional DataFrame aligned with X_test rows that contains
               'r_multiple', 'pnl_pct', 'win', 'entry_time' columns.

    Returns metrics dict including:
      auc, accuracy, precision, recall, f1,
      filtered_wr, filtered_pf, filtered_n, filter_rate, lift,
      max_drawdown (filtered), baseline_wr, baseline_pf
    """
    from sklearn.metrics import (
        roc_auc_score, accuracy_score, precision_score,
        recall_score, f1_score,
    )

    proba = model.predict_proba(X_test)
    preds = (proba >= threshold).astype(int)

    metrics = {
        "auc":       float(roc_auc_score(y_test, proba)),
        "accuracy":  float(accuracy_score(y_test, preds)),
        "precision": float(precision_score(y_test, preds, zero_division=0)),
        "recall":    float(recall_score(y_test, preds, zero_division=0)),
        "f1":        float(f1_score(y_test, preds, zero_division=0)),
        "threshold": threshold,
        "n_test":    len(y_test),
    }

    # Trade-level metrics (requires trades_df)
    if trades_df is not None and len(trades_df) == len(y_test):
        tdf = trades_df.copy().reset_index(drop=True)
        mask = preds.astype(bool)

        baseline_wr = float(tdf["win"].mean()) if "win" in tdf.columns else float(y_test.mean())
        baseline_r  = tdf["r_multiple"].values if "r_multiple" in tdf.columns else np.zeros(len(tdf))
        baseline_pf = _compute_pf(baseline_r)

        filtered_tdf = tdf[mask]
        filtered_n   = int(mask.sum())
        filter_rate  = float(filtered_n / len(tdf)) if len(tdf) > 0 else 0.0

        if filtered_n > 0:
            filtered_r  = filtered_tdf["r_multiple"].values if "r_multiple" in filtered_tdf.columns else np.zeros(filtered_n)
            filtered_wr = float(filtered_tdf["win"].mean()) if "win" in filtered_tdf.columns else float(y_test[mask].mean())
            filtered_pf = _compute_pf(filtered_r)
            max_dd      = _compute_max_drawdown(filtered_r)
            lift        = filtered_wr / baseline_wr if baseline_wr > 0 else 1.0
        else:
            filtered_r  = np.array([])
            filtered_wr = 0.0
            filtered_pf = 0.0
            max_dd      = 0.0
            lift        = 0.0

        metrics.update({
            "baseline_wr":  baseline_wr,
            "baseline_pf":  baseline_pf,
            "filtered_wr":  filtered_wr,
            "filtered_pf":  filtered_pf,
            "filtered_n":   filtered_n,
            "filter_rate":  filter_rate,
            "lift":         lift,
            "max_drawdown": max_dd,
        })

    return metrics


# ---------------------------------------------------------------------------
# Main training entry point
# ---------------------------------------------------------------------------

def _load_dataset(dataset_path):
    dataset_path = Path(dataset_path)
    if not dataset_path.exists():
        print(f"[ERROR] Dataset not found: {dataset_path}")
        return None
    print(f"Loading dataset from {dataset_path} ...")
    df = pd.read_parquet(dataset_path)
    print(f"  Shape: {df.shape}")
    print(f"  Columns: {list(df.columns)[:10]} ... ({len(df.columns)} total)")
    return df


def _get_feature_cols(df: pd.DataFrame) -> list:
    return [c for c in df.columns if c.startswith("f_")]


def train_and_eval(
    dataset_path,
    results_dir,
    test_start="2025-07-01",
    use_cv=True,
) -> "tuple[BreakoutFilterModel, list] | tuple[None, None]":
    """
    Main training function:
      1. Load ml_dataset.parquet
      2. Split train/test by time (test_start = OOS period)
      3. Feature selection via quick XGBoost importance (top 70)
      4. Train ensemble on train set using selected features
      5. Evaluate on test set
      6. Walk-forward CV evaluation
      7. Print comparison: unfiltered vs filtered performance
      8. Save model to results_dir/breakout_filter.pkl
    Returns (trained model, selected feature list).
    """
    df = _load_dataset(dataset_path)
    if df is None:
        return None, None

    results_dir = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)

    # ---- Filter low-quality instruments from training ----
    # XRP and SOL have negative avg_r — noise that hurts model generalization
    # QQQM/IVV replace them with higher-quality index-correlated signals
    EXCLUDE_INSTRUMENTS = {"XRPUSDT", "SOLUSDT"}
    if "instrument" in df.columns:
        before = len(df)
        df = df[~df["instrument"].isin(EXCLUDE_INSTRUMENTS)].copy()
        print(f"  Filtered {EXCLUDE_INSTRUMENTS}: {before} → {len(df)} samples")

    # ---- Feature columns (all f_* and fd_*) ----
    feature_cols_all = [c for c in df.columns if c.startswith(('f_', 'fd_'))]
    if not feature_cols_all:
        print("[ERROR] No feature columns (f_* / fd_*) found in dataset.")
        return None, None
    print(f"  Feature columns (all): {len(feature_cols_all)}")

    global FEATURE_NAMES
    FEATURE_NAMES = feature_cols_all

    # ---- Target ----
    if "win" not in df.columns:
        print("[ERROR] 'win' column not found in dataset.")
        return None, None

    # ---- Time split ----
    time_col = "entry_time"
    if time_col not in df.columns:
        print(f"[ERROR] '{time_col}' column not found.")
        return None, None

    df[time_col] = pd.to_datetime(df[time_col])
    test_cutoff  = pd.Timestamp(test_start)
    train_df = df[df[time_col] < test_cutoff].copy()
    test_df  = df[df[time_col] >= test_cutoff].copy()

    print(f"\nTrain samples : {len(train_df)}")
    print(f"Test  samples : {len(test_df)}")

    if len(train_df) < 50:
        print("[WARN] Very few training samples. Results may be unreliable.")

    # ---- Instrument weight map (defined here for reuse in feature selection + training) ----
    # Higher weight = model pays more attention to this instrument's breakout patterns.
    # Guiding principle: instruments structurally similar to TMF (leveraged ETF, Nasdaq-linked)
    # get top weight; non-leveraged index ETFs secondary; crypto = weak auxiliary regulariser.
    INSTRUMENT_WEIGHTS = {
        # Leveraged ETFs — same product class as TMF (amplified momentum, same session)
        "SPXL":   8.0,   # S&P 500 3x leveraged ETF
        "TECL":   7.0,   # Technology sector 3x — Nasdaq-correlated
        "TQQQ":   7.0,   # Nasdaq 100 3x — closest structural match to TMF
        # Non-leveraged index ETFs
        "QQQM":   4.0,   # Nasdaq 100 ETF
        "IVV":    4.0,   # S&P 500 ETF
        # Sector ETFs (Nasdaq-correlated)
        "XLK":    4.0,   # S&P 500 IT sector
        "SOXX":   3.0,   # Semiconductor (SOX proxy)
        "IBB":    2.5,   # Nasdaq Biotech (NBI proxy)
        "FNGS":   2.5,   # NYSE FANG+ ETF
        # Index CFD
        "NAS100": 4.0,   # Nasdaq 100 CFD (NDX) — identical to QQQM in behaviour
        # TW instruments
        "TMF":    3.0,   # deployment target; low weight because n is small
        "TX":     2.0,   # same session as TMF
    }
    DEFAULT_WEIGHT = 0.5  # BTC — auxiliary only

    # ---- Feature selection (weighted by instrument quality) ----
    # Use same sample weights as training so feature selection
    # prioritises TMF-relevant features, not just crypto patterns.
    print("\nRunning feature selection (weighted XGBoost importance)...")
    from xgboost import XGBClassifier as _XGBq

    X_all = train_df[feature_cols_all].fillna(0).values.astype('float32')
    y_all = train_df['win'].astype(int).values
    sw_all = (
        train_df["instrument"].map(lambda x: INSTRUMENT_WEIGHTS.get(x, DEFAULT_WEIGHT)).values.astype(float)
        if "instrument" in train_df.columns else None
    )

    _quick = _XGBq(n_estimators=100, max_depth=4, random_state=42, device='cpu', verbosity=0)
    _quick.fit(X_all, y_all, sample_weight=sw_all)
    importances = pd.Series(_quick.feature_importances_, index=feature_cols_all)
    importances = importances.sort_values(ascending=False)

    # Keep top 30 features (or all if fewer exist)
    # Rationale: 942 train samples / 30 features = 1:31 ratio (safer than 1:13.5 at n=70)
    N_SELECT = min(30, len(feature_cols_all))
    feature_cols = importances.head(N_SELECT).index.tolist()
    print(f"  Selected {len(feature_cols)} / {len(feature_cols_all)} features")
    print(f"  Top 5: {feature_cols[:5]}")

    # Save selected feature list
    selected_path = results_dir / "selected_features.txt"
    selected_path.write_text('\n'.join(feature_cols))
    print(f"  Saved to {selected_path}")

    FEATURE_NAMES = feature_cols

    X_train = train_df[feature_cols].fillna(0)
    y_train = train_df["win"].astype(int)
    X_test  = test_df[feature_cols].fillna(0)
    y_test  = test_df["win"].astype(int)

    # ---- Sample weights: upweight Nasdaq-correlated instruments ----
    # Without weighting, BTC samples (largest pool) dominate training and the model
    # learns crypto distributions instead of Nasdaq breakout patterns.
    if "instrument" in train_df.columns:
        sw_train = train_df["instrument"].map(
            lambda x: INSTRUMENT_WEIGHTS.get(x, DEFAULT_WEIGHT)
        ).values.astype(float)
        _nasdaq_set = {"SPXL","TECL","TQQQ","QQQM","IVV","XLK","SOXX","IBB","FNGS","NAS100","TMF","TX"}
        idx_mask = train_df['instrument'].isin(_nasdaq_set)
        print(f"  Sample weights applied (effective Nasdaq/TW share: {sw_train[idx_mask].sum()/sw_train.sum()*100:.1f}%)")
    else:
        sw_train = None

    # Optional: use a small validation slice from training set
    val_split = int(len(X_train) * 0.85)
    X_val = X_train.iloc[val_split:]
    y_val = y_train.iloc[val_split:]
    X_tr  = X_train.iloc[:val_split]
    y_tr  = y_train.iloc[:val_split]
    sw_tr = sw_train[:val_split] if sw_train is not None else None

    # ---- Train ----
    print("\nTraining ensemble...")
    model = BreakoutFilterModel(use_gpu=True)
    model.fit(X_tr, y_tr, X_val=X_val, y_val=y_val, sample_weight=sw_tr)

    # ---- Train AUC (to measure overfitting gap) ----
    from sklearn.metrics import roc_auc_score as _roc_auc_tr
    train_proba = model.predict_proba(X_train.values)
    train_auc = float(_roc_auc_tr(y_train.values, train_proba))
    print(f"\n  Train AUC : {train_auc:.4f}  (n={len(y_train)})")

    # ---- Evaluate (all instruments) ----
    print("\n--- All-instrument test set ---")
    metrics_all = evaluate_model(model, X_test, y_test, trades_df=test_df, threshold=0.5)
    print(f"  Test  AUC : {metrics_all.get('auc', 0):.4f}  (n={len(y_test)})")
    print(f"  Overfit gap (Train-Test AUC): {train_auc - metrics_all.get('auc', 0):.4f}")
    _print_metrics(metrics_all)

    # ---- Evaluate TMF only ----
    if "instrument" in test_df.columns:
        tmf_mask = test_df["instrument"] == "TMF"
        tmf_df = test_df[tmf_mask].reset_index(drop=True)
        if len(tmf_df) > 0:
            X_tmf = tmf_df[feature_cols].fillna(0)
            y_tmf = tmf_df["win"].astype(int)
            print("\n--- TMF only test set ---")
            metrics_tmf = evaluate_model(model, X_tmf, y_tmf, trades_df=tmf_df, threshold=0.5)
            _print_metrics(metrics_tmf)
        else:
            print("\n[WARN] No TMF rows in test set.")

    # ---- Walk-Forward CV ----
    print("\n--- Walk-Forward CV (12-month train, 3-month test) ---")
    from optimizer.ml.cv import WalkForwardCV
    from sklearn.metrics import roc_auc_score as _roc_auc

    wf = WalkForwardCV(train_months=12, test_months=3, step_months=3, min_train_samples=20)
    all_data = pd.concat([train_df, test_df]).sort_values('entry_time').reset_index(drop=True)
    splits = list(wf.split(all_data, time_col='entry_time'))

    if splits:
        fold_aucs = []
        tmf_fold_wrs = []   # TMF-specific per-fold win-rate improvement
        for tr_df, te_df in splits:
            if len(tr_df) < 10 or len(te_df) < 3:
                continue
            # Build sample weights for WF fold
            if "instrument" in tr_df.columns:
                sw_wf = tr_df["instrument"].map(
                    lambda x: INSTRUMENT_WEIGHTS.get(x, DEFAULT_WEIGHT)
                ).values.astype(float)
            else:
                sw_wf = None

            # ---- Nested feature selection per fold (prevents selection bias) ----
            # Feature selection must use only the fold's train data, not future folds.
            fold_feature_cols_all = [c for c in tr_df.columns if c.startswith(('f_', 'fd_'))]
            from xgboost import XGBClassifier as _XGBfs
            _fs_model = _XGBfs(n_estimators=100, max_depth=4, random_state=42, device='cpu', verbosity=0)
            _fs_model.fit(
                tr_df[fold_feature_cols_all].fillna(0).values.astype('float32'),
                tr_df['win'].astype(int).values,
                sample_weight=sw_wf,
            )
            _fs_imp = pd.Series(_fs_model.feature_importances_, index=fold_feature_cols_all)
            fold_feature_cols = _fs_imp.nlargest(min(70, len(fold_feature_cols_all))).index.tolist()

            X_tr_wf = tr_df[fold_feature_cols].fillna(0).values.astype('float32')
            y_tr_wf = tr_df['win'].astype(int).values
            X_te_wf = te_df[fold_feature_cols].fillna(0).values.astype('float32')
            y_te_wf = te_df['win'].astype(int).values

            _wf_model = BreakoutFilterModel(use_gpu=False)  # CPU for quick WF
            _wf_model.feature_names = fold_feature_cols
            _wf_model.fit(X_tr_wf, y_tr_wf,
                          params={"xgb": {"n_estimators": 100}, "lgb": {"n_estimators": 100}},
                          sample_weight=sw_wf)
            proba = _wf_model.predict_proba(X_te_wf)

            try:
                auc = _roc_auc(y_te_wf, proba)
                fold_aucs.append(auc)
            except Exception:
                pass

            # Primary validation: US index instruments (SPXL/USTEC/US500/QQQM/IVV)
            # These have sufficient sample size for statistically valid WR measurement.
            # TMF is still tracked separately as the final deployment target.
            PRIMARY_INDEX = {"SPXL", "TECL", "QQQM", "IVV"}
            if "instrument" in te_df.columns:
                idx_mask_wf = te_df["instrument"].isin(PRIMARY_INDEX).values
                if idx_mask_wf.sum() >= 5:   # need ≥5 index trades in fold
                    preds_wf = (proba >= 0.5).astype(bool)
                    idx_preds = preds_wf[idx_mask_wf]
                    idx_y = y_te_wf[idx_mask_wf]
                    if idx_preds.sum() > 0:
                        tmf_fold_wrs.append(float(idx_y[idx_preds].mean()))

                # TMF tracking (informational only — too few samples for Gate)
                tmf_mask_wf = (te_df["instrument"] == "TMF").values
                if tmf_mask_wf.sum() >= 2:
                    preds_wf = (proba >= 0.5).astype(bool)
                    tmf_preds_wf = preds_wf[tmf_mask_wf]
                    tmf_y_wf = y_te_wf[tmf_mask_wf]
                    if tmf_preds_wf.sum() > 0:
                        print(f"    [TMF info] fold TMF filtered WR={tmf_y_wf[tmf_preds_wf].mean():.3f} "
                              f"n={tmf_preds_wf.sum()}")

        if fold_aucs:
            print(f"  WF folds: {len(fold_aucs)}  AUC mean={np.mean(fold_aucs):.4f}  std={np.std(fold_aucs):.4f}")
            print(f"  Per-fold AUC: {[f'{a:.3f}' for a in fold_aucs]}")

        if tmf_fold_wrs:
            idx_wf_wr = float(np.mean(tmf_fold_wrs))
            print(f"  Index WF filtered WR mean={idx_wf_wr:.3f}  "
                  f"(SPXL/TECL/QQQM/IVV, from {len(tmf_fold_wrs)} folds with ≥5 trades)")

        # ---- Gate: primary = US index OOS WR; secondary = cross-instrument AUC ----
        # SPXL/USTEC/US500 have enough data for statistically robust WF validation.
        # AUC threshold raised to 0.50 (stricter) because we now have far more samples.
        idx_gate  = len(tmf_fold_wrs) >= 3 and np.mean(tmf_fold_wrs) >= 0.60
        auc_gate  = len(fold_aucs) > 0 and np.mean(fold_aucs) >= 0.50
        gate_pass = idx_gate and auc_gate
        print(f"\n  Stage 4/5 Gate:")
        print(f"    Index WF WR ≥ 0.60  → {'PASS' if idx_gate  else 'WARN'}  ({np.mean(tmf_fold_wrs):.3f} over {len(tmf_fold_wrs)} folds)" if tmf_fold_wrs else f"    Index WF WR ≥ 0.60  → WARN (no qualifying folds)")
        print(f"    WF AUC ≥ 0.50       → {'PASS' if auc_gate  else 'WARN'}  ({np.mean(fold_aucs):.4f})" if fold_aucs else f"    WF AUC ≥ 0.50       → WARN (no folds)")
        print(f"  Overall: {'PASS' if gate_pass else 'WARN'}")
    else:
        print("  (insufficient data for walk-forward splits)")

    # ---- Feature importance ----
    fi = model.feature_importance()
    if not fi.empty:
        fi_path = results_dir / "feature_importance.csv"
        fi.to_csv(fi_path, index=False)
        print(f"\nTop 10 features:\n{fi.head(10).to_string(index=False)}")
        print(f"Feature importance saved to {fi_path}")

    # ---- Save model ----
    model_path = results_dir / "breakout_filter.pkl"
    model.save(model_path)

    return model, feature_cols


def _print_metrics(m: dict):
    print(f"  AUC={m.get('auc', 0):.4f}  Acc={m.get('accuracy', 0):.3f}  "
          f"P={m.get('precision', 0):.3f}  R={m.get('recall', 0):.3f}  "
          f"F1={m.get('f1', 0):.3f}")
    if "baseline_wr" in m:
        print(f"  Baseline  WR={m['baseline_wr']:.3f}  PF={m['baseline_pf']:.2f}  N={m['n_test']}")
        print(f"  Filtered  WR={m['filtered_wr']:.3f}  PF={m['filtered_pf']:.2f}  "
              f"N={m['filtered_n']}  keep={m['filter_rate']*100:.1f}%  "
              f"lift={m['lift']:.2f}  maxDD={m.get('max_drawdown', 0):.2f}R")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    model, feature_cols = train_and_eval(
        ROOT / "data" / "historical" / "ml" / "ml_dataset.parquet",
        ROOT / "optimizer" / "results",
    )
