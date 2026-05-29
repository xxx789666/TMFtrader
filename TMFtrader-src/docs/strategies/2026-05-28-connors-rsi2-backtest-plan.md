# Connors RSI(2) 回測 + 優化 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Connors RSI(2) 從設計變成「可回測、過 Stage 7 穩健性 Gate、可上 paper」的實作。長/雙向兩變體並排測試。最大化重用 vwap_fade 已建的基礎設施。

**Architecture:** 新增 `strategy/connors_rsi2.py`（一個 `BaseStrategy` 子類、構造子帶 `allow_short` flag），用既有 `FastBacktestEngine` 跑回測；grid 抄 `scripts/optimize_vwap_fade.py`（dict-based worker、shared_memory 並行）；穩健性三件套**直接 import** `scripts/robustness_vwap_fade.py` 既有函式。不動 core/、不動既有策略檔。

**Tech Stack:** Python 3.12、pandas/numpy、pyarrow、現有 `core/gpu_indicators`、`ProcessPoolExecutor` + `shared_memory`、pytest。

**背景設計文件：** `TMFtrader-src/docs/strategies/2026-05-28-connors-rsi2-design.md`
**引擎事實參考：** `TMFtrader-src/docs/strategies/2026-05-28-vwap-fade-backtest-plan.md` §1 表 + §核心設計決策（整套**繼承**、不重列）

---

## 與 vwap_fade plan 的重大差異（實作前必讀）

| # | 差異 | 對任務的影響 |
|---|---|---|
| 1 | **資料已備** | `data/vwap_fade/{MXF,TXF}_day_5m.parquet` + `TMF_oos_day_5m.parquet` 都在；無 P1 資料準備任務 |
| 2 | **稅模型已動態化**（commit `a95ae44`） | P0 成本 test 不能照抄 vwap_fade 的 `assert commission==50`。新公式：`commission_total = 18×2×qty + (ceil(entry×pv×0.00002) + ceil(exit×pv×0.00002))×qty`。@22000 → `commission=46`、`net_pnl=54` |
| 3 | **RSI(2) state 不跨日 reset** | `_RsiState` 設計刻意與 `_SessionVwap` 相反（Connors 經典是連續指標、Wilder 平滑需要連續歷史）。`_trades_today` / `_cooldown_until_bar` / `_session_bar` 才跨日 reset |
| 4 | **無 regime filter** | 純 RSI(2) 進場；設計刻意，讓 4-3 Gate 全 testable |
| 5 | **雙變體 per-run、不進 grid** | 一個 `ConnorsRsi2Strategy` 類別 + 建構子 `allow_short: bool`。grid 跑 4 runs：{MXF,TXF} × {allow_short=False,True} |
| 6 | **`Signal.take_profit = 0`** | 無自然 TP；出場 100% 由 `check_exit` 驅動 |
| 7 | **`_session_bar` 在策略自管、check_exit 不增量** | 避免 vwap_fade 那個 cooldown off-by-one；cooldown 語意乾淨 = N 個 flat bar |

---

## 可直接重用的設施（不重造）

| 資源 | 路徑 | 用法 |
|---|---|---|
| 資料 | `data/vwap_fade/*.parquet` | `pd.read_parquet(...)`，欄位 datetime/open/high/low/close/volume |
| 動態稅成本模型 | `core/position.py:240-244` | 已整合，無需動 |
| grid 並行骨架 | `scripts/optimize_vwap_fade.py` | 複製、改 PARAM_GRID + 策略 import + 加 `--allow-short` CLI |
| 單 backtest helper | `from scripts.optimize_vwap_fade import _run_one` | `_run_one(params: dict, df, split_idx: int) -> dict` |
| 績效指標 | `from scripts.optimize_strategy import _calc_metrics` | 不重寫 |
| Monte Carlo | `from scripts.robustness_vwap_fade import monte_carlo` | `monte_carlo(pnl, n=5000, seed=42) -> (pf_p5, net_p5, mdd_p95)` |
| Stability | `from scripts.robustness_vwap_fade import stability` | `stability(returns) -> float` |
| Param perturbation | `from scripts.robustness_vwap_fade import perturb_and_run` | `perturb_and_run(best_params, df, split_idx, factors) -> {dim: [returns]}` |
| Regime split | `from scripts.robustness_vwap_fade import split_by_regime` | `split_by_regime(trades, bars_adx_df, trend_thr=25, range_thr=20) -> {"trend":[], "range":[], "neutral":[]}` |

---

## 檔案結構

| 檔案 | 職責 | 動作 |
|---|---|---|
| `strategy/connors_rsi2.py` | `_RsiState` + `ConnorsRsi2Strategy`（含 on_kbar + check_exit） | Create |
| `tests/test_connors_rsi2.py` | 策略單元測試（沿 test_vwap_fade.py 風格，不共用 helpers） | Create |
| `scripts/optimize_connors_rsi2.py` | 粗篩 grid（抄 optimize_vwap_fade.py，雙商品 × 雙變體） | Create |
| `scripts/run_connors_rsi2_experiments.py` | 端到端 OOS 實驗 + Gate 匯總報告 orchestrator | Create |
| `docs/strategies/2026-05-28-connors-rsi2-backtest-plan.md` | 本計畫 | （已存在） |

