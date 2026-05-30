"""
Stage 4: Robust Cross-Validation for Time-Series Financial Data
===============================================================
Implements purged K-fold and walk-forward CV from scratch — no mlfinlab
dependency.

The core problem with naive K-fold on financial time series:
  1. Look-ahead leak: test labels may be contemporaneous with train features
  2. Auto-correlation: nearby bars share information, inflating CV scores
  3. Non-stationarity: regime changes make shuffled splits unreliable

This module provides:
  PurgedKFold       — K-fold with purging + embargo (for random-ish splits)
  WalkForwardCV     — rolling walk-forward (gold standard for trading strategies)
  evaluate_cv()     — run CV and collect per-fold metrics
  purged_train_test_split() — simple holdout with embargo
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from typing import Iterator, List, Optional, Tuple

import numpy as np
import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# PurgedKFold
# ─────────────────────────────────────────────────────────────────────────────

class PurgedKFold:
    """
    Time-series K-Fold with purging and embargo.

    Prevents look-ahead leak by:
      1. Purging  — removing training samples whose observation window
                    overlaps with the test period.
      2. Embargo  — removing training samples that are within `embargo_pct`
                    of the total timeline immediately *after* each test fold
                    (captures any residual auto-correlation).

    The splits are contiguous time blocks, not shuffled, so temporal ordering
    is preserved.

    Parameters
    ----------
    n_splits : int
        Number of folds (default 5).
    embargo_pct : float
        Fraction of the total sample count to use as the embargo gap after
        each test fold (default 0.01 = 1%).

    Usage
    -----
    cv = PurgedKFold(n_splits=5, embargo_pct=0.01)
    for train_idx, test_idx in cv.split(X, times=df['entry_time']):
        X_train, X_test = X.iloc[train_idx], X.iloc[test_idx]
        y_train, y_test = y.iloc[train_idx], y.iloc[test_idx]
    """

    def __init__(self, n_splits: int = 5, embargo_pct: float = 0.01):
        if n_splits < 2:
            raise ValueError("n_splits must be >= 2")
        if not (0.0 <= embargo_pct < 1.0):
            raise ValueError("embargo_pct must be in [0, 1)")
        self.n_splits = n_splits
        self.embargo_pct = embargo_pct

    # ------------------------------------------------------------------
    def split(
        self,
        X: pd.DataFrame,
        y=None,
        times: Optional[pd.Series] = None,
    ) -> Iterator[Tuple[np.ndarray, np.ndarray]]:
        """
        Generate (train_indices, test_indices) pairs.

        Parameters
        ----------
        X : pd.DataFrame
            Feature matrix.  Must have a DatetimeIndex OR contain an
            'entry_time' column if `times` is not provided.
        y : ignored
            Kept for sklearn compatibility.
        times : pd.Series, optional
            Series of entry timestamps aligned with X rows.
            If None, tries X.index then X['entry_time'].

        Yields
        ------
        (train_indices, test_indices) : Tuple[np.ndarray, np.ndarray]
            Integer positional indices into X.
        """
        n_samples = len(X)
        if n_samples == 0:
            return

        # ── Resolve timestamps
        if times is not None:
            t = pd.to_datetime(times.values)
        elif isinstance(X.index, pd.DatetimeIndex):
            t = X.index.to_numpy()
        elif "entry_time" in X.columns:
            t = pd.to_datetime(X["entry_time"].values)
        else:
            raise ValueError(
                "Cannot determine timestamps: pass `times=`, use a DatetimeIndex, "
                "or include an 'entry_time' column."
            )

        # Sort order (should already be sorted, but be safe)
        sort_order = np.argsort(t)
        t_sorted = t[sort_order]

        embargo_size = max(1, int(n_samples * self.embargo_pct))

        # Split the timeline into n_splits equal blocks
        fold_bounds = np.array_split(np.arange(n_samples), self.n_splits)

        for fold_i, test_positions in enumerate(fold_bounds):
            if len(test_positions) == 0:
                continue

            test_start_pos = test_positions[0]
            test_end_pos = test_positions[-1]

            test_start_t = t_sorted[test_start_pos]
            test_end_t = t_sorted[test_end_pos]

            # All positions NOT in test fold
            all_positions = np.arange(n_samples)
            candidate_train = np.setdiff1d(all_positions, test_positions)

            # Purge: remove train samples whose time falls within test window
            # (conservative: uses exact time match; if you have event windows
            #  store t_start/t_end per row you can extend this)
            purge_mask = (t_sorted[candidate_train] >= test_start_t) & (
                t_sorted[candidate_train] <= test_end_t
            )
            candidate_train = candidate_train[~purge_mask]

            # Embargo: remove samples within `embargo_size` positions AFTER test fold
            # i.e. positions (test_end_pos+1) .. (test_end_pos+embargo_size)
            embargo_end = min(n_samples - 1, test_end_pos + embargo_size)
            embargo_positions = np.arange(test_end_pos + 1, embargo_end + 1)
            candidate_train = np.setdiff1d(candidate_train, embargo_positions)

            if len(candidate_train) == 0:
                continue

            # Map back to original (unsorted) indices
            train_orig = sort_order[candidate_train]
            test_orig = sort_order[test_positions]

            yield (
                np.sort(train_orig).astype(int),
                np.sort(test_orig).astype(int),
            )

    def get_n_splits(self, X=None, y=None, groups=None) -> int:
        return self.n_splits

    def __repr__(self) -> str:
        return (
            f"PurgedKFold(n_splits={self.n_splits}, "
            f"embargo_pct={self.embargo_pct})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# WalkForwardCV
# ─────────────────────────────────────────────────────────────────────────────

class WalkForwardCV:
    """
    Rolling walk-forward cross-validation.

    Produces a sequence of (train_df, test_df) splits where:
      - Each training window is exactly `train_months` calendar months long.
      - Each test window is exactly `test_months` calendar months long.
      - The window slides forward by `step_months` each fold.

    This mirrors how a live trading system would be retrained and evaluated:
    train on past N months, predict on next M months, repeat.

    Parameters
    ----------
    train_months : int
        Length of the training window in calendar months (default 12).
    test_months : int
        Length of the test window in calendar months (default 3).
    step_months : int
        Step size between folds in calendar months (default 3).
        If step_months == test_months, windows are non-overlapping (standard).
        If step_months < test_months, test windows overlap (expanding out-of-sample).
    min_train_samples : int
        Minimum number of training samples required to include a fold
        (default 30).  Folds with fewer samples are skipped.

    Usage
    -----
    cv = WalkForwardCV(train_months=12, test_months=3, step_months=3)
    for train_df, test_df in cv.split(df, time_col='entry_time'):
        model.fit(train_df[features], train_df['win'])
        preds = model.predict_proba(test_df[features])[:, 1]
    """

    def __init__(
        self,
        train_months: int = 12,
        test_months: int = 3,
        step_months: int = 3,
        min_train_samples: int = 30,
    ):
        if train_months < 1:
            raise ValueError("train_months must be >= 1")
        if test_months < 1:
            raise ValueError("test_months must be >= 1")
        if step_months < 1:
            raise ValueError("step_months must be >= 1")
        self.train_months = train_months
        self.test_months = test_months
        self.step_months = step_months
        self.min_train_samples = min_train_samples

    # ------------------------------------------------------------------
    def split(
        self,
        df: pd.DataFrame,
        time_col: str = "entry_time",
    ) -> Iterator[Tuple[pd.DataFrame, pd.DataFrame]]:
        """
        Yield (train_df, test_df) pairs.

        Parameters
        ----------
        df : pd.DataFrame
            DataFrame with a datetime column named `time_col`.
        time_col : str
            Name of the datetime column (default 'entry_time').

        Yields
        ------
        (train_df, test_df) : Tuple[pd.DataFrame, pd.DataFrame]
        """
        for train_df, test_df, *_ in self._iter_splits(df, time_col, yield_dates=False):
            yield train_df, test_df

    # ------------------------------------------------------------------
    def get_splits_info(
        self,
        df: pd.DataFrame,
        time_col: str = "entry_time",
    ) -> List[Tuple]:
        """
        Return metadata for each fold without yielding DataFrames.

        Returns
        -------
        list of (train_start, train_end, test_start, test_end, n_train, n_test)
        """
        info = []
        for _, _, train_start, train_end, test_start, test_end, n_train, n_test in (
            self._iter_splits(df, time_col, yield_dates=True)
        ):
            info.append((train_start, train_end, test_start, test_end, n_train, n_test))
        return info

    # ------------------------------------------------------------------
    def _iter_splits(self, df, time_col, yield_dates=False):
        """Internal generator used by both split() and get_splits_info()."""
        if time_col not in df.columns:
            raise ValueError(f"Column '{time_col}' not found in DataFrame.")

        df = df.copy()
        df[time_col] = pd.to_datetime(df[time_col])
        df = df.sort_values(time_col).reset_index(drop=True)

        min_t = df[time_col].min()
        max_t = df[time_col].max()

        if pd.isna(min_t) or pd.isna(max_t):
            return

        total_months = (
            (max_t.year - min_t.year) * 12 + (max_t.month - min_t.month)
        )
        if total_months < self.train_months + self.test_months:
            print(
                f"[WalkForwardCV] Warning: data spans only ~{total_months} months; "
                f"need at least {self.train_months + self.test_months}. "
                "No splits generated."
            )
            return

        # Generate fold start dates
        fold_train_start = min_t
        while True:
            train_end = _add_months(fold_train_start, self.train_months)
            test_start = train_end
            test_end = _add_months(test_start, self.test_months)

            if test_end > max_t + pd.Timedelta(days=1):
                break

            train_mask = (df[time_col] >= fold_train_start) & (df[time_col] < train_end)
            test_mask = (df[time_col] >= test_start) & (df[time_col] < test_end)

            train_df = df[train_mask].reset_index(drop=True)
            test_df = df[test_mask].reset_index(drop=True)

            if len(train_df) < self.min_train_samples:
                fold_train_start = _add_months(fold_train_start, self.step_months)
                continue

            if len(test_df) == 0:
                fold_train_start = _add_months(fold_train_start, self.step_months)
                continue

            if yield_dates:
                yield (
                    train_df, test_df,
                    fold_train_start, train_end,
                    test_start, test_end,
                    len(train_df), len(test_df),
                )
            else:
                yield train_df, test_df

            fold_train_start = _add_months(fold_train_start, self.step_months)

    def __repr__(self) -> str:
        return (
            f"WalkForwardCV(train_months={self.train_months}, "
            f"test_months={self.test_months}, "
            f"step_months={self.step_months}, "
            f"min_train_samples={self.min_train_samples})"
        )


# ─────────────────────────────────────────────────────────────────────────────
# evaluate_cv
# ─────────────────────────────────────────────────────────────────────────────

def evaluate_cv(
    model,
    X: pd.DataFrame,
    y: pd.Series,
    cv,
    scoring_fn,
    feature_cols: Optional[List[str]] = None,
) -> dict:
    """
    Run cross-validation and collect per-fold metrics.

    Compatible with both PurgedKFold (index-based splits) and WalkForwardCV
    (DataFrame-based splits).

    Parameters
    ----------
    model : sklearn-compatible estimator
        Must implement fit(X, y) and predict_proba(X) (or predict(X)).
    X : pd.DataFrame
        Feature matrix aligned with `y`.
    y : pd.Series
        Target labels aligned with `X`.
    cv : PurgedKFold | WalkForwardCV
        Cross-validator instance.
    scoring_fn : callable
        scoring_fn(y_true: np.ndarray, y_pred_proba: np.ndarray) -> dict
        Returns a dict of {metric_name: float}.
        y_pred_proba is the positive-class probability (1-D array).
    feature_cols : list of str, optional
        Columns to use from X.  Defaults to all columns.

    Returns
    -------
    dict
        {metric_name: [fold_value, ...]}
        Also contains 'n_train' and 'n_test' lists.
    """
    if feature_cols is None:
        feature_cols = X.columns.tolist()

    results: dict = {"n_train": [], "n_test": []}
    fold_num = 0

    # ── Detect split type
    is_wf = isinstance(cv, WalkForwardCV)

    if is_wf:
        # WalkForwardCV yields (train_df, test_df) with time_col in them
        time_col = "entry_time"
        # Reconstruct full df for WalkForwardCV
        full_df = X.copy()
        full_df["__y__"] = y.values

        splits = cv.split(full_df, time_col=time_col)
    else:
        splits = cv.split(X, times=X.get("entry_time", None))

    for split_data in splits:
        fold_num += 1

        if is_wf:
            train_df, test_df = split_data
            X_train = train_df[feature_cols].values
            y_train = train_df["__y__"].values
            X_test = test_df[feature_cols].values
            y_test = test_df["__y__"].values
        else:
            train_idx, test_idx = split_data
            X_train = X.iloc[train_idx][feature_cols].values
            y_train = y.iloc[train_idx].values
            X_test = X.iloc[test_idx][feature_cols].values
            y_test = y.iloc[test_idx].values

        # Handle NaNs (drop rows with any NaN feature)
        train_valid = ~np.isnan(X_train).any(axis=1)
        test_valid = ~np.isnan(X_test).any(axis=1)
        X_train = X_train[train_valid]
        y_train = y_train[train_valid]
        X_test = X_test[test_valid]
        y_test = y_test[test_valid]

        if len(X_train) == 0 or len(X_test) == 0:
            print(f"  [CV] Fold {fold_num}: insufficient samples after NaN removal, skipping.")
            continue

        # Fit
        try:
            model.fit(X_train, y_train)
        except Exception as exc:
            print(f"  [CV] Fold {fold_num}: model.fit() failed: {exc}")
            continue

        # Predict
        try:
            if hasattr(model, "predict_proba"):
                y_pred = model.predict_proba(X_test)[:, 1]
            else:
                y_pred = model.predict(X_test).astype(float)
        except Exception as exc:
            print(f"  [CV] Fold {fold_num}: model.predict() failed: {exc}")
            continue

        # Score
        try:
            fold_metrics = scoring_fn(y_test, y_pred)
        except Exception as exc:
            print(f"  [CV] Fold {fold_num}: scoring_fn() failed: {exc}")
            continue

        for metric, value in fold_metrics.items():
            if metric not in results:
                results[metric] = []
            results[metric].append(value)

        results["n_train"].append(len(X_train))
        results["n_test"].append(len(X_test))

        print(
            f"  [CV] Fold {fold_num}: n_train={len(X_train)}, n_test={len(X_test)} | "
            + " | ".join(f"{k}={v:.4f}" for k, v in fold_metrics.items())
        )

    if fold_num == 0:
        print("  [CV] Warning: no folds completed.")

    return results


# ─────────────────────────────────────────────────────────────────────────────
# purged_train_test_split
# ─────────────────────────────────────────────────────────────────────────────

def purged_train_test_split(
    df: pd.DataFrame,
    test_pct: float = 0.3,
    embargo_bars: int = 20,
    time_col: str = "entry_time",
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Simple purged train/test split with embargo.

    The most recent `test_pct` fraction of rows (by time) becomes the test
    set.  The `embargo_bars` rows immediately before the test set are dropped
    from training to prevent contamination from auto-correlated samples.

    Parameters
    ----------
    df : pd.DataFrame
        Must contain a datetime column named `time_col`.
    test_pct : float
        Fraction of data to use as test set (default 0.3 = 30%).
    embargo_bars : int
        Number of rows before the test set to exclude from training
        (default 20).
    time_col : str
        Name of the datetime column (default 'entry_time').

    Returns
    -------
    (train_df, test_df) : Tuple[pd.DataFrame, pd.DataFrame]

    Raises
    ------
    ValueError
        If `time_col` is missing or if there are insufficient rows.
    """
    if time_col not in df.columns:
        raise ValueError(f"Column '{time_col}' not found in DataFrame.")
    if not (0.0 < test_pct < 1.0):
        raise ValueError("test_pct must be in (0, 1)")

    df = df.copy()
    df[time_col] = pd.to_datetime(df[time_col])
    df = df.sort_values(time_col).reset_index(drop=True)

    n = len(df)
    if n < 4:
        raise ValueError(f"DataFrame has only {n} rows; too few to split.")

    n_test = max(1, int(n * test_pct))
    n_train = n - n_test

    if n_train <= embargo_bars:
        print(
            f"[purged_train_test_split] Warning: embargo_bars={embargo_bars} >= "
            f"n_train={n_train}. Reducing embargo to {max(0, n_train - 1)}."
        )
        embargo_bars = max(0, n_train - 1)

    test_df = df.iloc[n_train:].reset_index(drop=True)
    # Exclude the embargo zone from training
    train_end_idx = n_train - embargo_bars
    train_df = df.iloc[:train_end_idx].reset_index(drop=True)

    if len(train_df) == 0:
        raise ValueError(
            f"Train set is empty after applying embargo_bars={embargo_bars}. "
            "Reduce embargo_bars or test_pct."
        )

    print(
        f"[purged_train_test_split] "
        f"train={len(train_df)} rows "
        f"({df[time_col].iloc[0].date()} → "
        f"{df[time_col].iloc[train_end_idx - 1].date()}), "
        f"embargo={embargo_bars} rows dropped, "
        f"test={len(test_df)} rows "
        f"({test_df[time_col].iloc[0].date()} → "
        f"{test_df[time_col].iloc[-1].date()})"
    )

    return train_df, test_df


