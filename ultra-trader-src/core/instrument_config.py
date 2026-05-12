"""
UltraTrader 商品配置
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
    commission: float        # 單邊手續費
    tax: float               # 單邊期交稅
    strategy_type: str       # 使用的策略類型
    default_initial_price: float  # MockBroker 模擬用的初始價格
    shioaji_code: str = ""   # Shioaji 合約代碼（空字串=與 code 相同；TMFN→MXF）


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
    "TMFN": InstrumentSpec(
        code="TMFN",
        name="微型台指期貨（夜盤ORB）",
        point_value=10.0,
        margin=20600,
        maintenance_margin=15800,
        commission=18.0,
        tax=7.0,
        strategy_type="orb",
        default_initial_price=22000.0,
        shioaji_code="MXF",   # 對應 Shioaji 的 MXF 近月合約
    ),
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