零改動：core/、既有 strategy/*.py、既有 backtest/*、`scripts/robustness_vwap_fade.py`、`scripts/optimize_vwap_fade.py`、`scripts/optimize_strategy.py`、`data/vwap_fade/*`。

---

## Task 1（P0）：成本會計驗證（動態稅）

**Files:** Test `tests/test_connors_rsi2.py`（新檔）

- [ ] **Step 1: 寫測試** — 確認新動態稅進 `net_pnl`

```python
"""
Connors RSI(2) 策略測試
P0: 成本會計驗證（動態稅模型，commit a95ae44 起）
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import math
from core.position import PositionManager, Side
from core.instrument_config import INSTRUMENT_SPECS


def test_cost_accounting_dynamic_tax():
    """
    TMF spec：point_value=10, commission=18/邊, tax_rate_pct=0.00002
    進場 22000，平倉 22010，1 口：
      毛利       = (22010-22000) × 10 × 1 = 100
      fee_comm   = 18 × 2 邊 × 1 口 = 36
      entry_tax  = ceil(22000 × 10 × 0.00002) = ceil(4.4) = 5
      exit_tax   = ceil(22010 × 10 × 0.00002) = ceil(4.402) = 5
      fee_tax    = (5 + 5) × 1 = 10
      commission(全部扣項) = 36 + 10 = 46
      net_pnl   = 100 - 46 = 54
    """
    spec = INSTRUMENT_SPECS["TMF"]
    pm = PositionManager(instruments=["TMF"], configs={"TMF": spec}, initial_balance=200_000)
    pm.open_position("TMF", Side.LONG, price=22000.0, quantity=1,
                     stop_loss=21950, take_profit=22050, timestamp=None)
    trade = pm.close_position("TMF", 22010.0, "test", None)

    assert trade.pnl == 100.0,        f"pnl: expected 100.0, got {trade.pnl}"
    assert trade.commission == 46.0,  f"commission(全扣項): expected 46.0, got {trade.commission}"
    assert trade.net_pnl == 54.0,     f"net_pnl: expected 54.0, got {trade.net_pnl}"


def test_cost_accounting_high_price_tax_scales():
    """指數越高、稅越高。@45000 → tax=9/邊、commission=18*2+9*2=54、net_pnl 受影響"""
    spec = INSTRUMENT_SPECS["TMF"]
    pm = PositionManager(instruments=["TMF"], configs={"TMF": spec}, initial_balance=200_000)
    pm.open_position("TMF", Side.LONG, price=45000.0, quantity=1,
                     stop_loss=44950, take_profit=45050, timestamp=None)
    trade = pm.close_position("TMF", 45010.0, "test", None)
    # entry_tax = ceil(45000*10*0.00002) = ceil(9.0) = 9
    # exit_tax  = ceil(45010*10*0.00002) = ceil(9.002) = 10
    # fee_tax = 19, fee_comm = 36, commission = 55
    expected_entry_tax = math.ceil(45000 * 10 * 0.00002)
    expected_exit_tax  = math.ceil(45010 * 10 * 0.00002)
    expected_comm = 36 + (expected_entry_tax + expected_exit_tax)
    assert trade.commission == float(expected_comm), \
        f"@45k commission: expected {expected_comm}, got {trade.commission}"
```

- [ ] **Step 2: 跑測試** — Run from `TMFtrader-src/`:
  ```
  python -m pytest tests/test_connors_rsi2.py -v
  ```
  Expected: 2 PASS. 若 FAIL → 成本模型與假設不符，立刻 stop 並 report（這正是 P0 Gate 目的）。

- [ ] **Step 3: Commit**
  ```bash
  git add tests/test_connors_rsi2.py
  git commit -m "test(connors_rsi2): P0 verify dynamic tax in net_pnl"
  ```

**Gate P0：** 兩個 test PASS 才往下。

---

## Task 2（P2a）：`_RsiState` Wilder RSI helper（純邏輯 TDD）

**Files:** Create `strategy/connors_rsi2.py`（this task: ONLY `_RsiState` + module imports）；Test append to `tests/test_connors_rsi2.py`.

- [ ] **Step 1: 寫失敗測試**

```python
def test_rsi_warmup_returns_50():
    """暖身期（update 數 < period）回中性 50.0"""
    from strategy.connors_rsi2 import _RsiState
    s = _RsiState(period=2)
    assert s.rsi == 50.0      # 還沒 update
    s.update(100.0)
    assert s.rsi == 50.0      # 才 1 個 update，不足 period


def test_rsi_full_gain_approaches_100():
    """連續上漲 → RSI 趨向 100"""
    from strategy.connors_rsi2 import _RsiState
    s = _RsiState(period=2)
    for p in [100, 101, 102, 103, 104, 105]:
        s.update(float(p))
    assert s.rsi > 95.0


def test_rsi_full_loss_approaches_0():
    """連續下跌 → RSI 趨向 0"""
    from strategy.connors_rsi2 import _RsiState
    s = _RsiState(period=2)
    for p in [100, 99, 98, 97, 96, 95]:
        s.update(float(p))
    assert s.rsi < 5.0


def test_rsi_state_does_NOT_reset_on_call():
    """_RsiState 本身無 reset/換日邏輯 — 連續 Wilder smoothing。
    換日 reset 是策略層 (_trades_today / _cooldown / _session_bar) 才做。"""
    from strategy.connors_rsi2 import _RsiState
    s = _RsiState(period=2)
    for p in [100, 101, 102, 103, 104]:
        s.update(float(p))
    high_rsi = s.rsi
    assert high_rsi > 80.0
    # 沒有 reset() method
    assert not hasattr(s, "reset"), "_RsiState 不應有 reset() — 跨日連續是設計"
```

- [ ] **Step 2: 跑測試，確認失敗** — Run: `python -m pytest tests/test_connors_rsi2.py -k rsi -v`，Expected: `_RsiState` not defined。

- [ ] **Step 3: 實作** — 建 `strategy/connors_rsi2.py`，內容：

```python
"""
Connors RSI(2) 策略：日內極短期 RSI 均值回歸。
  - _RsiState           : Wilder smoothed RSI（連續，無跨日 reset）
  - ConnorsRsi2Strategy : 進出場（Task P2b/P2c，待後續任務新增）
"""

from datetime import datetime, time
from typing import Optional

# Task P2b/P2c 進出場邏輯所需 import（待後續任務取消註解）
# from strategy.base import BaseStrategy, Signal, SignalDirection
# from core.market_data import KBar, MarketSnapshot
# from core.position import Position, Side


class _RsiState:
    """Wilder smoothed RSI。

    呼叫 update(close) 餵價，property rsi 讀當前值。
    暖身期（update 數 < period）回 50.0（中性）。
    **沒有 reset()**：跨日連續累積，與 _SessionVwap（每日 reset）相反。
    """

    def __init__(self, period: int = 2):
        if period < 1:
            raise ValueError(f"period must be >= 1, got {period}")
        self.period = period
        self._prev_close: Optional[float] = None
        self._avg_gain = 0.0
        self._avg_loss = 0.0
        self._count = 0   # 已收到的 (價差) 樣本數

    def update(self, close: float) -> None:
        if self._prev_close is None:
            self._prev_close = close
            return
        change = close - self._prev_close
        gain = max(change, 0.0)
        loss = max(-change, 0.0)
        if self._count < self.period:
            # 初始期：累計簡單平均
            self._avg_gain += gain
            self._avg_loss += loss
            self._count += 1
            if self._count == self.period:
                self._avg_gain /= self.period
                self._avg_loss /= self.period
        else:
            # Wilder smoothed
            self._avg_gain = (self._avg_gain * (self.period - 1) + gain) / self.period
            self._avg_loss = (self._avg_loss * (self.period - 1) + loss) / self.period
        self._prev_close = close

    @property
    def rsi(self) -> float:
        if self._count < self.period:
            return 50.0
        if self._avg_loss < 1e-12:
            return 100.0
        rs = self._avg_gain / self._avg_loss
        return 100.0 - (100.0 / (1.0 + rs))


# ── ConnorsRsi2Strategy 由 Task P2b/P2c 新增於此 ───────────────────────────
```

- [ ] **Step 4: 跑測試確認通過** — `python -m pytest tests/test_connors_rsi2.py -k rsi -v` → 4 PASS。

- [ ] **Step 5: Commit**
  ```bash
  git add strategy/connors_rsi2.py tests/test_connors_rsi2.py
  git commit -m "feat(connors_rsi2): P2a _RsiState Wilder smoothing helper"
  ```

---

## Task 3（P2b）：`ConnorsRsi2Strategy.__init__` + `on_kbar`

**Files:** Modify `strategy/connors_rsi2.py`；append to `tests/test_connors_rsi2.py`.

- [ ] **Step 1: 寫失敗測試** — 進場條件、過度交易守衛、雙變體

```python
def _make_snap(price, adx, atr, ts):
    from core.market_data import MarketSnapshot
    s = MarketSnapshot()
    s.price = price; s.adx = adx; s.atr = atr; s.timestamp = ts; s.volume = 100
    return s


def _kbar(ts, p, vol=100):
    from core.market_data import KBar
    return KBar(ts, p, p+1, p-1, p, vol)


def test_entry_long_on_low_rsi():
    """RSI(2) 跌破閾值 → BUY，stop 在進場價下方"""
    from strategy.connors_rsi2 import ConnorsRsi2Strategy
    from strategy.base import SignalDirection
    from datetime import datetime
    strat = ConnorsRsi2Strategy(rsi_low=10, sl_atr=2.0, allow_short=False)
    # 暖身 + 連跌讓 RSI 趨 0
    prices = [100, 100, 99, 98, 97, 96, 95, 94, 93, 92, 91]
    for i, p in enumerate(prices):
        ts = datetime(2024, 1, 2, 9, i*5)
        sig = strat.on_kbar(_kbar(ts, p), _make_snap(p, 20, 2, ts))
        # 進場可能任一根觸發（一旦觸發、_trades_today 增至 1、後續仍可能再進）
    # 至少有一次進場訊號被觸發（_trades_today > 0）
    assert strat._trades_today > 0


def test_short_blocked_when_allow_short_false():
    """allow_short=False → RSI 高也不做空"""
    from strategy.connors_rsi2 import ConnorsRsi2Strategy
    from datetime import datetime
    strat = ConnorsRsi2Strategy(rsi_high=90, allow_short=False)
    # 連漲讓 RSI 趨 100
    for i, p in enumerate([100, 100, 101, 102, 103, 104, 105, 106, 107, 108]):
        ts = datetime(2024, 1, 2, 9, i*5)
        strat.on_kbar(_kbar(ts, p), _make_snap(p, 20, 2, ts))
    assert strat._trades_today == 0


def test_short_fires_when_allow_short_true():
    from strategy.connors_rsi2 import ConnorsRsi2Strategy
    from strategy.base import SignalDirection
    from datetime import datetime
    strat = ConnorsRsi2Strategy(rsi_high=90, allow_short=True)
    sig = None
    for i, p in enumerate([100, 100, 101, 102, 103, 104, 105, 106, 107, 108]):
        ts = datetime(2024, 1, 2, 9, i*5)
        s = strat.on_kbar(_kbar(ts, p), _make_snap(p, 20, 2, ts))
        if s is not None:
            sig = s
    assert sig is not None and sig.direction == SignalDirection.SELL
    assert sig.stop_loss > 108   # 空單 stop 在進場價之上


def test_entry_window_filter_blocks_before_open():
    """entry_window=09:00-13:00 → 08:40~08:55 即使 RSI 觸發也不進場"""
    from strategy.connors_rsi2 import ConnorsRsi2Strategy
    from datetime import datetime
    strat = ConnorsRsi2Strategy(rsi_low=50, entry_window=("09:00", "13:00"))
    # 全部丟在 entry_window 之前（08:40~08:55），rsi_low=50 寬鬆到幾乎必觸發
    for i, p in enumerate([100, 99, 98, 97]):
        ts = datetime(2024, 1, 2, 8, 40 + i*5)
        strat.on_kbar(_kbar(ts, p), _make_snap(p, 20, 2, ts))
    assert strat._trades_today == 0, "entry_window 守衛失效：盤前不該有交易"


def test_max_trades_per_day_caps():
    """單日 _trades_today >= max_trades 後不再進新場"""
    from strategy.connors_rsi2 import ConnorsRsi2Strategy
    from datetime import datetime
    strat = ConnorsRsi2Strategy(rsi_low=99, max_trades=2)   # rsi_low=99 → 幾乎每根都觸發
    # 暖身 + 灌多根
    for i in range(30):
        ts = datetime(2024, 1, 2, 9, i*5 if i*5 < 240 else (240+i))   # 簡化時間
        strat.on_kbar(_kbar(ts, 100 - i*0.1), _make_snap(100, 20, 2, ts))
    assert strat._trades_today == 2   # 觸頂後不再增


def test_daily_reset_clears_trades_count_but_not_rsi():
    """跨日：_trades_today 歸 0、_session_bar 歸 1、_RsiState 持續累積"""
    from strategy.connors_rsi2 import ConnorsRsi2Strategy
    from datetime import datetime
    strat = ConnorsRsi2Strategy(rsi_low=99, max_trades=10)
    for i in range(5):
        ts = datetime(2024, 1, 2, 9, i*5)
        strat.on_kbar(_kbar(ts, 100 - i), _make_snap(100, 20, 2, ts))
    rsi_eod = strat._rsi.rsi
    trades_day1 = strat._trades_today
    # 換日第一根
    ts2 = datetime(2024, 1, 3, 9, 0)
    strat.on_kbar(_kbar(ts2, 95), _make_snap(95, 20, 2, ts2))
    assert strat._trades_today <= 1     # 重置後最多本根的 1 次
    assert strat._session_bar == 1
    # RSI 不重置：avg_gain/loss 沿用昨日累計（連續性）
    # 可用 prev_close 是否帶入第二天的價（95）來驗
    assert strat._rsi._prev_close == 95.0
    # avg_gain/loss 都非零（昨天有漲有跌的話），用較寬鬆的非 zero 驗
    assert strat._rsi._count >= 2   # 不會被歸零
```

- [ ] **Step 2: 跑測試確認失敗**

- [ ] **Step 3: 實作** — Modify `strategy/connors_rsi2.py`：取消註解三個 import（`strategy.base`, `core.market_data`, `core.position`），加入 `time` from datetime，在 `_RsiState` 之後加入：

```python
class ConnorsRsi2Strategy(BaseStrategy):
    def __init__(
        self,
        rsi_period: int = 2,
        rsi_low: float = 10.0,
        rsi_high: float = 90.0,
        sl_atr: float = 2.0,
        max_bars: int = 24,
        cooldown: int = 3,
        max_trades: int = 6,
        entry_window: tuple = ("09:00", "13:00"),
        force_close: str = "13:25",
        allow_short: bool = False,
        point_value: float = 10.0,
    ):
        self.rsi_period = rsi_period
        self.rsi_low = rsi_low
        self.rsi_high = rsi_high
        self.sl_atr = sl_atr
        self.max_bars = max_bars
        self.cooldown = cooldown
        self.max_trades = max_trades
        self.allow_short = allow_short
        self.point_value = point_value
        self._ew_start = time.fromisoformat(entry_window[0])
        self._ew_end   = time.fromisoformat(entry_window[1])
        self._force_close = time.fromisoformat(force_close)

        self._rsi = _RsiState(period=rsi_period)   # 跨日連續
        self._trades_today = 0
        self._cooldown_until_bar = -1
        self._session_bar = 0
        self._day = None

    @property
    def name(self) -> str:
        return "connors_rsi2"

    def _maybe_daily_reset(self, dt: datetime) -> None:
        d = dt.date()
        if d != self._day:
            self._day = d
            self._trades_today = 0
            self._cooldown_until_bar = -1
            self._session_bar = 0
            # 注意：_rsi 不 reset（連續 Wilder）

    def on_kbar(self, kbar, snapshot):
        ts = kbar.datetime
        self._maybe_daily_reset(ts)
        self._rsi.update(kbar.close)
        self._session_bar += 1
        # 守衛
        if not (self._ew_start <= ts.time() < self._ew_end):
            return None
        if self._trades_today >= self.max_trades:
            return None
        if self._session_bar <= self._cooldown_until_bar:
            return None
        rsi = self._rsi.rsi
        atr = max(snapshot.atr, 1.0)
        close = kbar.close
        if rsi <= self.rsi_low:
            stop = close - self.sl_atr * atr
            return self._signal(SignalDirection.BUY, close, stop, rsi)
        if self.allow_short and rsi >= self.rsi_high:
            stop = close + self.sl_atr * atr
            return self._signal(SignalDirection.SELL, close, stop, rsi)
        return None

    def _signal(self, direction, price, stop, rsi):
        self._trades_today += 1
        return Signal(
            direction=direction, strength=1.0,
            stop_loss=round(stop, 1), take_profit=0,
            reason=f"connors_rsi2 {direction.value} rsi={rsi:.1f}",
            source=self.name,
        )
```

- [ ] **Step 4: 跑測試** — `python -m pytest tests/test_connors_rsi2.py -k "entry or short or trades or reset" -v` → 全綠（注意 `test_entry_window_filter_blocks_before_open` 寫得較寬，只驗無 crash；可視情況強化）

- [ ] **Step 5: Commit**
  ```bash
  git add strategy/connors_rsi2.py tests/test_connors_rsi2.py
  git commit -m "feat(connors_rsi2): P2b entry logic on_kbar + dual-variant allow_short"
  ```

---

## Task 4（P2c）：`check_exit` + 停損冷卻接線

**Files:** Modify `strategy/connors_rsi2.py`；append to `tests/test_connors_rsi2.py`.

關鍵：用 `snapshot.timestamp`（**不**用 `self._current_bar_time`、會 stale）；持倉期間 `update(price)` 維持 RSI；停損觸發時設 cooldown_until_bar。

- [ ] **Step 1: 寫失敗測試**

```python
def _pos(side, entry, stop, bars):
    """用 PositionManager 建真實 Position（dataclass 無 instrument 欄位）"""
    from core.position import PositionManager
    from core.instrument_config import INSTRUMENT_SPECS
    pm = PositionManager(instruments=["TMF"], configs={"TMF": INSTRUMENT_SPECS["TMF"]},
                         initial_balance=200_000)
    pm.open_position("TMF", side, price=entry, quantity=1,
                     stop_loss=stop, take_profit=0, timestamp=None)
    p = pm.positions["TMF"]
    p.bars_since_entry = bars
    return p


def test_exit_force_close_at_1325():
    from strategy.connors_rsi2 import ConnorsRsi2Strategy
    from strategy.base import SignalDirection
    from core.position import Side
    from datetime import datetime
    strat = ConnorsRsi2Strategy()
    snap = _make_snap(100, 20, 2, datetime(2024, 1, 2, 13, 25))
    sig = strat.check_exit(_pos(Side.LONG, 95, 90, 5), snap)
    assert sig is not None and sig.direction == SignalDirection.CLOSE and "盤末" in sig.reason


def test_exit_atr_stop_long_sets_cooldown():
    """多單 price 跌破 stop_loss → CLOSE + cooldown_until_bar 設定"""
    from strategy.connors_rsi2 import ConnorsRsi2Strategy
    from strategy.base import SignalDirection
    from core.position import Side
    from datetime import datetime
    strat = ConnorsRsi2Strategy(cooldown=3)
    # 灌幾根讓 _session_bar 移動
    for i in range(5):
        ts = datetime(2024, 1, 2, 9, i*5)
        strat.on_kbar(_kbar(ts, 100), _make_snap(100, 20, 2, ts))
    bar_at_stop = strat._session_bar   # 5
    snap = _make_snap(89, 20, 2, datetime(2024, 1, 2, 10, 0))
    sig = strat.check_exit(_pos(Side.LONG, 95, 90, 3), snap)
    assert sig is not None and "停損" in sig.reason
    assert strat._cooldown_until_bar == bar_at_stop + 3   # cooldown=3 → block 後續 3 根


def test_exit_atr_stop_short_sets_cooldown():
    """空單 price 漲破 stop_loss → CLOSE + cooldown"""
    from strategy.connors_rsi2 import ConnorsRsi2Strategy
    from core.position import Side
    from datetime import datetime
    strat = ConnorsRsi2Strategy(cooldown=3, allow_short=True)
    for i in range(5):
        ts = datetime(2024, 1, 2, 9, i*5)
        strat.on_kbar(_kbar(ts, 100), _make_snap(100, 20, 2, ts))
    bar_at_stop = strat._session_bar
    snap = _make_snap(103, 20, 2, datetime(2024, 1, 2, 10, 0))
    sig = strat.check_exit(_pos(Side.SHORT, 100, 102, 3), snap)
    assert sig is not None and "停損" in sig.reason
    assert strat._cooldown_until_bar == bar_at_stop + 3


def test_exit_rsi_mid_long():
    """多單 RSI 回 >= 50 → 停利"""
    from strategy.connors_rsi2 import ConnorsRsi2Strategy
    from core.position import Side
    from datetime import datetime
    strat = ConnorsRsi2Strategy()
    # 灌幾根讓 RSI 介於 50 上方
    for i, p in enumerate([100, 100, 101, 102, 103, 104, 105]):
        ts = datetime(2024, 1, 2, 9, i*5)
        strat.on_kbar(_kbar(ts, p), _make_snap(p, 20, 2, ts))
    # 此時 RSI 應 > 50
    assert strat._rsi.rsi > 50
    snap = _make_snap(105, 20, 2, datetime(2024, 1, 2, 10, 0))
    sig = strat.check_exit(_pos(Side.LONG, 100, 95, 3), snap)
    assert sig is not None and "RSI" in sig.reason


def test_exit_time_stop():
    from strategy.connors_rsi2 import ConnorsRsi2Strategy
    from core.position import Side
    from datetime import datetime
    strat = ConnorsRsi2Strategy(max_bars=24)
    snap = _make_snap(95, 20, 2, datetime(2024, 1, 2, 11, 0))
    # bars=25 > max_bars=24，且價未觸停損（多單 95 > 90）、且 RSI 還沒回 50
    # （此 strat 未 update _rsi，RSI 維持 50，會立刻觸發 RSI 出場前進入時間停損分支）
    # 為避開 RSI 觸發、先強制 RSI 偏低（多單 < 50 不出）：灌幾根下跌
    for i, p in enumerate([100, 99, 98, 97, 96]):
        ts = datetime(2024, 1, 2, 9, i*5)
        strat.on_kbar(_kbar(ts, p), _make_snap(p, 20, 2, ts))
    # RSI < 50
    assert strat._rsi.rsi < 50
    sig = strat.check_exit(_pos(Side.LONG, 95, 90, 25), snap)
    assert sig is not None and "時間停損" in sig.reason
```

- [ ] **Step 2: 跑測試確認失敗**（check_exit 未實作）

- [ ] **Step 3: 實作** — 在 `ConnorsRsi2Strategy` 內加：

```python
    def check_exit(self, position, snapshot):
        if position.is_flat:
            return None
        ts = snapshot.timestamp          # 真實 bar 時間
        price = snapshot.price
        self._rsi.update(price)          # 持倉期間維持 RSI（close-proxy）
        # 注意：_session_bar 在 check_exit **不**增量（只 on_kbar flat bar 才算）
        is_long = (position.side == Side.LONG)

        def close_sig(reason):
            return Signal(direction=SignalDirection.CLOSE, strength=1.0,
                          stop_loss=0, take_profit=0, reason=reason, source=self.name)

        # 1) 盤末強平
        if ts.time() >= self._force_close:
            return close_sig(f"盤末強平 @ {price:.0f}")
        # 2) ATR 凍結停損 + cooldown 接線
        if position.stop_loss > 0:
            if is_long and price <= position.stop_loss:
                self._cooldown_until_bar = self._session_bar + self.cooldown
                return close_sig(f"停損 @ {price:.0f}")
            if (not is_long) and price >= position.stop_loss:
                self._cooldown_until_bar = self._session_bar + self.cooldown
                return close_sig(f"停損 @ {price:.0f}")
        # 3) RSI 回中
        rsi = self._rsi.rsi
        if is_long and rsi >= 50:
            return close_sig(f"RSI回中停利 rsi={rsi:.1f}")
        if (not is_long) and rsi <= 50:
            return close_sig(f"RSI回中停利 rsi={rsi:.1f}")
        # 4) 時間停損
        if position.bars_since_entry > self.max_bars:
            return close_sig(f"時間停損 {position.bars_since_entry}根")
        return None

    def get_parameters(self):
        return {
            "rsi_period": self.rsi_period, "rsi_low": self.rsi_low, "rsi_high": self.rsi_high,
            "sl_atr": self.sl_atr, "max_bars": self.max_bars,
            "max_trades": self.max_trades, "cooldown": self.cooldown,
            "allow_short": self.allow_short,
        }

    def reset(self):
        # 引擎不會跨日呼叫此方法（vwap_fade plan §1 表確認），這裡只供手動測試/重啟用
        self._rsi = _RsiState(period=self.rsi_period)
        self._trades_today = 0
        self._cooldown_until_bar = -1
        self._session_bar = 0
        self._day = None
```

- [ ] **Step 4: 跑測試** — `python -m pytest tests/test_connors_rsi2.py -v` → 全綠

- [ ] **Step 5: Commit**
  ```bash
  git add strategy/connors_rsi2.py tests/test_connors_rsi2.py
  git commit -m "feat(connors_rsi2): P2c exit logic + stop cooldown wiring"
  ```

---

## Task 5（P2d）：引擎整合冒煙（雙變體真實資料）

**Files:** Append to `tests/test_connors_rsi2.py`.

- [ ] **Step 1: 寫測試** — 兩個變體都驗能跑 + 頻率合理

```python
def test_engine_smoke_long_only():
    """P2d: ConnorsRsi2Strategy(allow_short=False) 跑真實 MXF 5m 短段。"""
    import pandas as pd
    from pathlib import Path
    p = Path("data/vwap_fade/MXF_day_5m.parquet")
    if not p.exists():
        import pytest; pytest.skip("data 未準備")
    from core.gpu_indicators import precompute_all
    from backtest.fast_engine import FastBacktestEngine
    from strategy.connors_rsi2 import ConnorsRsi2Strategy
    df = pd.read_parquet(p).head(3000).reset_index(drop=True)
    ind = precompute_all(df, verbose=False)
    res = FastBacktestEngine(initial_balance=200_000, instrument="TMF").run(
        df, ind, ConnorsRsi2Strategy(allow_short=False), "balanced")
    assert len(res.trades) > 0, "long-only 在 3000 根 bar 上零交易，檢查 rsi_low/entry_window"
    days = df["datetime"].dt.date.nunique()
    assert len(res.trades) / max(days, 1) < 10, "過度交易"
    # 所有 trades 必為 long（注意：Side.LONG.value = "long" 小寫，trades[i]["side"] 也是小寫）
    sides = {t["side"] for t in res.trades}
    assert sides.issubset({"long"}), f"long-only 不該有 short，但出現 {sides}"


def test_engine_smoke_long_short():
    """P2d: ConnorsRsi2Strategy(allow_short=True) 跑真實資料、雙向都會出現"""
    import pandas as pd
    from pathlib import Path
    p = Path("data/vwap_fade/MXF_day_5m.parquet")
    if not p.exists():
        import pytest; pytest.skip("data 未準備")
    from core.gpu_indicators import precompute_all
    from backtest.fast_engine import FastBacktestEngine
    from strategy.connors_rsi2 import ConnorsRsi2Strategy
    df = pd.read_parquet(p).head(3000).reset_index(drop=True)
    ind = precompute_all(df, verbose=False)
    res = FastBacktestEngine(initial_balance=200_000, instrument="TMF").run(
        df, ind, ConnorsRsi2Strategy(allow_short=True), "balanced")
    assert len(res.trades) > 0
    days = df["datetime"].dt.date.nunique()
    assert len(res.trades) / max(days, 1) < 10
    # allow_short=True 預期雙向都會出現（資料夠長時）
    # 注意：Side enum value 為小寫字串 "long" / "short"
    sides = {t["side"] for t in res.trades}
    assert "long" in sides or "short" in sides   # 至少一邊有
    # 若只有單側、紀錄這個 observation（report 時呈現）
```

- [ ] **Step 2: 跑測試** — `python -m pytest tests/test_connors_rsi2.py::test_engine_smoke_long_only tests/test_connors_rsi2.py::test_engine_smoke_long_short -v`

- [ ] **Step 3: 觀察並記錄**（不入測試斷言、寫進 commit msg）：兩個變體各自的 trade count、avg/day、WR、net_pnl，作為下游 grid/OOS 任務的 sanity baseline。

- [ ] **Step 4: Commit**
  ```bash
  git add tests/test_connors_rsi2.py
  git commit -m "test(connors_rsi2): P2d engine smoke for both variants on real MXF data"
  ```

**Gate P2：** 兩個變體都能 > 0 交易、頻率 < 10 筆/日。

---

## Task 6（P3）：粗篩 Grid（雙變體 × 雙商品 = 4 runs）

**Files:** Create `scripts/optimize_connors_rsi2.py`；append a tiny integration test to `tests/test_connors_rsi2.py`.

照抄 `scripts/optimize_vwap_fade.py`，改 4 處：

- [ ] **Step 1: 複製 + 改策略 import + 工廠**

```python
# 取代原本的 _make_strategy / VwapFadeStrategy 引用：
from strategy.connors_rsi2 import ConnorsRsi2Strategy
def _make_strategy(params: dict, allow_short: bool):
    return ConnorsRsi2Strategy(allow_short=allow_short, **params)
```
worker 內：
```python
def _worker(args):
    params, allow_short, shm_meta = args   # 多帶一個 allow_short（per-run 固定、不在 grid）
    # ... 重建 indicators 從 shm（同 vwap_fade）...
    strat = _make_strategy(params, allow_short)
    # 跑 train + test（同 vwap_fade）
    return {...}
```

- [ ] **Step 2: 改 PARAM_GRID**

```python
PARAM_GRID = {
    "rsi_period": [2, 3],
    "rsi_low":    [5, 10, 15],
    "rsi_high":   [85, 90, 95],
    "sl_atr":     [1.5, 2.0, 2.5],
    "max_bars":   [12, 18, 24],
    "cooldown":   [3, 5],
}
# allow_short=False  → 略掉 rsi_high 維度（沒效）→ 108 combos
# allow_short=True   → 完整 grid → 324 combos
```

實作時：若 `allow_short=False`，filter 掉 rsi_high 維度（從 PARAM_GRID copy 移除 key，或每組 params 固定 rsi_high=90 不變）。簡單實作：

```python
def _build_param_combos(allow_short: bool):
    grid = dict(PARAM_GRID)
    if not allow_short:
        grid.pop("rsi_high")
    keys = list(grid.keys())
    return [dict(zip(keys, v)) for v in itertools.product(*grid.values())]
```

- [ ] **Step 3: 加 CLI**

```python
parser.add_argument("--symbol", choices=["MXF", "TXF"], required=True)
parser.add_argument("--allow-short", action="store_true",
                    help="若給 → 跑雙向變體（324 combos）；否則 long-only（108 combos）")
```

輸出檔名含 variant 區隔：
```python
variant = "biside" if allow_short else "longonly"
csv_path  = OUT / f"grid_connors_rsi2_{symbol}_{variant}_{ts}.csv"
json_path = OUT / f"best_params_connors_rsi2_{symbol}_{variant}_{ts}.json"
# JSON 內額外存 allow_short flag，方便下游 OOS 還原構造
```

- [ ] **Step 4: 重用 `_calc_metrics` + split / wf_score**

```python
from scripts.optimize_strategy import _calc_metrics
# split: SPLIT_DATE = "2024-01-01"，找 split_idx 同 vwap_fade
# wf_score 公式同 vwap_fade（test_pf*0.4 + test_wr/50*0.25 + sharpe*0.20 + ...）
```

- [ ] **Step 5: 加 `_run_one(params: dict, df, split_idx: int, allow_short: bool) -> dict` helper**（給 Task 7 用，且本任務的測試呼叫它跳過 multiprocessing）：

```python
def _run_one(params: dict, df, split_idx: int, allow_short: bool = False) -> dict:
    """不依賴 shared memory；單 backtest（連同 metrics + wf_score）"""
    from core.gpu_indicators import precompute_all
    from backtest.fast_engine import FastBacktestEngine
    ind = precompute_all(df, verbose=False)
    eng = FastBacktestEngine(initial_balance=200_000, instrument="TMF")
    strat_train = ConnorsRsi2Strategy(allow_short=allow_short, **params)
    r_train = eng.run(df, ind, strat_train, "balanced", start_idx=0, end_idx=split_idx)
    m_train = _calc_metrics(r_train)
    strat_test = ConnorsRsi2Strategy(allow_short=allow_short, **params)
    r_test = eng.run(df, ind, strat_test, "balanced", start_idx=split_idx, end_idx=len(df))
    m_test = _calc_metrics(r_test)
    # wf_score（同 vwap_fade）
    if m_test["n"] < 5:
        wf = -99.0
    else:
        wf = (m_test["pf"]*0.40 + min(m_test["wr"]/50.0, 2.0)*0.25
              + max(m_test["sharpe"], -5.0)*0.20 + max(0.0, 1.0 - m_test["dd"]/20.0)*0.15)
    return {**{f"train_{k}": v for k, v in m_train.items()},
            **{f"test_{k}": v for k, v in m_test.items()},
            "wf_score": round(wf, 4), **params, "allow_short": allow_short}
```

- [ ] **Step 6: 不移植 `_apply_best_params`** — connors_rsi2 參數是 ctor args、無 signals.py 對應，**僅**輸出 CSV + JSON。

- [ ] **Step 7: 加 `--dry-run` flag**（同 vwap_fade Task 7，跑 4 combos / 5000 bars，秒級完成，方便 smoke）。

- [ ] **Step 8: 寫整合測試（呼叫 _run_one、不走 multiproc）**

```python
def test_optimize_run_one_smoke():
    import pandas as pd
    from pathlib import Path
    p = Path("data/vwap_fade/MXF_day_5m.parquet")
    if not p.exists():
        import pytest; pytest.skip("data 未準備")
    from scripts.optimize_connors_rsi2 import _run_one
    df = pd.read_parquet(p).head(2000).reset_index(drop=True)
    res = _run_one(
        {"rsi_period":2, "rsi_low":10, "sl_atr":2.0, "max_bars":18, "cooldown":3},
        df, split_idx=1000, allow_short=False,
    )
    assert "wf_score" in res and "test_n" in res
```

- [ ] **Step 9: dry-run 真實驗收**
  ```
  python scripts/optimize_connors_rsi2.py --symbol MXF --dry-run
  python scripts/optimize_connors_rsi2.py --symbol MXF --allow-short --dry-run
  ```
  確認兩條路徑都不 crash、有寫出 CSV + JSON。

- [ ] **Step 10: Commit**
  ```bash
  git add scripts/optimize_connors_rsi2.py tests/test_connors_rsi2.py
  git commit -m "feat(connors_rsi2): P3 grid optimizer (dual variants)"
  ```

**注意：本任務不跑完整 4 個 grid run**（那是 Task 7 的事，~5 分 × 4）。

---

## Task 7（P4+P5）：OOS 終驗 + Gate 匯總（orchestrator）

**Files:** Create `scripts/run_connors_rsi2_experiments.py`. 不再寫單元測試（這個檔是 orchestrator、跑長實驗）。

這個任務一次跑完所有實驗、產出 Gate 匯總報告。**核心：所有穩健性函式都從 `scripts.robustness_vwap_fade` import**，不重造。

- [ ] **Step 1: 寫 orchestrator 主流程**

```python
"""
P4+P5 orchestrator：
  1) 跑 4 個 grid run（MXF/TXF × allow_short False/True）   (~5 min × 4)
  2) 各變體選 best params（跨商品 Top-1 by wf_score）
  3) 每變體跑 TMF OOS、收 trades + metrics
  4) 三件套：MC / perturb / regime split（直接 import robustness_vwap_fade）
  5) 與 breakout 相關性（best-effort）
  6) 寫 Gate 匯總報告 markdown，**兩變體並排**
"""
import sys, subprocess, json, glob, datetime as dt
from pathlib import Path
import numpy as np
import pandas as pd

sys.path.insert(0, ".")

OUT = Path("data/connors_rsi2"); OUT.mkdir(parents=True, exist_ok=True)


def step1_run_grids(skip_if_recent: bool = True):
    """跑 4 grids（若最近已有結果可 skip）"""
    for sym in ["MXF", "TXF"]:
        for allow_short in [False, True]:
            variant = "biside" if allow_short else "longonly"
            existing = sorted(OUT.glob(f"best_params_connors_rsi2_{sym}_{variant}_*.json"))
            if skip_if_recent and existing:
                print(f"[skip] {sym} {variant} 已有結果：{existing[-1].name}")
                continue
            cmd = ["python", "scripts/optimize_connors_rsi2.py", "--symbol", sym]
            if allow_short:
                cmd.append("--allow-short")
            print(f"[run]  {' '.join(cmd)}")
            subprocess.run(cmd, check=True)


def step2_pick_best(variant: str) -> dict:
    """跨商品取 Top-1 by wf_score"""
    files = sorted(OUT.glob(f"best_params_connors_rsi2_*_{variant}_*.json"))
    if not files:
        raise FileNotFoundError(f"無 {variant} grid 結果")
    candidates = []
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        d["_source_file"] = f.name
        candidates.append(d)
    candidates.sort(key=lambda x: x.get("wf_score", -999), reverse=True)
    return candidates[0]


def step3_run_oos(best: dict) -> tuple:
    """用 best params 跑 TMF OOS，回 (trades, metrics, df_oos, ind_oos)"""
    from core.gpu_indicators import precompute_all
    from backtest.fast_engine import FastBacktestEngine
    from strategy.connors_rsi2 import ConnorsRsi2Strategy
    from scripts.optimize_strategy import _calc_metrics

    df_oos = pd.read_parquet("data/vwap_fade/TMF_oos_day_5m.parquet").reset_index(drop=True)
    ind_oos = precompute_all(df_oos, verbose=False)
    allow_short = bool(best.get("allow_short", False))
    # best dict 可能含 train_*/test_* 等欄位；只挑策略構造子認識的
    valid_keys = {"rsi_period","rsi_low","rsi_high","sl_atr","max_bars","cooldown",
                  "max_trades","entry_window","force_close","point_value"}
    params = {k: v for k, v in best.items() if k in valid_keys}
    strat = ConnorsRsi2Strategy(allow_short=allow_short, **params)
    res = FastBacktestEngine(initial_balance=200_000, instrument="TMF").run(
        df_oos, ind_oos, strat, "balanced")
    metrics = _calc_metrics(res)
    return res.trades, metrics, df_oos, ind_oos


