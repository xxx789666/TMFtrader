"""
TMFtrader 商品配置
定義各商品的合約規格、保證金、手續費等參數
"""

from dataclasses import dataclass


@dataclass
class InstrumentSpec:
    """商品規格"""
    code: str               # 商品代碼（如 TMF, TGF）
    name: str               # 商品名稱
    point_value: float       # 每點價值（元/點）
    margin: float            # 原始保證金
    maintenance_margin: float  # 維持保證金
    commission: float        # 單邊手續費（元/口/邊、永豐 TMF 預設 18）
    # 期貨交易稅率（合約價值 × 此比率、雙邊各課、元以下進位）。
    # 期交所股價指數類期貨 = 2/100,000 = 0.00002。
    # 舊欄位 `tax` 為固定金額/口/邊、已棄用（指數高漲時會低估、見 [[live-pnl-tax-fix-2026-05-28]]）。
    tax_rate_pct: float = 0.00002
    tax: float = 0.0         # DEPRECATED：保留欄位給舊資料相容、新計算改用 tax_rate_pct
    strategy_type: str = ""  # 使用的策略類型
    default_initial_price: float = 0.0  # MockBroker 模擬用的初始價格
    shioaji_code: str = ""   # Shioaji 合約代碼（空字串=與 code 相同）


# 支援的商品
INSTRUMENT_SPECS = {
    "TMF": InstrumentSpec(
        code="TMF",
        name="微型台指期貨",
        point_value=10.0,      # 1 點 = 10 元
        margin=20600,          # 2026/02/26 期交所
        maintenance_margin=15800,
        commission=18.0,
        tax=7.0,
        strategy_type="breakout",
        default_initial_price=22000.0,
    ),
    # (2026-05-29 lock-tmf-only) 移除舊 TMFN spec（shioaji_code=MXF）。
    # 夜盤 ORB 自 2026-05-20 起已直接用 TMF/TMFR1（見 night_orb.py），TMFN→MXF 映射不再使用。
    "TGF": InstrumentSpec(
        code="TGF",
        name="小型黃金期貨",
        point_value=10.0,      # 1 點 = 10 元（10 公克 × 1 元/公克）
        margin=11600,          # 約略值，請確認期交所公告
        maintenance_margin=8900,
        commission=18.0,
        tax=7.0,
        strategy_type="gold_trend",
        default_initial_price=3050.0,  # 約 TWD/公克
    ),
}


def get_spec(code: str) -> InstrumentSpec:
    """取得商品規格，找不到就報錯"""
    if code not in INSTRUMENT_SPECS:
        raise ValueError(f"不支援的商品: {code}，可用: {list(INSTRUMENT_SPECS.keys())}")
    return INSTRUMENT_SPECS[code]
