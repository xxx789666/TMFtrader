"""
UltraTrader 部位大小計算
三種風格預設：保守 / 平衡 / 積極
"""

import math
from dataclasses import dataclass
from typing import Optional


@dataclass
class RiskPreset:
    """風險預設參數"""
    name: str
    label: str
    risk_per_trade: float      # 每筆交易風險佔帳戶比例
    max_contracts: int          # 最大口數
    max_daily_trades: int       # 每日最大交易次數
    max_daily_loss: float       # 每日最大虧損（元）
    max_consecutive_loss: int   # 連續虧損暫停門檻
    cooldown_minutes: int       # 暫停冷卻時間（分鐘）
    max_drawdown_pct: float     # 最大回撤比例（佔帳戶）


# 三種風格預設
RISK_PRESETS = {
    # 風險參數以 43K 帳戶 + TMF(10元/點) + ATR≈80 為基準校準
    # 1 口 TMF 停損 ≈ 100pt × 10 = 1,000 元（含滑價 1,100 元）
    "conservative": RiskPreset(
        name="conservative",
        label="🛡️ 保守",
        risk_per_trade=0.03,        # 3% = 1,304 元，剛好 1 口
        max_contracts=1,
        max_daily_trades=5,
        max_daily_loss=2000,        # 約 2 筆停損
        max_consecutive_loss=3,
        cooldown_minutes=30,
        max_drawdown_pct=0.05,      # 5%
    ),
    "balanced": RiskPreset(
        name="balanced",
        label="⚖️ 平衡",
        risk_per_trade=0.04,        # 4% = 1,739 元，穩定 1 口
        max_contracts=1,
        max_daily_trades=10,
        max_daily_loss=3000,        # 約 2-3 筆停損
        max_consecutive_loss=4,
        cooldown_minutes=15,
        max_drawdown_pct=0.06,      # 6%
    ),
    "aggressive": RiskPreset(
        name="aggressive",
        label="🔥 積極",
        risk_per_trade=0.06,        # 6% = 2,609 元，可能 1-2 口
        max_contracts=2,
        max_daily_trades=20,
        max_daily_loss=4000,
        max_consecutive_loss=5,
        cooldown_minutes=10,
        max_drawdown_pct=0.08,      # 8%
    ),
    "crisis": RiskPreset(
        name="crisis",
        label="⚔️ 危機",
        risk_per_trade=0.08,        # 8% = 3,478 元
        max_contracts=2,
        max_daily_trades=20,
        max_daily_loss=5000,
        max_consecutive_loss=5,
        cooldown_minutes=5,
        max_drawdown_pct=0.12,      # 12%
    ),
    "tmf_3x": RiskPreset(
        name="tmf_3x",
        label="3x TMF",
        risk_per_trade=0.04,        # 4%，與 balanced 同，讓公式算出 3 口
        max_contracts=3,            # 允許最多 3 口微台指
        max_daily_trades=15,
        max_daily_loss=9000,        # 3 口 × 3 筆停損緩衝
        max_consecutive_loss=4,
        cooldown_minutes=15,
        max_drawdown_pct=0.10,      # 10%
    ),
}


class PositionSizer:
    """
    部位大小計算器

    計算公式：
    quantity = floor(risk_amount / (stop_distance × point_value))

    risk_amount = 帳戶權益 × risk_per_trade
    stop_distance = 策略的 ATR 停損距離（點）
    point_value = 從 InstrumentSpec 動態讀取（TMF=10, TGF=10）
    """

    def __init__(self, profile: str = "balanced"):
        self.preset = RISK_PRESETS.get(profile, RISK_PRESETS["balanced"])

    def set_profile(self, profile: str):
        """切換風險預設"""
        if profile in RISK_PRESETS:
            self.preset = RISK_PRESETS[profile]

    def calculate(self, account_balance: float, stop_distance: float,
                  point_value: float = 10.0) -> int:
        """
        計算建議口數

        account_balance: 帳戶權益（元）
        stop_distance: 停損距離（點）
        point_value: 每點價值（元/點），從 InstrumentSpec 傳入
        """
        if stop_distance <= 0 or account_balance <= 0:
            return 0  # 無效輸入拒絕交易，不再預設 1 口

        if point_value <= 0:
            point_value = 10.0  # 安全 fallback

        # 單筆最大風險金額
        risk_amount = account_balance * self.preset.risk_per_trade

        # 每口風險 = 停損距離 × 點值（含滑價 buffer 10%）
        risk_per_contract = stop_distance * point_value * 1.1

        # 計算口數
        quantity = math.floor(risk_amount / risk_per_contract)

        # 限制在 0 ~ max_contracts 之間（0 = 風險過大不下單）
        quantity = min(quantity, self.preset.max_contracts)

        return max(0, quantity)

    def get_preset_info(self) -> dict:
        """取得當前預設資訊（供 Dashboard 顯示）"""
        p = self.preset
        return {
            "name": p.name,
            "label": p.label,
            "risk_per_trade": f"{p.risk_per_trade * 100:.1f}%",
            "max_contracts": p.max_contracts,
            "max_daily_trades": p.max_daily_trades,
            "max_daily_loss": f"{p.max_daily_loss:,.0f} 元",
            "max_consecutive_loss": p.max_consecutive_loss,
            "cooldown_minutes": f"{p.cooldown_minutes} 分鐘",
            "max_drawdown_pct": f"{p.max_drawdown_pct * 100:.0f}%",
        }