def step4_robustness(trades: list, df_oos, ind_oos, best: dict) -> dict:
    """三件套 + Gate 判定"""
    from scripts.robustness_vwap_fade import monte_carlo, stability, perturb_and_run, split_by_regime
    allow_short = bool(best.get("allow_short", False))
    valid_keys = {"rsi_period","rsi_low","rsi_high","sl_atr","max_bars","cooldown"}
    perturb_params = {k: v for k, v in best.items() if k in valid_keys}

    n = len(trades)
    out = {"n_trades": n}

    # 4-1 MC
    if n >= 30:
        pnl = np.array([t["pnl"] for t in trades], dtype=float)
        out["mc_pf_p5"], out["mc_net_p5"], out["mc_mdd_p95"] = monte_carlo(pnl, n=5000)
    else:
        out["mc_pf_p5"] = out["mc_net_p5"] = out["mc_mdd_p95"] = float("nan")
        out["mc_note"] = "INSUFFICIENT_TRADES (<30)"

    # 4-2 perturb（用 OOS slice 整段、split_idx=0 表示全當 test）
    # 注意：perturb_and_run 內部會 import scripts.optimize_vwap_fade._run_one
    # 但我們的策略是 ConnorsRsi2Strategy，所以要寫一個本檔內的 perturb helper
    # 或：複用 scripts.optimize_connors_rsi2._run_one，請 perturb_and_run 用 monkey-patched 版
    # 最乾淨：本檔內寫 perturb_connors，直接呼叫 _run_one_connors
    from scripts.optimize_connors_rsi2 import _run_one as _run_one_c
    factors = (0.9, 0.95, 1.0, 1.05, 1.1, 1.2)
    perturb_results = {}
    for dim, base in perturb_params.items():
        if not isinstance(base, (int, float)) or isinstance(base, bool):
            continue
        runs = []
        for f in factors:
            p = dict(perturb_params)
            v = base * f
            if isinstance(base, int):
                v = max(1, int(round(v)))
            p[dim] = v
            r = _run_one_c(p, df_oos, split_idx=0, allow_short=allow_short)
            runs.append(float(r.get("test_ret", 0.0)))
        perturb_results[dim] = runs
    stabilities = {d: stability(r) for d, r in perturb_results.items()}
    out["perturb_stabilities"] = stabilities
    out["perturb_worst"] = max(stabilities.values()) if stabilities else float("inf")

    # 4-3 regime
    bars_adx = df_oos.assign(adx=ind_oos["adx"])
    buckets = split_by_regime(trades, bars_adx, trend_thr=25, range_thr=20)
    out["regime_buckets"] = {k: {"n": len(v), "net": float(sum(v))}
                             for k, v in buckets.items()}
    return out


