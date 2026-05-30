"""
ML Pipeline Master Runner — Stages 1-7
Usage: python optimizer/ml/run_pipeline.py [--stage N] [--from-stage N] [--to-stage N]
                                            [--skip-download] [--trials N]
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).parent.parent.parent

import time
import traceback

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Stage registry
# ---------------------------------------------------------------------------

STAGES = {
    1: ("資料收集",    "collect_data.py"),
    2: ("特徵工程",    "features.py"),
    3: ("標籤生成",    "labels.py"),
    4: ("CV設計",      "cv.py"),
    5: ("模型訓練",    "models.py"),
    6: ("Optuna搜尋",  "optuna_search.py"),
    7: ("穩健性驗證",  "robustness.py"),
}

ML_DIR    = ROOT / "optimizer" / "ml"
DATA_DIR  = ROOT / "data" / "historical" / "ml"
RESULTS_DIR = ROOT / "optimizer" / "results"


# ---------------------------------------------------------------------------
# Utility printers
# ---------------------------------------------------------------------------

def print_stage_header(n: int, name: str):
    bar = "=" * 60
    print(f"\n{bar}")
    print(f"  STAGE {n}: {name}")
    print(f"{bar}")


def print_gate_check(passed_items: list, failed_items: list):
    print("\n--- Gate Check ---")
    for item in passed_items:
        print(f"  [PASS] {item}")
    for item in failed_items:
        print(f"  [FAIL] {item}")
    if failed_items:
        print("  => Gate FAILED. Stopping pipeline.")
    else:
        print("  => Gate PASSED.")
    print()


# ---------------------------------------------------------------------------
# Data availability check
# ---------------------------------------------------------------------------

def check_data_available(data_dir: Path) -> dict:
    """Check which instrument parquet files exist."""
    data_dir = Path(data_dir)
    result = {"ml_dataset": False, "instruments": []}

    ml_dataset = data_dir / "ml_dataset.parquet"
    result["ml_dataset"] = ml_dataset.exists()

    if data_dir.exists():
        parquet_files = list(data_dir.glob("*.parquet"))
        for f in parquet_files:
            if f.name != "ml_dataset.parquet":
                result["instruments"].append(f.stem)

    return result


# ---------------------------------------------------------------------------
# Stage runners
# ---------------------------------------------------------------------------

def run_stage1(data_dir: Path, skip_existing: bool = True):
    """Stage 1: Data collection."""
    data_dir = Path(data_dir)
    script = ML_DIR / "collect_data.py"

    if not script.exists():
        print(f"[WARN] collect_data.py not found at {script}.")
        print("       Checking if raw data already exists ...")
        avail = check_data_available(data_dir)
        if avail["ml_dataset"] or avail["instruments"]:
            print(f"  Found: {avail}")
            return True
        else:
            print("  [SKIP] No data found and collect_data.py missing.")
            return False

    print(f"Running {script} ...")
    import subprocess
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=False, text=True,
    )
    return result.returncode == 0


def run_stage2(data_dir: Path):
    """Stage 2: Feature engineering."""
    data_dir = Path(data_dir)
    script = ML_DIR / "features.py"

    if not script.exists():
        print(f"[WARN] features.py not found. Skipping feature engineering.")
        return True  # May already be baked into ml_dataset.parquet

    print(f"Running {script} ...")
    import subprocess
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=False, text=True,
    )
    return result.returncode == 0


def run_stage3(data_dir: Path):
    """Stage 3: Label generation."""
    data_dir = Path(data_dir)
    script = ML_DIR / "labels.py"

    if not script.exists():
        print(f"[WARN] labels.py not found. Skipping label generation.")
        return True

    print(f"Running {script} ...")
    import subprocess
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=False, text=True,
    )
    return result.returncode == 0


def run_stage4(data_dir: Path):
    """
    Stage 4: CV design — validate CV splits and print info.
    Tries to import cv.py and run any validation functions.
    """
    data_dir = Path(data_dir)
    ml_dataset = data_dir / "ml_dataset.parquet"

    if not ml_dataset.exists():
        print(f"[ERROR] ml_dataset.parquet not found at {ml_dataset}")
        return False

    print(f"Loading {ml_dataset} for CV validation ...")
    df = pd.read_parquet(ml_dataset)

    if "entry_time" not in df.columns:
        print("[ERROR] 'entry_time' column missing.")
        return False

    df["entry_time"] = pd.to_datetime(df["entry_time"])
    df = df.sort_values("entry_time")

    # Try to import cv module
    cv_script = ML_DIR / "cv.py"
    if cv_script.exists():
        try:
            import importlib.util
            spec = importlib.util.spec_from_file_location("cv", cv_script)
            cv_mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(cv_mod)
            print("  cv.py imported successfully.")
        except Exception as e:
            print(f"  [WARN] Could not import cv.py: {e}")

    # Print basic CV info
    total_n = len(df)
    instruments = df["instrument"].unique().tolist() if "instrument" in df.columns else []
    date_range = (df["entry_time"].min(), df["entry_time"].max())

    print(f"  Total samples:  {total_n}")
    print(f"  Instruments:    {instruments}")
    print(f"  Date range:     {date_range[0].date()} → {date_range[1].date()}")

    # Propose walk-forward splits
    split_date = pd.Timestamp("2025-07-01")
    train_n = int((df["entry_time"] < split_date).sum())
    test_n  = int((df["entry_time"] >= split_date).sum())
    print(f"  Train (< {split_date.date()}): {train_n}")
    print(f"  Test  (>= {split_date.date()}): {test_n}")

    if "instrument" in df.columns:
        tmf_n = int((df["instrument"] == "TMF").sum())
        tmf_test_n = int(((df["instrument"] == "TMF") & (df["entry_time"] >= split_date)).sum())
        print(f"  TMF total: {tmf_n}  |  TMF test: {tmf_test_n}")

    return True


def run_stage5(data_dir: Path, results_dir: Path):
    """Stage 5: Model training."""
    from optimizer.ml.models import train_and_eval

    ml_dataset = Path(data_dir) / "ml_dataset.parquet"
    if not ml_dataset.exists():
        print(f"[ERROR] ml_dataset.parquet not found: {ml_dataset}")
        return None

    return train_and_eval(
        dataset_path=ml_dataset,
        results_dir=results_dir,
        test_start="2025-07-01",
        use_cv=True,
    )


def run_stage6(data_dir: Path, results_dir: Path, n_trials: int = 200):
    """Stage 6: Optuna hyperparameter search."""
    from optimizer.ml.optuna_search import run_optuna_search

    ml_dataset = Path(data_dir) / "ml_dataset.parquet"
    if not ml_dataset.exists():
        print(f"[ERROR] ml_dataset.parquet not found: {ml_dataset}")
        return None

    return run_optuna_search(
        dataset_path=ml_dataset,
        results_dir=results_dir,
        n_trials=n_trials,
        test_start="2025-07-01",
    )


def run_stage7(data_dir: Path, results_dir: Path):
    """Stage 7: Robustness tests."""
    from optimizer.ml.robustness import run_all_robustness_tests

    ml_dataset  = Path(data_dir) / "ml_dataset.parquet"
    model_path  = Path(results_dir) / "breakout_filter.pkl"

    if not ml_dataset.exists():
        print(f"[ERROR] ml_dataset.parquet not found: {ml_dataset}")
        return None

    if not model_path.exists():
        print(f"[ERROR] Model not found: {model_path}. Run Stage 5 first.")
        return None

    return run_all_robustness_tests(
        dataset_path=ml_dataset,
        results_dir=results_dir,
        model_path=model_path,
        test_start="2025-07-01",
    )


# ---------------------------------------------------------------------------
# Gate checks
# ---------------------------------------------------------------------------

def _gate_after_stage3(data_dir: Path) -> bool:
    """Gate: total samples >= 200, TMF samples >= 40."""
    ml_dataset = Path(data_dir) / "ml_dataset.parquet"
    if not ml_dataset.exists():
        print_gate_check([], ["ml_dataset.parquet not found"])
        return False

    df = pd.read_parquet(ml_dataset)
    total_n = len(df)
    tmf_n   = int((df["instrument"] == "TMF").sum()) if "instrument" in df.columns else 0

    passed, failed = [], []
    if total_n >= 200:
        passed.append(f"Total samples {total_n} >= 200")
    else:
        failed.append(f"Total samples {total_n} < 200")

    if tmf_n >= 40:
        passed.append(f"TMF samples {tmf_n} >= 40")
    else:
        failed.append(f"TMF samples {tmf_n} < 40")

    print_gate_check(passed, failed)
    return len(failed) == 0


def _gate_after_stage5(results_dir: Path, data_dir: Path) -> bool:
    """Gate: model exists, TMF test AUC >= 0.55, filtered WR >= 55%, filtered PF >= 1.3."""
    model_path = Path(results_dir) / "breakout_filter.pkl"
    if not model_path.exists():
        print_gate_check([], ["breakout_filter.pkl not found"])
        return False

    try:
        from optimizer.ml.models import BreakoutFilterModel, evaluate_model

        model      = BreakoutFilterModel.load(model_path)
        ml_dataset = Path(data_dir) / "ml_dataset.parquet"
        df         = pd.read_parquet(ml_dataset)
        feature_cols = [c for c in df.columns if c.startswith("f_")]

        df["entry_time"] = pd.to_datetime(df["entry_time"])
        test_cutoff = pd.Timestamp("2025-07-01")
        test_df = df[df["entry_time"] >= test_cutoff]

        tmf_mask = test_df["instrument"] == "TMF" if "instrument" in test_df.columns else pd.Series([True] * len(test_df))
        tmf_df   = test_df[tmf_mask].reset_index(drop=True)

        if len(tmf_df) == 0:
            print_gate_check([], ["No TMF test rows found"])
            return False

        X_tmf = tmf_df[feature_cols].fillna(0)
        y_tmf = tmf_df["win"].astype(int)
        m = evaluate_model(model, X_tmf, y_tmf, trades_df=tmf_df, threshold=0.5)

        print(f"  TMF test AUC={m.get('auc', 0):.4f}  "
              f"filtered_WR={m.get('filtered_wr', 0):.3f}  "
              f"filtered_PF={m.get('filtered_pf', 0):.2f}  "
              f"N={m.get('filtered_n', 0)}")

        passed, failed = [], []
        if m.get("auc", 0) >= 0.55:
            passed.append(f"TMF AUC {m['auc']:.4f} >= 0.55")
        else:
            failed.append(f"TMF AUC {m.get('auc', 0):.4f} < 0.55")

        if m.get("filtered_wr", 0) >= 0.55:
            passed.append(f"Filtered WR {m['filtered_wr']:.3f} >= 0.55")
        else:
            failed.append(f"Filtered WR {m.get('filtered_wr', 0):.3f} < 0.55")

        if m.get("filtered_pf", 0) >= 1.3:
            passed.append(f"Filtered PF {m['filtered_pf']:.2f} >= 1.3")
        else:
            failed.append(f"Filtered PF {m.get('filtered_pf', 0):.2f} < 1.3")

        print_gate_check(passed, failed)
        return len(failed) == 0

    except Exception as e:
        print(f"[ERROR] Gate 5 check failed: {e}")
        traceback.print_exc()
        return False


def _gate_after_stage7(results_dir: Path) -> bool:
    """Gate: robustness_report.md exists and summary shows passes."""
    report_path = Path(results_dir) / "robustness_report.md"
    if not report_path.exists():
        print_gate_check([], ["robustness_report.md not found"])
        return False

    with open(report_path, "r", encoding="utf-8") as f:
        content = f.read()

    # Count PASS lines in summary table (now 4 tests: 7.1/7.2/7.3/7.4)
    pass_count = content.count("| PASS |")
    fail_count = content.count("| FAIL |")

    passed, failed = [], []
    if pass_count >= 3:
        passed.append(f"At least 3/4 robustness tests passed ({pass_count} passed)")
    else:
        failed.append(f"Only {pass_count}/4 robustness tests passed (need >= 3)")

    print_gate_check(passed, failed)
    return len(failed) == 0


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    start_stage: int = 1,
    end_stage: int = 7,
    skip_download: bool = False,
    n_trials: int = 200,
):
    """
    Run stages start_stage to end_stage.
    After key stages, run gate checks and stop on failure.
    """
    data_dir    = DATA_DIR
    results_dir = RESULTS_DIR

    data_dir.mkdir(parents=True, exist_ok=True)
    results_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'#'*60}")
    print(f"#  ML Pipeline — Stages {start_stage} → {end_stage}")
    print(f"{'#'*60}")
    print(f"  DATA_DIR    : {data_dir}")
    print(f"  RESULTS_DIR : {results_dir}")
    print(f"  skip_download={skip_download}  n_trials={n_trials}")

    t0_total = time.time()

    for stage_n in range(start_stage, end_stage + 1):
        if stage_n not in STAGES:
            print(f"[WARN] Stage {stage_n} not defined. Skipping.")
            continue

        name = STAGES[stage_n][0]
        print_stage_header(stage_n, name)
        t0 = time.time()

        try:
            ok = True

            if stage_n == 1:
                if skip_download:
                    avail = check_data_available(data_dir)
                    print(f"  --skip-download set. Data status: {avail}")
                else:
                    ok = run_stage1(data_dir, skip_existing=True)

            elif stage_n == 2:
                ok = run_stage2(data_dir)

            elif stage_n == 3:
                ok = run_stage3(data_dir)
                if ok:
                    gate_ok = _gate_after_stage3(data_dir)
                    if not gate_ok:
                        print("[STOP] Gate check failed after Stage 3.")
                        return

            elif stage_n == 4:
                ok = run_stage4(data_dir)

            elif stage_n == 5:
                model = run_stage5(data_dir, results_dir)
                ok = model is not None
                if ok:
                    gate_ok = _gate_after_stage5(results_dir, data_dir)
                    if not gate_ok:
                        print("[WARN] Gate check failed after Stage 5. Consider retraining or re-labeling.")
                        # Continue (don't stop) — let user decide via Optuna

            elif stage_n == 6:
                study = run_stage6(data_dir, results_dir, n_trials=n_trials)
                ok = study is not None

            elif stage_n == 7:
                result = run_stage7(data_dir, results_dir)
                ok = result is not None
                if ok:
                    _gate_after_stage7(results_dir)

        except Exception as e:
            print(f"\n[ERROR] Stage {stage_n} raised an exception:")
            traceback.print_exc()
            ok = False

        elapsed = time.time() - t0
        status  = "OK" if ok else "FAILED"
        print(f"\n  Stage {stage_n} [{name}] finished in {elapsed:.1f}s — {status}")

        if not ok:
            print(f"[STOP] Stage {stage_n} failed. Halting pipeline.")
            break

    total_elapsed = time.time() - t0_total
    print(f"\n{'='*60}")
    print(f"  Pipeline finished in {total_elapsed:.1f}s")
    print(f"{'='*60}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="ML Pipeline Master Runner — Stages 1-7",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python optimizer/ml/run_pipeline.py                        # Run all stages
  python optimizer/ml/run_pipeline.py --stage 5             # Run only stage 5
  python optimizer/ml/run_pipeline.py --from-stage 5        # Run stages 5-7
  python optimizer/ml/run_pipeline.py --from-stage 5 --to-stage 6 --trials 500
  python optimizer/ml/run_pipeline.py --skip-download       # Skip data download
""",
    )
    parser.add_argument("--stage",       type=int, default=None,
                        help="Run a single stage N (overrides --from-stage/--to-stage)")
    parser.add_argument("--from-stage",  type=int, default=1,
                        help="Start stage (default: 1)")
    parser.add_argument("--to-stage",    type=int, default=7,
                        help="End stage (default: 7)")
    parser.add_argument("--skip-download", action="store_true",
                        help="Skip data download in Stage 1")
    parser.add_argument("--trials",      type=int, default=200,
                        help="Number of Optuna trials for Stage 6 (default: 200)")

    args = parser.parse_args()

    if args.stage is not None:
        run_pipeline(args.stage, args.stage, args.skip_download, args.trials)
    else:
        run_pipeline(args.from_stage, args.to_stage, args.skip_download, args.trials)