# ─────────────────────────────────────────────────────────────────────────────
# Utility functions
# ─────────────────────────────────────────────────────────────────────────────

def _add_months(dt: pd.Timestamp, n: int) -> pd.Timestamp:
    """Add `n` calendar months to a Timestamp, clamping to valid month end."""
    month = dt.month - 1 + n
    year = dt.year + month // 12
    month = month % 12 + 1
    # Clamp day to last valid day of the target month
    import calendar
    max_day = calendar.monthrange(year, month)[1]
    day = min(dt.day, max_day)
    return pd.Timestamp(year=year, month=month, day=day,
                        hour=dt.hour, minute=dt.minute, second=dt.second)


def cv_summary(cv_results: dict) -> pd.DataFrame:
    """
    Convert the dict returned by evaluate_cv() to a tidy summary DataFrame.

    Returns a DataFrame with one row per fold and one column per metric,
    plus a final 'mean' and 'std' row.
    """
    # Exclude non-metric keys
    skip_keys = set()
    metric_keys = [k for k in cv_results if k not in skip_keys]

    if not metric_keys:
        return pd.DataFrame()

    n_folds = max(len(cv_results[k]) for k in metric_keys)
    rows = []
    for i in range(n_folds):
        row = {"fold": i + 1}
        for k in metric_keys:
            vals = cv_results[k]
            row[k] = vals[i] if i < len(vals) else np.nan
        rows.append(row)

    summary = pd.DataFrame(rows).set_index("fold")

    # Append mean / std rows
    mean_row = summary.mean().rename("mean")
    std_row = summary.std().rename("std")
    summary = pd.concat([summary, mean_row.to_frame().T, std_row.to_frame().T])
    summary.index = summary.index.astype(str)

    return summary