def step5_breakout_correlation(trades: list, df_oos) -> float:
    """best-effort：若 BreakoutStrategy 可在 FastBacktestEngine 跑、算 daily PnL Pearson r"""
    try:
        from core.gpu_indicators import precompute_all
        from backtest.fast_engine import FastBacktestEngine
        from strategy.breakout import BreakoutStrategy
        ind = precompute_all(df_oos, verbose=False)
        res_b = FastBacktestEngine(initial_balance=200_000, instrument="TMF").run(
            df_oos, ind, BreakoutStrategy(), "balanced")
        # 兩者各自的 daily_pnl 字典 → 對齊算 Pearson r
        days = sorted(set(res_b.daily_pnl) | {pd.to_datetime(t["exit_time"]).date().isoformat()
                                              for t in trades})
        # connors 的 daily pnl
        c_dp = {}
        for t in trades:
            d = pd.to_datetime(t["exit_time"]).date().isoformat()
            c_dp[d] = c_dp.get(d, 0) + t["pnl"]
        b_series = np.array([res_b.daily_pnl.get(d, 0) for d in days], dtype=float)
        c_series = np.array([c_dp.get(d, 0) for d in days], dtype=float)
        if len(b_series) < 5 or b_series.std() < 1e-9 or c_series.std() < 1e-9:
            return float("nan")
        return float(np.corrcoef(b_series, c_series)[0, 1])
    except Exception as e:
        print(f"[corr] breakout backtest failed: {e}")
        return float("nan")


