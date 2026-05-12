"""
apply_ml_filter.py — ORB ML Filter 推論腳本（US ETFs + TMF）
=============================================================
用途：在 ORB 信號產生後，套用 ML 過濾器決定是否下單。
適用商品：US ETFs + TMF（台指夜盤）

用法：
    from deployed_strategies.us_etf_orb_ml.apply_ml_filter import OrbMLFilter

    orb_filter = OrbMLFilter()
    should_trade, prob = orb_filter.predict(instrument, features_dict)

閾值選擇（B1 模型）：
    - 0.45：保守，TMF 保留 ~40 筆/OOS，WR ~53%
    - 0.50：平衡，TMF 保留 ~30 筆/OOS，WR ~56%
    - 0.576：Optuna 最佳，TMF 保留 ~18 筆/OOS，WR 61.1%（樣本偏小）
    建議上線用 0.45，累積 150+ OOS 後再提高
"""

from pathlib import Path
import pickle
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent.parent
MODEL_PATH = ROOT / "optimizer" / "results_orb" / "orb_filter_b1.pkl"
FEATURES_PATH = ROOT / "optimizer" / "results_orb" / "selected_features_b1.txt"

# 所有商品都套 ML 過濾（包含 TMF）
# TMF 不套 ML 時 avg_R=-0.042（略負），套 ML 後 WR=61.1%，必須過濾
SUPPORTED_INSTRUMENTS = {
    "SPXL", "TECL", "TQQQ", "QQQM", "IVV",
    "XLK", "SOXX", "IBB", "FNGS", "TMF",
}


class OrbMLFilter:
    def __init__(self, threshold: float = None):
        """
        Parameters
        ----------
        threshold : float, optional
            ML 機率閾值（None = 使用模型內建最佳閾值）
        """
        with open(MODEL_PATH, "rb") as f:
            bundle = pickle.load(f)

        self.model     = bundle["model"]
        self.threshold = threshold or bundle.get("threshold", 0.5)
        self.features  = bundle.get("features", self._load_features())
        print(f"[OrbMLFilter] Loaded: threshold={self.threshold:.3f}, "
              f"features={len(self.features)}")

    def _load_features(self):
        if FEATURES_PATH.exists():
            return [l.strip() for l in FEATURES_PATH.read_text().splitlines() if l.strip()]
        return []

    def predict(self, instrument: str, features: dict | pd.Series) -> tuple[bool, float]:
        """
        Returns
        -------
        (should_trade, probability)
            should_trade : True = 下單，False = 過濾掉
            probability  : ML 給出的勝率預測（0~1）
        """
        # 不支援的商品直接通過（保守起見）
        if instrument not in SUPPORTED_INSTRUMENTS:
            return True, 1.0

        # 組裝特徵向量
        row = {}
        for feat in self.features:
            row[feat] = features.get(feat, np.nan) if isinstance(features, dict) \
                        else getattr(features, feat, np.nan)

        X = pd.DataFrame([row])[self.features]
        prob = float(self.model.predict_proba(X)[0, 1])
        return prob >= self.threshold, prob