# ─────────────────────────────────────────────────────────────────────────────
# Example scoring function (import or replace in your training script)
# ─────────────────────────────────────────────────────────────────────────────

def default_scoring_fn(y_true: np.ndarray, y_pred_proba: np.ndarray) -> dict:
    """
    Compute common binary-classification metrics for a single fold.

    Parameters
    ----------
    y_true : np.ndarray  shape (n,)  — binary labels (0/1)
    y_pred_proba : np.ndarray  shape (n,)  — predicted positive-class probability

    Returns
    -------
    dict with keys: accuracy, precision, recall, f1, roc_auc, brier
    """
    from sklearn.metrics import (
        accuracy_score,
        brier_score_loss,
        f1_score,
        precision_score,
        recall_score,
        roc_auc_score,
    )

    y_pred_bin = (y_pred_proba >= 0.5).astype(int)
    metrics = {
        "accuracy": accuracy_score(y_true, y_pred_bin),
        "precision": precision_score(y_true, y_pred_bin, zero_division=0),
        "recall": recall_score(y_true, y_pred_bin, zero_division=0),
        "f1": f1_score(y_true, y_pred_bin, zero_division=0),
        "brier": brier_score_loss(y_true, y_pred_proba),
    }
    # ROC-AUC requires both classes present
    if len(np.unique(y_true)) > 1:
        metrics["roc_auc"] = roc_auc_score(y_true, y_pred_proba)
    else:
        metrics["roc_auc"] = np.nan

    return metrics