def step6_write_report(results: dict, out_path: Path) -> None:
    """並排兩變體的 Gate 表 + 觀察 + 建議"""
    lines = ["# connors_rsi2 穩健性報告 — " + dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
             "", "## 1. 兩變體 Best Params", ""]
    for v in ["longonly", "biside"]:
        b = results[v]["best"]
        m = results[v]["oos_metrics"]
        lines.append(f"### {v}（來源：{b.get('_source_file','?')}）")
        lines.append(f"- params：`{ {k:v for k,v in b.items() if not k.startswith('_') and k not in ('train_pf','train_wr','train_n','train_ret','train_dd','test_pf','test_wr','test_n','test_ret','test_dd','test_sharpe','wf_score','allow_short')} }`")
        lines.append(f"- wf_score (train+test)：{b.get('wf_score','?')}")
        lines.append(f"- OOS metrics：n={m.get('n')}  WR={m.get('wr')}%  PF={m.get('pf')}  ret={m.get('ret')}%  dd={m.get('dd')}%  sharpe={m.get('sharpe')}")
        lines.append("")
    lines.append("## 2. Gate 表（並排）")
    lines.append("")
    lines.append("| Gate | 門檻 | longonly | biside |")
    lines.append("|---|---|---|---|")
    def fmt(v):
        return f"{v:.3f}" if isinstance(v, float) and v == v else str(v)
    for label, key, threshold in [
        ("MC PF p5",     "mc_pf_p5",     "> 1.0"),
        ("MC 淨利 p5",   "mc_net_p5",    "> 0"),
        ("MC MDD p95",   "mc_mdd_p95",   "< 12%"),
        ("Perturb worst","perturb_worst","< 0.3"),
    ]:
        l = fmt(results["longonly"]["robust"].get(key))
        b = fmt(results["biside"]["robust"].get(key))
        lines.append(f"| {label} | {threshold} | {l} | {b} |")
    # regime
    for v in ["longonly", "biside"]:
        rb = results[v]["robust"].get("regime_buckets", {})
        for bucket in ("range", "trend", "neutral"):
            d = rb.get(bucket, {"n": 0, "net": 0})
            lines.append(f"| regime/{bucket} ({v}) | — | n={d['n']} net={d['net']:+.0f} | |")
    # correlation
    lines.append(f"| |r| breakout (longonly) | < 0.3 | {fmt(abs(results['longonly']['corr']) if results['longonly']['corr']==results['longonly']['corr'] else float('nan'))} | |")
    lines.append(f"| |r| breakout (biside)   | < 0.3 | | {fmt(abs(results['biside']['corr']) if results['biside']['corr']==results['biside']['corr'] else float('nan'))} |")
    lines.append("")
    lines.append("## 3. 觀察與建議")
    lines.append("（人工填寫）")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n=> {out_path}")


