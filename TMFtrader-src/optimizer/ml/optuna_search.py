"""Stage 6: Multi-objective Optuna search for model hyperparams + filter threshold"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).parent.parent.parent

import json
import pickle
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

import optuna
optuna.logging.set_verbosity(optuna.logging.WARNING)


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------

def compute_backtest_metrics(trades_df: pd.DataFrame) -> dict:
    """
    Given a trades DataFrame with 'pnl_pct', 'r_multiple', 'win', 'entry_time',
    compute: pf, wr, max_dd, sharpe, n_trades.
    Uses r_multiple for PF computation.
    """
    if trades_df is None or len(trades_df) == 0:
        return {"pf": 0.0, "wr": 0.0, "max_dd": 0.0, "sharpe": 0.0, "n_trades": 0}

    r = trades_df["r_multiple"].values if "r_multiple" in trades_df.columns else np.zeros(len(trades_df))

    wins   = r[r > 0].sum()
    losses = abs(r[r < 0].sum())
    pf     = (wins / losses) if losses > 0 else (float("inf") if wins > 0 else 1.0)

    wr = float(trades_df["win"].mean()) if "win" in trades_df.columns else float((r > 0).mean())

    # Max drawdown on cumulative r-multiple curve
    cum  = np.cumsum(r)
    peak = np.maximum.accumulate(cum)
    dd   = cum - peak
    max_dd = float(-dd.min()) if len(dd) > 0 else 0.0

    # Sharpe (annualised, assume ~250 trades/year as proxy if no time info)
    if len(r) > 1:
        sharpe = float(np.mean(r) / (np.std(r) + 1e-9) * np.sqrt(min(len(r), 252)))
    else:
        sharpe = 0.0

    return {
        "pf":       float(pf),
        "wr":       float(wr),
        "max_dd":   float(max_dd),
        "sharpe":   float(sharpe),
        "n_trades": int(len(trades_df)),
    }


# ---------------------------------------------------------------------------
# Objective factory
# ---------------------------------------------------------------------------

def make_objective(train_df: pd.DataFrame, test_df: pd.DataFrame, feature_cols: list):
    """
    Returns objective function for Optuna.
    Trains model on train_df, evaluates on test_df TMF trades only.
    Objectives: (filtered_pf, filtered_wr, -max_drawdown)
    Constraint: filtered_n >= 15
    """
    from optimizer.ml.models import BreakoutFilterModel

    # Pre-split TMF test set
    if "instrument" in test_df.columns:
        tmf_test = test_df[test_df["instrument"] == "TMF"].reset_index(drop=True)
    else:
        tmf_test = test_df.reset_index(drop=True)

    X_train = train_df[feature_cols].fillna(0)
    y_train = train_df["win"].astype(int)

    # Validation slice (last 15% of training by time order)
    val_split = int(len(X_train) * 0.85)
    X_val = X_train.iloc[val_split:]
    y_val = y_train.iloc[val_split:]
    X_tr  = X_train.iloc[:val_split]
    y_tr  = y_train.iloc[:val_split]

    X_tmf = tmf_test[feature_cols].fillna(0)

    def objective(trial: optuna.Trial):
        # ---- Search space ----
        # XGBoost
        n_est_xgb    = trial.suggest_int("n_est_xgb", 100, 500)
        max_depth_xgb = trial.suggest_int("max_depth_xgb", 3, 8)
        lr_xgb       = trial.suggest_float("lr_xgb", 0.01, 0.15, log=True)
        subsample_xgb = trial.suggest_float("subsample_xgb", 0.6, 1.0)
        colsample_xgb = trial.suggest_float("colsample_xgb", 0.5, 1.0)
        min_child_xgb = trial.suggest_int("min_child_xgb", 1, 20)
        reg_alpha_xgb = trial.suggest_float("reg_alpha_xgb", 1e-3, 10.0, log=True)
        reg_lambda_xgb = trial.suggest_float("reg_lambda_xgb", 1e-3, 10.0, log=True)

        # LightGBM
        num_leaves_lgb  = trial.suggest_int("num_leaves_lgb", 15, 63)
        lr_lgb          = trial.suggest_float("lr_lgb", 0.01, 0.15, log=True)
        subsample_lgb   = trial.suggest_float("subsample_lgb", 0.6, 1.0)
        min_child_lgb   = trial.suggest_int("min_child_lgb", 5, 50)
        reg_alpha_lgb   = trial.suggest_float("reg_alpha_lgb", 1e-3, 10.0, log=True)
        reg_lambda_lgb  = trial.suggest_float("reg_lambda_lgb", 1e-3, 10.0, log=True)
        n_est_lgb       = trial.suggest_int("n_est_lgb", 100, 500)

        # Filter threshold
        threshold = trial.suggest_float("threshold", 0.4, 0.7)

        # Ensemble weights (constrained: w_rf = 1 - w_xgb - w_lgb, clipped to [0.05, 0.5])
        w_xgb = trial.suggest_float("w_xgb", 0.2, 0.6)
        w_lgb = trial.suggest_float("w_lgb", 0.2, 0.6)
        w_rf  = max(0.05, 1.0 - w_xgb - w_lgb)

        # ---- Build model ----
        xgb_params = {
            "n_estimators":    n_est_xgb,
            "max_depth":       max_depth_xgb,
            "learning_rate":   lr_xgb,
            "subsample":       subsample_xgb,
            "colsample_bytree": colsample_xgb,
            "min_child_weight": min_child_xgb,
            "reg_alpha":       reg_alpha_xgb,
            "reg_lambda":      reg_lambda_xgb,
        }
        lgb_params = {
            "n_estimators":    n_est_lgb,
            "num_leaves":      num_leaves_lgb,
            "learning_rate":   lr_lgb,
            "subsample":       subsample_lgb,
            "min_child_samples": min_child_lgb,
            "reg_alpha":       reg_alpha_lgb,
            "reg_lambda":      reg_lambda_lgb,
        }

        model = BreakoutFilterModel(use_gpu=True)
        model.weights = {"xgb": w_xgb, "lgb": w_lgb, "rf": w_rf}
        model.threshold = threshold

        try:
            model.fit(X_tr, y_tr, X_val=X_val, y_val=y_val,
                      params={"xgb": xgb_params, "lgb": lgb_params})
        except Exception as e:
            # Return poor values on failure
            return 0.0, 0.0, -100.0

        # ---- Evaluate on TMF test ----
        if len(X_tmf) == 0:
            return 0.0, 0.0, -100.0

        proba = model.predict_proba(X_tmf)
        mask  = proba >= threshold

        filtered_n = int(mask.sum())

        # Constraint: must keep enough trades
        if filtered_n < 15:
            return 0.0, 0.0, -100.0

        filtered_df = tmf_test[mask].reset_index(drop=True)
        m = compute_backtest_metrics(filtered_df)

        filtered_pf = m["pf"]
        filtered_wr = m["wr"]
        max_dd      = m["max_dd"]

        # Clamp inf PF
        if np.isinf(filtered_pf) or np.isnan(filtered_pf):
            filtered_pf = 10.0

        return float(filtered_pf), float(filtered_wr), float(-max_dd)

    return objective


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_optuna_search(
    dataset_path,
    results_dir,
    n_trials: int = 200,
    test_start: str = "2025-07-01",
):
    """
    Run multi-objective search.
    Saves: study.pkl, best_params.json, pareto_front.csv
    Prints top 10 Pareto solutions.
    """
    dataset_path = Path(dataset_path)
    results_dir  = Path(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)

    if not dataset_path.exists():
        print(f"[ERROR] Dataset not found: {dataset_path}")
        return None

    print(f"Loading dataset from {dataset_path} ...")
    df = pd.read_parquet(dataset_path)
    print(f"  Shape: {df.shape}")

    feature_cols = [c for c in df.columns if c.startswith("f_")]
    if not feature_cols:
        print("[ERROR] No feature columns (f_*) found.")
        return None
    print(f"  Features: {len(feature_cols)}")

    if "entry_time" not in df.columns:
        print("[ERROR] 'entry_time' column not found.")
        return None

    df["entry_time"] = pd.to_datetime(df["entry_time"])
    test_cutoff = pd.Timestamp(test_start)
    train_df = df[df["entry_time"] < test_cutoff].copy()
    test_df  = df[df["entry_time"] >= test_cutoff].copy()

    print(f"  Train: {len(train_df)}  Test: {len(test_df)}")

    # TMF counts
    if "instrument" in test_df.columns:
        tmf_n = int((test_df["instrument"] == "TMF").sum())
        print(f"  TMF test trades: {tmf_n}")
        if tmf_n < 15:
            print("[WARN] Very few TMF test trades; Optuna constraint may never be satisfied.")

    objective = make_objective(train_df, test_df, feature_cols)

    study = optuna.create_study(
        directions=["maximize", "maximize", "maximize"],  # pf, wr, -maxdd
        sampler=optuna.samplers.NSGAIISampler(seed=42),
        study_name="breakout_filter_search",
    )

    print(f"\nStarting Optuna search: {n_trials} trials ...")
    study.optimize(objective, n_trials=n_trials, show_progress_bar=True, n_jobs=1)

    # ---- Pareto front ----
    pareto_trials = study.best_trials
    print(f"\nPareto front: {len(pareto_trials)} solutions")

    rows = []
    for t in pareto_trials:
        pf_val, wr_val, neg_dd = t.values
        row = {
            "trial_number":  t.number,
            "filtered_pf":   pf_val,
            "filtered_wr":   wr_val,
            "neg_max_dd":    neg_dd,
            "max_dd":        -neg_dd,
        }
        row.update(t.params)
        rows.append(row)

    pareto_df = pd.DataFrame(rows)
    if not pareto_df.empty:
        # Sort by PF descending
        pareto_df = pareto_df.sort_values("filtered_pf", ascending=False).reset_index(drop=True)
        pareto_path = results_dir / "pareto_front.csv"
        pareto_df.to_csv(pareto_path, index=False)
        print(f"Pareto front saved to {pareto_path}")

        print("\nTop 10 Pareto solutions (by filtered_pf):")
        display_cols = ["trial_number", "filtered_pf", "filtered_wr", "max_dd", "threshold", "w_xgb", "w_lgb"]
        display_cols = [c for c in display_cols if c in pareto_df.columns]
        print(pareto_df[display_cols].head(10).to_string(index=False))

        # Best params = highest PF on Pareto front
        best_row = pareto_df.iloc[0]
        best_params = {k: v for k, v in best_row.items()
                       if k not in ("trial_number", "filtered_pf", "filtered_wr",
                                    "neg_max_dd", "max_dd")}
        best_params_path = results_dir / "best_params.json"
        with open(best_params_path, "w", encoding="utf-8") as f:
            json.dump(best_params, f, indent=2)
        print(f"Best params saved to {best_params_path}")

    # ---- Save study ----
    study_path = results_dir / "study.pkl"
    with open(study_path, "wb") as f:
        pickle.dump(study, f)
    print(f"Study saved to {study_path}")

    return study


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    run_optuna_search(
        ROOT / "data" / "historical" / "ml" / "ml_dataset.parquet",
        ROOT / "optimizer" / "results",
        n_trials=200,
    )
