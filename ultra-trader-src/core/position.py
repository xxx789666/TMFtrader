"""
UltraTrader 部位管理
追蹤持倉、記錄交易、計算績效
"""

import json
import threading
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Optional


class Side(Enum):
    FLAT = "flat"
    LONG = "long"
    SHORT = "short"


@dataclass
class Position:
    """當前持倉"""
    side: Side = Side.FLAT
    entry_price: float = 0.0
    quantity: int = 0
    entry_time: Optional[datetime] = None
    stop_loss: float = 0.0
    take_profit: float = 0.0
    trailing_stop: float = 0.0
    trailing_activated: bool = False
    breakeven_activated: bool = False       # 保本停損已啟動
    max_unrealized_profit: float = 0.0     # 最大未實現獲利（點數）
    highest_since_entry: float = 0.0
    lowest_since_entry: float = 999999.0
    bars_since_entry: int = 0
    # 分段停利：[(price, fraction), ...]
    take_profit_levels: list = field(default_factory=list)
    original_quantity: int = 0  # 原始數量（分段出場用）

    @property
    def is_flat(self) -> bool:
        return self.side == Side.FLAT

    def unrealized_pnl(self, current_price: float, point_value: float = 1.0) -> float:
        """計算未實現損益"""
        if self.is_flat:
            return 0.0
        if self.side == Side.LONG:
            return (current_price - self.entry_price) * self.quantity * point_value
        else:  # SHORT
            return (self.entry_price - current_price) * self.quantity * point_value

    def unrealized_points(self, current_price: float) -> float:
        """計算未實現點數"""
        if self.is_flat:
            return 0.0
        if self.side == Side.LONG:
            return (current_price - self.entry_price) * self.quantity
        else:
            return (self.entry_price - current_price) * self.quantity


@dataclass
class Trade:
    """已完成的交易紀錄"""
    entry_time: datetime
    exit_time: datetime
    side: str  # "long" / "short"
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float  # 損益（元）
    pnl_points: float  # 損益（點）
    commission: float  # 手續費
    reason: str  # 出場原因
    bars_held: int = 0  # 持倉 K 棒數
    instrument: str = ""  # 商品代碼

    # === 績效追蹤新增欄位 ===
    strategy: str = ""                  # "momentum" / "mean_reversion"
    market_regime: str = ""             # "TRENDING_UP" / "RANGING" / etc.
    signal_strength: float = 0.0        # 進場信號強度 0-1
    max_favorable: float = 0.0          # 最大有利點數（MFE）
    max_adverse: float = 0.0            # 最大不利點數（MAE）

    @property
    def is_winner(self) -> bool:
        return self.pnl > 0

    @property
    def net_pnl(self) -> float:
        """扣除手續費後的淨損益"""
        return self.pnl - self.commission

    def to_perf_dict(self) -> dict:
        """轉為績效記錄用 dict"""
        return {
            "entry_time": self.entry_time.isoformat() if self.entry_time else "",
            "exit_time": self.exit_time.isoformat() if self.exit_time else "",
            "side": self.side,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "quantity": self.quantity,
            "pnl": self.pnl,
            "pnl_points": self.pnl_points,
            "net_pnl": self.net_pnl,
            "commission": self.commission,
            "reason": self.reason,
            "bars_held": self.bars_held,
            "strategy": self.strategy,
            "market_regime": self.market_regime,
            "signal_strength": self.signal_strength,
            "max_favorable": self.max_favorable,
            "max_adverse": self.max_adverse,
        }


# ============================================================
# 部位管理器
# ============================================================