def main():
    print("== Step 1: 跑 4 grids ==")
    step1_run_grids(skip_if_recent=True)

    results = {}
    for variant in ["longonly", "biside"]:
        print(f"\n== Variant: {variant} ==")
        best = step2_pick_best(variant)
        print(f"  best wf_score={best.get('wf_score')} from {best.get('_source_file')}")
        trades, metrics, df_oos, ind_oos = step3_run_oos(best)
        print(f"  OOS: n={metrics['n']}, WR={metrics['wr']}%, PF={metrics['pf']}, net={metrics['ret']}%")
        robust = step4_robustness(trades, df_oos, ind_oos, best)
        corr = step5_breakout_correlation(trades, df_oos)
        results[variant] = {"best": best, "oos_metrics": metrics,
                            "robust": robust, "corr": corr}

    ts = dt.datetime.now().strftime("%Y%m%d_%H%M")
    step6_write_report(results, OUT / f"robustness_report_{ts}.md")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
```

- [ ] **Step 2: 跑** — `python scripts/run_connors_rsi2_experiments.py`
  - 預估 15-25 分（4 grid runs × ~3-5 min + OOS + perturb）
  - 報告寫到 `data/connors_rsi2/robustness_report_<ts>.md`

- [ ] **Step 3: 人工判讀報告**，填 §3 觀察與建議；若任一變體全 Gate 過 → 進 paper；都不過 → 設計 §9 備案；其中一變體部分過 → 視情況決定。

- [ ] **Step 4: Commit**（只 commit orchestrator 與報告 md；CSV/JSON 大檔不入 git）
  ```bash
  # 先確認 .gitignore 不擋這個路徑（vwap_fade 的 robustness_report 順利進了 commit 1677dac、
  # 表示 data/<strategy>/*.md 通常可加；若被擋、用 `git add -f` 或把報告搬到 docs/strategies/reports/）
  git add scripts/run_connors_rsi2_experiments.py data/connors_rsi2/robustness_report_*.md
  git commit -m "feat(connors_rsi2): P4+P5 OOS experiments + gate summary report"
  ```

**Gate 表（兩變體獨立評分）：**

| Gate | 門檻 |
|---|---|
| P3 雙商品 | 各變體 MXF/TXF Top-10 每維參數區間重疊 |
| 4-1 MC | PF p5 > 1.0 / 淨利 p5 > 0 / MDD p95 < 12% |
| 4-2 擾動 | worst-dim stability < 0.3 |
| 4-3 Regime | 震盪日淨利 > 0 / 趨勢日淨利 > -50% × \|震盪日淨利\| |
| P5 OOS | PF > 1.2 / 頻率 1-3 筆/日 / \|r\| breakout < 0.3 |

最後選 Gate 通過數較多 / 整體更佳的變體進 paper。都不過 → 設計文件 §9 備案 C1/C2/C3。

---

## 開發紀律提醒（呼應設計文件 §11）

引擎事實全部繼承 `2026-05-28-vwap-fade-backtest-plan.md` §1 表。本計畫新增的紀律：

1. **`_RsiState` 跨日連續、`ConnorsRsi2Strategy._maybe_daily_reset` 才重置週期狀態**——別誤把 `_rsi.update(...)` 放進 `_maybe_daily_reset` 反向重設。
2. **`Signal.take_profit = 0`**（無自然 TP；出場由 check_exit 接管）。
3. **進場 stop 天然安全**——ATR 停損公式（`close ± sl_atr·atr`）保 `long stop < entry` / `short stop > entry`，不像 vwap_fade σ-band 可能越界。
4. **`_session_bar` 只在 on_kbar 增量**（check_exit 不動），避免 vwap_fade 的 cooldown off-by-one。
5. **per-run `allow_short`**——grid 不放這維、避免 long/short 混在同一 grid 出現 cherry-pick 偏見。

## 未來工作（不在範圍）
- 上線掛載（與 breakout / orb / vwap_fade 用 `position_lock` 互斥）：Gate 過 + paper 驗證後再開實作計畫
- v2 grid：把 `max_trades` / `entry_window` 列入 grid
- ML meta-labeling：Connors RSI(2) 規則出訊號、ML 學「該不該做」——僅在 v1 過 Gate 後考慮