# ─────────────────────────────────────────────────────────────────────────────
# Entry point — demo / smoke test
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("Stage 4: Cross-Validation Demo / Smoke Test")
    print("=" * 60)

    # ── Try to load the pooled ML dataset
    ml_dataset_path = (
        Path(__file__).parent.parent.parent
        / "data" / "historical" / "ml" / "ml_dataset.parquet"
    )

    if not ml_dataset_path.exists():
        print(f"\n[INFO] ml_dataset.parquet not found at {ml_dataset_path}")
        print("       Run labels.py (Stage 3) first to generate the dataset.")
        print("\n--- Generating synthetic data for smoke test ---")

        # Synthetic data
        np.random.seed(42)
        n = 500
        dates = pd.date_range("2023-01-01", periods=n, freq="B")
        X_synth = pd.DataFrame(
            np.random.randn(n, 5),
            columns=["f_a", "f_b", "f_c", "f_d", "f_e"],
            index=dates,
        )
        X_synth["entry_time"] = dates
        y_synth = pd.Series(np.random.randint(0, 2, n))

        print("\n--- PurgedKFold ---")
        pkf = PurgedKFold(n_splits=5, embargo_pct=0.02)
        for fold_i, (tr, te) in enumerate(pkf.split(X_synth)):
            print(f"  Fold {fold_i + 1}: train={len(tr)}, test={len(te)}")

        print("\n--- WalkForwardCV ---")
        df_synth = X_synth.copy()
        df_synth["win"] = y_synth.values
        wf = WalkForwardCV(train_months=3, test_months=1, step_months=1, min_train_samples=10)
        splits_info = wf.get_splits_info(df_synth, time_col="entry_time")
        for ts in splits_info:
            print(
                f"  train: {ts[0].date()} → {ts[1].date()}  "
                f"({ts[4]} samples)  |  "
                f"test: {ts[2].date()} → {ts[3].date()}  "
                f"({ts[5]} samples)"
            )

        print("\n--- purged_train_test_split ---")
        train_df, test_df = purged_train_test_split(df_synth, test_pct=0.2, embargo_bars=5)

        print("\n[Stage 4] Smoke test passed.")

    else:
        df = pd.read_parquet(ml_dataset_path)
        print(f"\nLoaded ml_dataset: {len(df):,} trades")
        print(f"Instruments: {df['instrument'].unique().tolist()}")
        print(f"Date range: {df['entry_time'].min()} → {df['entry_time'].max()}")

        # Walk-forward splits info
        wf = WalkForwardCV(train_months=12, test_months=3, step_months=3, min_train_samples=30)
        splits = wf.get_splits_info(df, time_col="entry_time")
        print(f"\nWalkForwardCV splits ({len(splits)} folds):")
        for s in splits:
            print(
                f"  train: {s[0].date()} → {s[1].date()}  ({s[4]:>4} trades)  |  "
                f"test: {s[2].date()} → {s[3].date()}  ({s[5]:>4} trades)"
            )

        # Purged K-fold info
        feat_cols = [c for c in df.columns if c.startswith("f_")]
        if feat_cols:
            pkf = PurgedKFold(n_splits=5, embargo_pct=0.01)
            X = df[feat_cols + ["entry_time"]]
            print(f"\nPurgedKFold(5) splits ({len(feat_cols)} features):")
            for fold_i, (tr, te) in enumerate(pkf.split(X)):
                print(f"  Fold {fold_i + 1}: train={len(tr):>4}, test={len(te):>4}")
        else:
            print("\n[INFO] No f_* feature columns found in dataset.")

        print("\n[Stage 4] Done.")