class PositionManager:
    """
    管理持倉狀態和交易紀錄
    支援多商品同時持倉（每個商品獨立一個 Position）
    餘額跨商品共用
    """

    def __init__(self, instruments: list[str] = None, configs: dict = None,
                 initial_balance: float = 0.0, state_dir: "Path | None" = None):
        """
        instruments: 商品代碼列表，如 ["TMF", "TGF"]
        configs: 每個商品的規格 {code: InstrumentSpec}
        state_dir: 若指定，paper 持倉會寫入此目錄，重啟後可恢復
        """
        self.instruments = instruments or ["TMF"]
        self.configs = configs or {}
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self._lock = threading.Lock()  # 線程安全鎖
        self._state_dir: "Path | None" = Path(state_dir) if state_dir else None
        if self._state_dir:
            self._state_dir.mkdir(parents=True, exist_ok=True)

        # 每個商品各一個 Position
        self.positions: dict[str, Position] = {inst: Position() for inst in self.instruments}

        # 向後相容：單商品時 self.position 指向第一個
        self.position = self.positions[self.instruments[0]]

        self.trades: list[Trade] = []
        self.daily_trades: list[Trade] = []
        self._today: Optional[str] = None

    def _get_config(self, instrument: str):
        """取得商品規格"""
        return self.configs.get(instrument, None)

    def open_position(
        self,
        instrument: str,
        side: Side,
        price: float,
        quantity: int,
        stop_loss: float,
        take_profit: float,
        timestamp: Optional[datetime] = None,
        take_profit_levels: list = None,
    ) -> Position:
        """開倉（指定商品）— 線程安全"""
        with self._lock:
            pos = self.positions.get(instrument)
            if pos is None:
                raise ValueError(f"未知商品: {instrument}")
            if not pos.is_flat:
                raise ValueError(f"已有 {instrument} 持倉，請先平倉")

            new_pos = Position(
                side=side,
                entry_price=price,
                quantity=quantity,
                entry_time=timestamp or datetime.now(),
                stop_loss=stop_loss,
                take_profit=take_profit,
                trailing_stop=0.0,
                trailing_activated=False,
                highest_since_entry=price,
                lowest_since_entry=price,
                bars_since_entry=0,
                take_profit_levels=take_profit_levels or [],
                original_quantity=quantity,
            )
            self.positions[instrument] = new_pos
            # 向後相容
            if instrument == self.instruments[0]:
                self.position = new_pos
            self._save_position(instrument, new_pos)
            return new_pos

    def close_position(
        self,
        instrument: str,
        price: float,
        reason: str,
        timestamp: Optional[datetime] = None,
        quantity: int = 0,  # 0=全平；N>0=部分平倉 N 口
    ) -> Optional[Trade]:
        """平倉（指定商品），回傳交易紀錄 — 線程安全
        quantity=0 或 quantity>=持倉量 → 全平（原行為）
        quantity<持倉量 → 部分平倉，持倉量減少，位置維持開倉狀態
        """
        with self._lock:
            pos = self.positions.get(instrument)
            if pos is None or pos.is_flat:
                return None

            exit_time = timestamp or datetime.now()
            config = self._get_config(instrument)
            point_value = config.point_value if config else 10.0
            commission_rate = config.commission if config else 18.0
            tax_rate = config.tax if config else 7.0

            # 決定本次平倉口數
            close_qty = quantity if (0 < quantity < pos.quantity) else pos.quantity
            is_full_close = (close_qty >= pos.quantity)

            # 計算損益
            if pos.side == Side.LONG:
                pnl_points = (price - pos.entry_price) * close_qty
            else:
                pnl_points = (pos.entry_price - price) * close_qty

            pnl = pnl_points * point_value
            commission = (commission_rate + tax_rate) * 2 * close_qty

            # MFE / MAE（以本次平倉口數為基礎）
            if pos.side == Side.LONG:
                max_favorable = (pos.highest_since_entry - pos.entry_price) * close_qty
                max_adverse = (pos.entry_price - pos.lowest_since_entry) * close_qty
            else:
                max_favorable = (pos.entry_price - pos.lowest_since_entry) * close_qty
                max_adverse = (pos.highest_since_entry - pos.entry_price) * close_qty

            trade = Trade(
                entry_time=pos.entry_time,
                exit_time=exit_time,
                side=pos.side.value,
                entry_price=pos.entry_price,
                exit_price=price,
                quantity=close_qty,
                pnl=pnl,
                pnl_points=pnl_points,
                commission=commission,
                reason=reason,
                bars_held=pos.bars_since_entry,
                max_favorable=max_favorable,
                max_adverse=max_adverse,
                instrument=instrument,
            )

            self.trades.append(trade)
            self._update_daily_trades(trade)
            self.balance += trade.net_pnl

            if is_full_close:
                # 清空持倉
                self.positions[instrument] = Position()
                if instrument == self.instruments[0]:
                    self.position = self.positions[instrument]
                self._clear_position(instrument)
            else:
                # 部分平倉：僅減少口數，持倉繼續
                pos.quantity -= close_qty
                self._save_position(instrument, pos)

            return trade

    def update_price(self, instrument: str, price: float):
        """更新指定商品的當前價格（追蹤最高/最低價）"""
        with self._lock:
            pos = self.positions.get(instrument)
            if pos is None or pos.is_flat:
                return
            pos.highest_since_entry = max(pos.highest_since_entry, price)
            pos.lowest_since_entry = min(pos.lowest_since_entry, price)

    def increment_bars(self, instrument: str):
        """K 棒收盤，增加指定商品的持倉 K 棒計數"""
        with self._lock:
            pos = self.positions.get(instrument)
            if pos and not pos.is_flat:
                pos.bars_since_entry += 1

    def get_total_unrealized_pnl(self, prices: dict[str, float]) -> float:
        """計算所有商品的未實現損益總和"""
        total = 0.0
        for inst in self.instruments:
            pos = self.positions[inst]
            if not pos.is_flat and inst in prices:
                config = self._get_config(inst)
                pv = config.point_value if config else 1.0
                total += pos.unrealized_pnl(prices[inst], pv)
        return total

    def get_total_margin_used(self) -> float:
        """計算所有持倉佔用的保證金"""
        total = 0.0
        for inst in self.instruments:
            pos = self.positions[inst]
            if not pos.is_flat:
                config = self._get_config(inst)
                margin = config.margin if config else 20600
                total += margin * pos.quantity
        return total

    def _update_daily_trades(self, trade: Trade):
        """更新每日交易紀錄"""
        today = trade.exit_time.strftime("%Y-%m-%d")
        if self._today != today:
            self._today = today
            self.daily_trades = []
        self.daily_trades.append(trade)

    # ---- 統計 ----

    def get_daily_pnl(self) -> float:
        """今日已實現損益"""
        return sum(t.net_pnl for t in self.daily_trades)

    def get_daily_trade_count(self) -> int:
        """今日交易次數（自動重置隔天計數）"""
        today = datetime.now().strftime("%Y-%m-%d")
        if self._today != today:
            self._today = today
            self.daily_trades = []
        return len(self.daily_trades)

    def get_consecutive_losses(self) -> int:
        """當前連續虧損次數"""
        count = 0
        for trade in reversed(self.daily_trades):
            if trade.net_pnl < 0:
                count += 1
            else:
                break
        return count

    def get_stats(self) -> dict:
        """計算整體績效統計"""
        if not self.trades:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "avg_win": 0.0,
                "avg_loss": 0.0,
                "profit_factor": 0.0,
                "total_pnl": 0.0,
                "max_drawdown": 0.0,
            }

        winners = [t for t in self.trades if t.net_pnl > 0]
        losers = [t for t in self.trades if t.net_pnl <= 0]

        total_wins = sum(t.net_pnl for t in winners) if winners else 0
        total_losses = abs(sum(t.net_pnl for t in losers)) if losers else 0

        equity_curve = []
        running = 0.0
        for t in self.trades:
            running += t.net_pnl
            equity_curve.append(running)

        max_dd = 0.0
        peak = 0.0
        for eq in equity_curve:
            peak = max(peak, eq)
            dd = peak - eq
            max_dd = max(max_dd, dd)

        return {
            "total_trades": len(self.trades),
            "winners": len(winners),
            "losers": len(losers),
            "win_rate": len(winners) / len(self.trades) * 100 if self.trades else 0,
            "avg_win": total_wins / len(winners) if winners else 0,
            "avg_loss": total_losses / len(losers) if losers else 0,
            "profit_factor": total_wins / total_losses if total_losses > 0 else float("inf"),
            "total_pnl": sum(t.net_pnl for t in self.trades),
            "max_drawdown": max_dd,
            "avg_bars_held": sum(t.bars_held for t in self.trades) / len(self.trades),
        }

    # ---- 持倉持久化（paper 模式） ----

    def _pos_file(self, instrument: str) -> "Path | None":
        if not self._state_dir:
            return None
        return self._state_dir / f"paper_pos_{instrument}.json"

    def _save_position(self, instrument: str, pos: Position):
        """將開倉資訊寫入磁碟（僅當 state_dir 已設定）"""
        f = self._pos_file(instrument)
        if f is None or pos.is_flat:
            return
        try:
            data = {
                "instrument":          instrument,
                "side":                pos.side.value,
                "entry_price":         pos.entry_price,
                "quantity":            pos.quantity,
                "original_quantity":   pos.original_quantity,
                "entry_time":          pos.entry_time.isoformat() if pos.entry_time else None,
                "stop_loss":           pos.stop_loss,
                "take_profit":         pos.take_profit,
                "trailing_stop":       pos.trailing_stop,
                "trailing_activated":  pos.trailing_activated,
                "breakeven_activated": pos.breakeven_activated,
                "bars_since_entry":    pos.bars_since_entry,
                "highest_since_entry": pos.highest_since_entry,
                "lowest_since_entry":  pos.lowest_since_entry,
                "max_unrealized_profit": pos.max_unrealized_profit,
                "strategy_state":      getattr(pos, '_strategy_state', {}),
                "saved_at":            datetime.now().isoformat(),
            }
            f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _clear_position(self, instrument: str):
        """刪除磁碟上的持倉檔案"""
        f = self._pos_file(instrument)
        if f and f.exists():
            try:
                f.unlink()
            except Exception:
                pass

    def restore_positions(self) -> list[str]:
        """
        從磁碟恢復 paper 持倉。
        回傳已恢復的商品列表。
        """
        if not self._state_dir:
            return []
        restored = []
        for instrument in self.instruments:
            f = self._pos_file(instrument)
            if not f or not f.exists():
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                side = Side.LONG if data["side"] == "long" else Side.SHORT
                entry_time = datetime.fromisoformat(data["entry_time"]) if data.get("entry_time") else datetime.now()
                pos = Position(
                    side=side,
                    entry_price=data["entry_price"],
                    quantity=data["quantity"],
                    original_quantity=data.get("original_quantity", data["quantity"]),
                    entry_time=entry_time,
                    stop_loss=data["stop_loss"],
                    take_profit=data["take_profit"],
                    trailing_stop=data.get("trailing_stop", 0.0),
                    trailing_activated=data.get("trailing_activated", False),
                    breakeven_activated=data.get("breakeven_activated", False),
                    bars_since_entry=data.get("bars_since_entry", 0),
                    highest_since_entry=data.get("highest_since_entry", data["entry_price"]),
                    lowest_since_entry=data.get("lowest_since_entry", data["entry_price"]),
                    max_unrealized_profit=data.get("max_unrealized_profit", 0.0),
                )
                pos._strategy_state = data.get("strategy_state", {})
                self.positions[instrument] = pos
                if instrument == self.instruments[0]:
                    self.position = pos
                restored.append(instrument)
            except Exception:
                pass
        return restored

    def to_dict(self, prices: dict[str, float] = None) -> dict:
        """序列化為 dict（供 Dashboard 顯示）— 多商品版本"""
        prices = prices or {}
        result = {}
        for inst in self.instruments:
            pos = self.positions[inst]
            price = prices.get(inst, 0.0)
            config = self._get_config(inst)
            pv = config.point_value if config else 1.0
            result[inst] = {
                "side": pos.side.value,
                "entry_price": pos.entry_price,
                "quantity": pos.quantity,
                "entry_time": pos.entry_time.isoformat() if pos.entry_time else None,
                "stop_loss": pos.stop_loss,
                "take_profit": pos.take_profit,
                "trailing_stop": pos.trailing_stop,
                "trailing_activated": pos.trailing_activated,
                "bars_since_entry": pos.bars_since_entry,
                "unrealized_pnl": pos.unrealized_pnl(price, pv),
                "unrealized_points": pos.unrealized_points(price),
            }
        return result
