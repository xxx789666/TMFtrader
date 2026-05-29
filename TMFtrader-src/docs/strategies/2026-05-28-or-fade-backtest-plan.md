# OR Fade 回測 + 優化 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 OR Fade 從設計變成「可回測、過 Stage 7 穩健性 Gate、可上 paper」的實作。雙變體並排測試（long-only / long+short）。最大化重用前兩支策略已建的基礎設施。

**Architecture:** 新增 `strategy/or_fade.py`（`_OrSession` helper + `OrFadeStrategy(BaseStrategy)`），用既有 `FastBacktestEngine` 跑回測；grid 抄 `scripts/optimize_connors_rsi2.py`（dict-based + `--allow-short` CLI）；穩健性三件套**直接 import** `scripts/robustness_vwap_fade.py` 既有函式；OOS orchestrator 抄 `scripts/run_connors_rsi2_experiments.py`。不動 core/、不動既有策略檔。

**Tech Stack:** Python 3.12、pandas/numpy、pyarrow、現有 `core/gpu_indicators`、`ProcessPoolExecutor` + `shared_memory`、pytest。

**背景設計文件：** `TMFtrader-src/docs/strategies/2026-05-28-or-fade-design.md`
**引擎事實參考：** `TMFtrader-src/docs/strategies/2026-05-28-connors-rsi2-backtest-plan.md` 的「已驗證的引擎事實」與「重大差異 vs vwap_fade」整套**繼承**、不重列。

---

## 與 connors_rsi2 plan 的重大差異（實作前必讀）

| # | 差異 | 對任務的影響 |
|---|---|---|
| 1 | **OR session state 替代 RSI state** | `_OrSession`（收 N 根後鎖、之後不變）替代 `_RsiState`（連續 Wilder）。**換日 reset 所有 OR 狀態**（與 _RsiState 跨日連續相反） |
| 2 | **wait_bars=1 pending state** | 策略多維護 `_pending_long` / `_pending_short`（觸邊那根的 low/high）。**只活 1 根**，下一根評估後必清空；換日也清空；長短可並存互不影響 |
| 3 | **check_exit 比前任更簡單** | OR-mid 固定不動 → check_exit 純讀、**不 update** 任何 state；force_close 觸發時**不設 cooldown**（當日已結束、無意義） |
| 4 | **grid 更大** | 324 combos per 變體（vs connors_rsi2 long-only 108）。grid：`or_bars × vol_ratio_max × wait_bars × sl_atr × max_bars × cooldown` = 3·3·2·3·3·2 |
| 5 | **entry_window 動態 start** | `_ew_start = 08:45 + or_bars × 5min`；在 `__init__` 算。對 grid 結果有顯著影響（or_bars=12 樣本數明顯少） |
| 6 | **進場用 snapshot.volume_ratio** | 已預算欄位 `core/gpu_indicators.py:691`，不自算 vol/vol_ma20 |
| 7 | **ATR stop 可能 > OR-mid** | v1 不 clamp、接受。grid 自然會避開此區域 |

---

## 可直接重用的設施（不重造）

| 資源 | 路徑 | 用法 |
|---|---|---|
| 資料 | `data/vwap_fade/*.parquet` | 同前 |
| 動態稅 | `core/position.py:240-244` | 同前 |
| grid 並行骨架 | `scripts/optimize_connors_rsi2.py` | 抄、改 PARAM_GRID + 策略 import + 輸出路徑 |
| 單 backtest helper | `_run_one(params, df, split_idx, allow_short)` | 模式相同（OR fade 版自己寫一個） |
| 績效指標 | `from scripts.optimize_strategy import _calc_metrics` | 不重寫 |
| 三件套 | `from scripts.robustness_vwap_fade import monte_carlo, stability, split_by_regime` | 直接 import |
| OOS orchestrator 結構 | `scripts/run_connors_rsi2_experiments.py` | 抄、改策略 import + 輸出路徑（**含本地 perturb 實作** — 因 `perturb_and_run` 寫死 vwap_fade） |
| 成本 test | `tests/test_connors_rsi2.py::test_cost_accounting_*` | 內容直接複製到 `tests/test_or_fade.py` |

---

## 檔案結構

| 檔案 | 職責 | 動作 |
|---|---|---|
| `strategy/or_fade.py` | `_OrSession` + `OrFadeStrategy`（on_kbar + check_exit） | Create |
| `tests/test_or_fade.py` | 策略單元測試 | Create |
| `scripts/optimize_or_fade.py` | 粗篩 grid（抄 optimize_connors_rsi2.py） | Create |
| `scripts/run_or_fade_experiments.py` | OOS 實驗 + Gate 匯總 orchestrator | Create |
| `docs/strategies/2026-05-28-or-fade-backtest-plan.md` | 本計畫 | （已存在） |

零改動：core/、既有 strategy/*.py、既有 backtest/*、`scripts/robustness_vwap_fade.py`、`scripts/optimize_connors_rsi2.py`、`scripts/optimize_strategy.py`、`data/vwap_fade/*`。

---

## Task 1（P0）：成本會計驗證（動態稅）

**Files:** Test `tests/test_or_fade.py`（新檔）

內容與 `tests/test_connors_rsi2.py` 的 P0 兩個 test 相同——驗證引擎成本模型而非策略；分檔存放避免跨檔 import test helpers。

- [ ] **Step 1: 寫測試**（內容與 connors_rsi2 P0 相同）

```python
"""
OR Fade 策略測試
P0: 成本會計驗證（動態稅模型，commit a95ae44 起）
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import math
from core.position import PositionManager, Side
from core.instrument_config import INSTRUMENT_SPECS


def test_cost_accounting_dynamic_tax():
    """同 connors_rsi2 P0：@22000 → commission=46, net_pnl=54"""
    spec = INSTRUMENT_SPECS["TMF"]
    pm = PositionManager(instruments=["TMF"], configs={"TMF": spec}, initial_balance=200_000)
    pm.open_position("TMF", Side.LONG, price=22000.0, quantity=1,
                     stop_loss=21950, take_profit=22050, timestamp=None)
    trade = pm.close_position("TMF", 22010.0, "test", None)
    assert trade.pnl == 100.0
    assert trade.commission == 46.0
    assert trade.net_pnl == 54.0


def test_cost_accounting_high_price_tax_scales():
    """@45000 → tax=9-10/邊；驗證動態縮放"""
    spec = INSTRUMENT_SPECS["TMF"]
    pm = PositionManager(instruments=["TMF"], configs={"TMF": spec}, initial_balance=200_000)
    pm.open_position("TMF", Side.LONG, price=45000.0, quantity=1,
                     stop_loss=44950, take_profit=45050, timestamp=None)
    trade = pm.close_position("TMF", 45010.0, "test", None)
    expected_entry_tax = math.ceil(45000 * 10 * 0.00002)
    expected_exit_tax  = math.ceil(45010 * 10 * 0.00002)
    expected_comm = 36 + (expected_entry_tax + expected_exit_tax)
    assert trade.commission == float(expected_comm)
```

- [ ] **Step 2: Run** `python -m pytest tests/test_or_fade.py -v` → 2 PASS。**FAIL** → 成本模型差異，stop 並 report（不要 mutate assertion）。

- [ ] **Step 3: Commit**
  ```bash
  git add tests/test_or_fade.py
  git commit -m "test(or_fade): P0 verify dynamic tax in net_pnl"
  ```

**Gate P0：** 2 PASS 才往下。

---

## Task 2（P2a）：`_OrSession` helper（純邏輯 TDD）

**Files:** Create `strategy/or_fade.py`（this task: ONLY `_OrSession` + imports）；append to `tests/test_or_fade.py`.

- [ ] **Step 1: 寫失敗測試**

```python
def test_or_session_not_locked_initially():
    from strategy.or_fade import _OrSession
    s = _OrSession(or_bars=3)
    assert s.locked is False


def test_or_session_locks_after_n_bars():
    from strategy.or_fade import _OrSession
    from datetime import datetime
    s = _OrSession(or_bars=3)
    s.update(datetime(2024,1,2,9,0),  high=10, low=8)
    assert s.locked is False
    s.update(datetime(2024,1,2,9,5),  high=11, low=7)
    assert s.locked is False
    s.update(datetime(2024,1,2,9,10), high=9,  low=6)
    assert s.locked is True
    assert s.or_high == 11.0  # max(10, 11, 9)
    assert s.or_low  == 6.0   # min(8, 7, 6)
    assert s.or_mid  == 8.5   # (11+6)/2


def test_or_session_locked_ignores_further_updates():
    """OR 鎖住後新 bar 不應改 high/low/mid"""
    from strategy.or_fade import _OrSession
    from datetime import datetime
    s = _OrSession(or_bars=2)
    s.update(datetime(2024,1,2,9,0), high=10, low=8)
    s.update(datetime(2024,1,2,9,5), high=12, low=7)
    assert s.locked
    locked_high, locked_low, locked_mid = s.or_high, s.or_low, s.or_mid
    # 進一步 update 不應改變
    s.update(datetime(2024,1,2,9,10), high=99, low=1)
    assert s.or_high == locked_high
    assert s.or_low  == locked_low
    assert s.or_mid  == locked_mid


def test_or_session_resets_across_days():
    """換日 → OR 狀態歸零、locked=False、重新收集"""
    from strategy.or_fade import _OrSession
    from datetime import datetime
    s = _OrSession(or_bars=2)
    s.update(datetime(2024,1,2,9,0), high=10, low=8)
    s.update(datetime(2024,1,2,9,5), high=12, low=7)
    assert s.locked
    # Day 2 第一根
    s.update(datetime(2024,1,3,9,0), high=20, low=18)
    assert s.locked is False           # reset
    import math
    assert math.isnan(s.or_high)       # 鎖前回 nan
    # 第二根（or_bars=2 → 此根鎖）
    s.update(datetime(2024,1,3,9,5), high=22, low=17)
    assert s.locked
    assert s.or_high == 22.0
    assert s.or_low  == 17.0


def test_or_session_invalid_or_bars():
    from strategy.or_fade import _OrSession
    import pytest
    with pytest.raises(ValueError):
        _OrSession(or_bars=0)
```

- [ ] **Step 2: Run** `python -m pytest tests/test_or_fade.py -k or_session -v`，Expected: FAIL（module not found）

- [ ] **Step 3: 實作** — 建 `strategy/or_fade.py`：

```python
"""
OR Fade 策略：開盤後區間 fade（vwap_fade 設計 §9 備案 C）。
  - _OrSession        : 開盤 N 根定區間、鎖後不變、跨日 reset
  - OrFadeStrategy    : 觸邊+量縮 fade 回 OR-mid（Task P2b/P2c，待後續任務新增）
"""

from datetime import datetime, time
from typing import Optional
import math

# Task P2b/P2c 進出場邏輯所需 import（待後續任務取消註解）
# from strategy.base import BaseStrategy, Signal, SignalDirection
# from core.market_data import KBar, MarketSnapshot
# from core.position import Position, Side


class _OrSession:
    """每日 OR 區間 state：收前 or_bars 根高低、鎖後不變、跨日重置。

    Attributes
    ----------
    locked  : OR 是否已鎖（已收滿 or_bars 根）
    or_high : 鎖後的 OR 上緣（鎖前回 nan）
    or_low  : 鎖後的 OR 下緣
    or_mid  : (or_high + or_low) / 2
    """

    def __init__(self, or_bars: int):
        if or_bars < 1:
            raise ValueError(f"or_bars must be >= 1, got {or_bars}")
        self.or_bars = or_bars
        self._date = None
        self._high = float("-inf")
        self._low  = float("inf")
        self._bar_count = 0
        self._locked = False

    def _maybe_reset(self, dt: datetime) -> None:
        d = dt.date()
        if d != self._date:
            self._date = d
            self._high = float("-inf")
            self._low  = float("inf")
            self._bar_count = 0
            self._locked = False

    def update(self, dt: datetime, high: float, low: float) -> None:
        """收一根 K；鎖後成 no-op。每次呼叫先做換日 reset 偵測。"""
        self._maybe_reset(dt)
        if self._locked:
            return
        if high > self._high: self._high = high
        if low  < self._low:  self._low  = low
        self._bar_count += 1
        if self._bar_count >= self.or_bars:
            self._locked = True

    @property
    def locked(self) -> bool:
        return self._locked

    @property
    def or_high(self) -> float:
        return self._high if self._locked else float("nan")

    @property
    def or_low(self) -> float:
        return self._low if self._locked else float("nan")

    @property
    def or_mid(self) -> float:
        if not self._locked:
            return float("nan")
        return (self._high + self._low) / 2.0


# ── OrFadeStrategy 由 Task P2b/P2c 新增於此 ───────────────────────────────────
```

- [ ] **Step 4: Run** `python -m pytest tests/test_or_fade.py -k or_session -v` → 5 PASS。 Full: `python -m pytest tests/test_or_fade.py -v` → 7 PASS（2 P0 + 5 OR session）。

- [ ] **Step 5: Commit**
  ```bash
  git add strategy/or_fade.py tests/test_or_fade.py
  git commit -m "feat(or_fade): P2a _OrSession state helper"
  ```

---

## Task 3（P2b）：`OrFadeStrategy.__init__` + `on_kbar`（含 pending state）

**Files:** Modify `strategy/or_fade.py`；append to `tests/test_or_fade.py`.

關鍵：wait_bars=1 的 pending state 是最易錯的點。**TDD 必須覆蓋：confirm 成功進場、confirm 失敗丟棄、換日清空**。

- [ ] **Step 1: 寫失敗測試** — 加 helper + 6 個進場 test

```python
# ── Shared helpers (also used by Tasks 4, 5) ────────────────────────────────
def _make_snap(price, adx, atr, ts, vol_ratio=1.0, volume=100):
    """注意：volume_ratio 是 OR fade 主要過濾條件，預設 1.0 = 中性"""
    from core.market_data import MarketSnapshot
    s = MarketSnapshot()
    s.price = price; s.adx = adx; s.atr = atr; s.timestamp = ts
    s.volume = volume; s.volume_ratio = vol_ratio
    return s


def _kbar(ts, o=None, h=None, l=None, c=None, vol=100):
    """O/H/L/C 可指定（OR fade 對 high/low 敏感）；預設用 c 等於 o，h=c+1,l=c-1"""
    from core.market_data import KBar
    if c is None: c = 100.0
    if o is None: o = c
    if h is None: h = c + 1
    if l is None: l = c - 1
    return KBar(ts, o, h, l, c, vol)


# ── Task 3 entry tests ──────────────────────────────────────────────────────
def test_entry_blocked_until_or_locked():
    """OR 尚未鎖 → 即使觸邊+量縮也不進場"""
    from strategy.or_fade import OrFadeStrategy
    from datetime import datetime
    strat = OrFadeStrategy(or_bars=3, vol_ratio_max=0.9, wait_bars=0, allow_short=True)
    # 只 update 2 根（不到 or_bars=3）
    for i in range(2):
        ts = datetime(2024, 1, 2, 8, 45 + i*5)
        strat.on_kbar(_kbar(ts, c=100, h=105, l=95), _make_snap(100, 20, 2, ts, vol_ratio=0.5))
    assert strat._trades_today == 0


def test_entry_long_on_touch_low_with_vol_drop():
    """OR 鎖後觸下緣 + 量縮 + wait=0 → BUY"""
    from strategy.or_fade import OrFadeStrategy
    from strategy.base import SignalDirection
    from datetime import datetime
    strat = OrFadeStrategy(or_bars=3, vol_ratio_max=0.7, wait_bars=0,
                           sl_atr=2.0, allow_short=False)
    # OR 收集：3 根，high/low = 105/95
    for i in range(3):
        ts = datetime(2024, 1, 2, 8, 45 + i*5)
        strat.on_kbar(_kbar(ts, c=100, h=105, l=95), _make_snap(100, 20, 2, ts, vol_ratio=1.0))
    # OR 鎖後一根：低點觸 OR_low=95、量縮
    ts = datetime(2024, 1, 2, 9, 0)   # _ew_start = 08:45 + 3*5 = 09:00
    sig = strat.on_kbar(_kbar(ts, c=96, h=98, l=95), _make_snap(96, 20, 2, ts, vol_ratio=0.5))
    assert sig is not None and sig.direction == SignalDirection.BUY
    assert sig.stop_loss < 96   # ATR stop 在進場價下方


def test_entry_blocked_when_volume_too_high():
    """觸下緣但 vol_ratio > vol_ratio_max → 不進場"""
    from strategy.or_fade import OrFadeStrategy
    from datetime import datetime
    strat = OrFadeStrategy(or_bars=3, vol_ratio_max=0.7, wait_bars=0)
    for i in range(3):
        ts = datetime(2024, 1, 2, 8, 45 + i*5)
        strat.on_kbar(_kbar(ts, c=100, h=105, l=95), _make_snap(100, 20, 2, ts, vol_ratio=1.0))
    ts = datetime(2024, 1, 2, 9, 0)
    # vol_ratio=1.5 > vol_ratio_max=0.7
    sig = strat.on_kbar(_kbar(ts, c=96, h=98, l=95), _make_snap(96, 20, 2, ts, vol_ratio=1.5))
    assert sig is None
    assert strat._trades_today == 0


def test_short_blocked_when_allow_short_false():
    from strategy.or_fade import OrFadeStrategy
    from datetime import datetime
    strat = OrFadeStrategy(or_bars=3, vol_ratio_max=0.9, allow_short=False)
    for i in range(3):
        ts = datetime(2024, 1, 2, 8, 45 + i*5)
        strat.on_kbar(_kbar(ts, c=100, h=105, l=95), _make_snap(100, 20, 2, ts, vol_ratio=1.0))
    ts = datetime(2024, 1, 2, 9, 0)
    # high 觸上緣 + 量縮，但 allow_short=False → 不該 SELL
    sig = strat.on_kbar(_kbar(ts, c=104, h=105, l=103), _make_snap(104, 20, 2, ts, vol_ratio=0.5))
    assert strat._trades_today == 0


def test_wait_bars_1_confirm_success_enters_next_bar():
    """wait=1：觸下緣那根記 pending；下根 low > 觸邊 low → 進場"""
    from strategy.or_fade import OrFadeStrategy
    from strategy.base import SignalDirection
    from datetime import datetime
    strat = OrFadeStrategy(or_bars=3, vol_ratio_max=0.7, wait_bars=1, allow_short=False)
    for i in range(3):
        ts = datetime(2024, 1, 2, 8, 45 + i*5)
        strat.on_kbar(_kbar(ts, c=100, h=105, l=95), _make_snap(100, 20, 2, ts, vol_ratio=1.0))
    # 觸邊那根：low=95（= OR_low）、量縮、wait=1 → 記 pending、不進場
    ts1 = datetime(2024, 1, 2, 9, 0)
    sig = strat.on_kbar(_kbar(ts1, c=96, h=98, l=95), _make_snap(96, 20, 2, ts1, vol_ratio=0.5))
    assert sig is None
    assert strat._pending_long is not None
    # 下一根：low=97 > pending touch_low=95 → confirm 成功、進場
    ts2 = datetime(2024, 1, 2, 9, 5)
    sig = strat.on_kbar(_kbar(ts2, c=98, h=99, l=97), _make_snap(98, 20, 2, ts2, vol_ratio=0.8))
    assert sig is not None and sig.direction == SignalDirection.BUY
    assert strat._pending_long is None   # 進場後清空


def test_wait_bars_1_confirm_fail_clears_old_pending():
    """wait=1：下根若再創新低 → 不進場 + 舊 pending 被清。
    注意：若該根本身也觸下緣 + 量縮，會設一個**新** pending（touch_low=新低），
    這是預期行為（新 bar 觸發新 pending，與「舊 pending 被清」是兩件事）。"""
    from strategy.or_fade import OrFadeStrategy
    from datetime import datetime
    strat = OrFadeStrategy(or_bars=3, vol_ratio_max=0.7, wait_bars=1)
    for i in range(3):
        ts = datetime(2024, 1, 2, 8, 45 + i*5)
        strat.on_kbar(_kbar(ts, c=100, h=105, l=95), _make_snap(100, 20, 2, ts, vol_ratio=1.0))
    ts1 = datetime(2024, 1, 2, 9, 0)
    strat.on_kbar(_kbar(ts1, c=96, h=98, l=95), _make_snap(96, 20, 2, ts1, vol_ratio=0.5))
    assert strat._pending_long == {"touch_low": 95}
    # 下根 low=93 ≤ 95 → 確認失敗 + 再次觸下緣 + 量縮 → 設新 pending
    ts2 = datetime(2024, 1, 2, 9, 5)
    sig = strat.on_kbar(_kbar(ts2, c=93, h=95, l=93), _make_snap(93, 20, 2, ts2, vol_ratio=0.5))
    assert sig is None
    assert strat._pending_long == {"touch_low": 93}   # 舊清+新設（touch_low 變了即證明清了）
    assert strat._trades_today == 0


def test_wait_bars_1_confirm_fail_no_new_touch_clears_pending():
    """wait=1 確認失敗、且該根不再觸下緣 → pending 完全清空（None）。
    需用「OR 期內就深探」造出 pending.touch_low < or_low 的情境，才能讓 fail-bar 不再觸下緣。"""
    from strategy.or_fade import OrFadeStrategy
    from datetime import datetime
    strat = OrFadeStrategy(or_bars=3, vol_ratio_max=0.9, wait_bars=1)
    # OR 期內讓 low 探到 90（or_low=90）
    for i, l in enumerate([95, 92, 90]):
        ts = datetime(2024, 1, 2, 8, 45 + i*5)
        strat.on_kbar(_kbar(ts, c=100, h=105, l=l), _make_snap(100, 20, 2, ts, vol_ratio=1.0))
    assert strat._or.or_low == 90
    # 觸邊那根：low=90（= or_low），pending.touch_low = 90
    ts1 = datetime(2024, 1, 2, 9, 0)
    strat.on_kbar(_kbar(ts1, c=91, h=92, l=90), _make_snap(91, 20, 2, ts1, vol_ratio=0.5))
    assert strat._pending_long == {"touch_low": 90}
    # 下根：low=91 ≤ 90? 否（91 > 90 → confirm 成功！）
    # 改為造 fail：low=90（= pending.touch_low，confirm 失敗），但又觸 or_low → 仍設新 pending
    # 真正的「fail + 不重設」geometry：低於 pending 但這根 high < or_low 是不可能的
    # 結論：只要該根觸下緣（low ≤ or_low），無論 confirm 成敗都會有 pending
    # 真正能達成「pending 完全清為 None」的唯一路徑 = vol_ratio 過高 → return None 前 pending 沒被重設
    ts2 = datetime(2024, 1, 2, 9, 5)
    sig = strat.on_kbar(_kbar(ts2, c=89, h=90, l=89), _make_snap(89, 20, 2, ts2, vol_ratio=1.5))
    # vol_ratio=1.5 > 0.9 → 新觸發 path 不執行 → pending 維持 None（舊的已在 wait 段清）
    assert sig is None
    assert strat._pending_long is None
    assert strat._trades_today == 0


def test_daily_reset_clears_or_and_pending():
    """換日：OR 狀態 + pending state + 計數全清"""
    from strategy.or_fade import OrFadeStrategy
    from datetime import datetime
    strat = OrFadeStrategy(or_bars=3, vol_ratio_max=0.7, wait_bars=1)
    for i in range(3):
        ts = datetime(2024, 1, 2, 8, 45 + i*5)
        strat.on_kbar(_kbar(ts, c=100, h=105, l=95), _make_snap(100, 20, 2, ts, vol_ratio=1.0))
    # 製造 pending
    ts1 = datetime(2024, 1, 2, 9, 0)
    strat.on_kbar(_kbar(ts1, c=96, h=98, l=95), _make_snap(96, 20, 2, ts1, vol_ratio=0.5))
    assert strat._pending_long is not None
    # 換日
    ts2 = datetime(2024, 1, 3, 8, 45)
    strat.on_kbar(_kbar(ts2, c=200, h=205, l=195), _make_snap(200, 20, 2, ts2, vol_ratio=1.0))
    assert strat._pending_long is None
    assert strat._or.locked is False   # OR 也 reset
    assert strat._trades_today == 0
    assert strat._session_bar == 1


def test_entry_window_dynamic_start():
    """or_bars=6 → _ew_start = 08:45 + 6*5min = 09:15；09:10 不該進場"""
    from strategy.or_fade import OrFadeStrategy
    from datetime import time
    strat = OrFadeStrategy(or_bars=6)
    assert strat._ew_start == time(9, 15)
    strat2 = OrFadeStrategy(or_bars=3)
    assert strat2._ew_start == time(9, 0)
    strat3 = OrFadeStrategy(or_bars=12)
    assert strat3._ew_start == time(9, 45)
```

- [ ] **Step 2: Run，confirm FAIL**

- [ ] **Step 3: 實作** — 取消 `strategy/or_fade.py` 開頭三個 import 註解（`strategy.base`, `core.market_data`, `core.position`）。在 `_OrSession` 之後加 `OrFadeStrategy`：

```python
class OrFadeStrategy(BaseStrategy):
    def __init__(
        self,
        or_bars: int = 6,
        vol_ratio_max: float = 0.7,
        wait_bars: int = 0,
        sl_atr: float = 2.0,
        max_bars: int = 18,
        cooldown: int = 3,
        max_trades: int = 6,
        entry_window_end: str = "12:00",
        force_close: str = "13:25",
        allow_short: bool = False,
        point_value: float = 10.0,
    ):
        if wait_bars not in (0, 1):
            raise ValueError(f"wait_bars must be 0 or 1, got {wait_bars}")
        self.or_bars = or_bars
        self.vol_ratio_max = vol_ratio_max
        self.wait_bars = wait_bars
        self.sl_atr = sl_atr
        self.max_bars = max_bars
        self.cooldown = cooldown
        self.max_trades = max_trades
        self.allow_short = allow_short
        self.point_value = point_value

        # _ew_start 動態 = 08:45 + or_bars*5min
        ew_start_min = 8*60 + 45 + or_bars * 5
        self._ew_start = time(ew_start_min // 60, ew_start_min % 60)
        self._ew_end = time.fromisoformat(entry_window_end)
        self._force_close = time.fromisoformat(force_close)

        self._or = _OrSession(or_bars=or_bars)
        self._trades_today = 0
        self._cooldown_until_bar = -1
        self._session_bar = 0
        self._day = None
        # pending state for wait_bars=1（只活 1 根）
        self._pending_long: Optional[dict] = None    # {"touch_low": float}
        self._pending_short: Optional[dict] = None   # {"touch_high": float}

    @property
    def name(self) -> str:
        return "or_fade"

    def _maybe_daily_reset(self, dt: datetime) -> None:
        d = dt.date()
        if d != self._day:
            self._day = d
            self._trades_today = 0
            self._cooldown_until_bar = -1
            self._session_bar = 0
            self._pending_long = None
            self._pending_short = None
            # 注意：_or 有自己的 _maybe_reset（在 update 時觸發），無需手動 call

    def on_kbar(self, kbar, snapshot):
        ts = kbar.datetime
        self._maybe_daily_reset(ts)
        self._or.update(ts, kbar.high, kbar.low)
        self._session_bar += 1

        # ── 守衛 ──────────────────────────────────────────────────────────
        if not self._or.locked:
            return None
        if not (self._ew_start <= ts.time() < self._ew_end):
            return None
        if self._trades_today >= self.max_trades:
            # 即使 max_trades 滿，pending 仍應清空（只活 1 根）
            if self.wait_bars == 1:
                self._pending_long = None
                self._pending_short = None
            return None
        if self._session_bar <= self._cooldown_until_bar:
            if self.wait_bars == 1:
                self._pending_long = None
                self._pending_short = None
            return None

        atr = max(snapshot.atr, 1.0)
        close = kbar.close

        # ── wait_bars=1：先處理 pending（前一根記下的） ───────────────────
        if self.wait_bars == 1:
            # 必清空：先暫存再清，最後決定是否回 Signal
            pending_long = self._pending_long
            pending_short = self._pending_short
            self._pending_long = None
            self._pending_short = None

            if pending_long is not None:
                if kbar.low > pending_long["touch_low"]:
                    # confirm 成功 → BUY
                    stop = close - self.sl_atr * atr
                    return self._signal(SignalDirection.BUY, close, stop)
                # 失敗：pending 已清、繼續向下評估新觸發
            if pending_short is not None:
                if kbar.high < pending_short["touch_high"]:
                    stop = close + self.sl_atr * atr
                    return self._signal(SignalDirection.SELL, close, stop)
                # 失敗：pending 已清

        # ── 新觸邊偵測（vol_ratio 過濾） ──────────────────────────────────
        vol_ratio = snapshot.volume_ratio
        if vol_ratio > self.vol_ratio_max:
            return None

        if kbar.low <= self._or.or_low:
            # 觸下緣
            if self.wait_bars == 0:
                stop = close - self.sl_atr * atr
                return self._signal(SignalDirection.BUY, close, stop)
            else:
                self._pending_long = {"touch_low": kbar.low}
                return None

        if self.allow_short and kbar.high >= self._or.or_high:
            # 觸上緣
            if self.wait_bars == 0:
                stop = close + self.sl_atr * atr
                return self._signal(SignalDirection.SELL, close, stop)
            else:
                self._pending_short = {"touch_high": kbar.high}
                return None

        return None

    def _signal(self, direction, price, stop):
        self._trades_today += 1
        tp = round(self._or.or_mid, 1) if self._or.locked else 0
        return Signal(
            direction=direction, strength=1.0,
            stop_loss=round(stop, 1), take_profit=tp,
            reason=f"or_fade {direction.value} or_mid={tp}",
            source=self.name,
        )
```

- [ ] **Step 4: Run** `python -m pytest tests/test_or_fade.py -v` → 15 PASS（7 prior + 8 new entry tests）

- [ ] **Step 5: Commit**
  ```bash
  git add strategy/or_fade.py tests/test_or_fade.py
  git commit -m "feat(or_fade): P2b entry logic on_kbar + wait_bars pending state"
  ```

---

## Task 4（P2c）：`check_exit` + cooldown 接線

**Files:** Modify `strategy/or_fade.py`；append to `tests/test_or_fade.py`.

關鍵：
- 用 `snapshot.timestamp`（不用 `self._current_bar_time`）
- **不 update** OR state（OR-mid 固定）
- **不增量 `_session_bar`**（only on_kbar）
- ATR stop 觸發 → 設 cooldown；force_close 觸發 → **不設** cooldown

- [ ] **Step 1: 寫失敗測試**

```python
def _pos(side, entry, stop, bars):
    from core.position import PositionManager
    from core.instrument_config import INSTRUMENT_SPECS
    pm = PositionManager(instruments=["TMF"], configs={"TMF": INSTRUMENT_SPECS["TMF"]},
                         initial_balance=200_000)
    pm.open_position("TMF", side, price=entry, quantity=1,
                     stop_loss=stop, take_profit=0, timestamp=None)
    p = pm.positions["TMF"]
    p.bars_since_entry = bars
    return p


def _strat_with_locked_or(or_high=105, or_low=95, allow_short=False, cooldown=3, max_bars=18):
    """Helper：建一個 OR 已鎖（mid = (105+95)/2 = 100）的 strat"""
    from strategy.or_fade import OrFadeStrategy
    from datetime import datetime
    strat = OrFadeStrategy(or_bars=2, vol_ratio_max=0.9, allow_short=allow_short,
                           cooldown=cooldown, max_bars=max_bars)
    # 灌 2 根鎖 OR
    strat.on_kbar(_kbar(datetime(2024,1,2,8,45), c=100, h=or_high, l=or_low),
                  _make_snap(100, 20, 2, datetime(2024,1,2,8,45), vol_ratio=1.0))
    strat.on_kbar(_kbar(datetime(2024,1,2,8,50), c=100, h=or_high, l=or_low),
                  _make_snap(100, 20, 2, datetime(2024,1,2,8,50), vol_ratio=1.0))
    assert strat._or.locked and strat._or.or_mid == (or_high + or_low) / 2
    return strat


def test_exit_force_close_no_cooldown():
    """13:25 強平 → CLOSE，且 cooldown_until_bar 不變"""
    from strategy.base import SignalDirection
    from core.position import Side
    from datetime import datetime
    strat = _strat_with_locked_or()
    cd_before = strat._cooldown_until_bar
    snap = _make_snap(100, 20, 2, datetime(2024,1,2,13,25), vol_ratio=1.0)
    sig = strat.check_exit(_pos(Side.LONG, 96, 92, 5), snap)
    assert sig is not None and sig.direction == SignalDirection.CLOSE and "盤末" in sig.reason
    assert strat._cooldown_until_bar == cd_before   # NOT set on force_close


def test_exit_atr_stop_long_sets_cooldown():
    """多單 price 跌破 stop → CLOSE + cooldown_until_bar = _session_bar + cooldown"""
    from core.position import Side
    from datetime import datetime
    strat = _strat_with_locked_or(cooldown=3)
    bar_at_stop = strat._session_bar
    snap = _make_snap(89, 20, 2, datetime(2024,1,2,10,0), vol_ratio=1.0)
    sig = strat.check_exit(_pos(Side.LONG, 95, 90, 3), snap)
    assert sig is not None and "停損" in sig.reason
    assert strat._cooldown_until_bar == bar_at_stop + 3


def test_exit_or_mid_profit_target_long():
    """多單 price ≥ or_mid → 停利"""
    from core.position import Side
    from datetime import datetime
    strat = _strat_with_locked_or(or_high=105, or_low=95)   # mid=100
    # 多單 entry 96、價=100 ≥ or_mid → 觸發停利
    snap = _make_snap(100, 20, 2, datetime(2024,1,2,10,0), vol_ratio=1.0)
    sig = strat.check_exit(_pos(Side.LONG, 96, 90, 3), snap)
    assert sig is not None and "OR-mid" in sig.reason


def test_exit_or_mid_profit_target_short():
    from core.position import Side
    from datetime import datetime
    strat = _strat_with_locked_or(or_high=105, or_low=95, allow_short=True)
    # 空單 entry 104、價=100 ≤ or_mid → 觸發停利
    snap = _make_snap(100, 20, 2, datetime(2024,1,2,10,0), vol_ratio=1.0)
    sig = strat.check_exit(_pos(Side.SHORT, 104, 110, 3), snap)
    assert sig is not None and "OR-mid" in sig.reason


def test_exit_time_stop():
    """持倉超 max_bars 且未觸停損/停利 → 時間停損"""
    from core.position import Side
    from datetime import datetime
    strat = _strat_with_locked_or(or_high=105, or_low=95, max_bars=18)
    # 多單 entry 96、價=97（未到 mid=100 也未到 stop=90）、bars=19
    snap = _make_snap(97, 20, 2, datetime(2024,1,2,11,0), vol_ratio=1.0)
    sig = strat.check_exit(_pos(Side.LONG, 96, 90, 19), snap)
    assert sig is not None and "時間停損" in sig.reason


def test_check_exit_does_not_update_or_state_or_session_bar():
    """invariant：check_exit 不寫 _or、不增量 _session_bar"""
    from core.position import Side
    from datetime import datetime
    strat = _strat_with_locked_or()
    or_high_before = strat._or.or_high
    or_low_before  = strat._or.or_low
    session_bar_before = strat._session_bar
    # 跑一次 check_exit（不應觸發任何 close、純讀）
    snap = _make_snap(99, 20, 2, datetime(2024,1,2,10,0), vol_ratio=1.0)
    strat.check_exit(_pos(Side.LONG, 96, 90, 3), snap)
    assert strat._or.or_high == or_high_before
    assert strat._or.or_low  == or_low_before
    assert strat._session_bar == session_bar_before
```

- [ ] **Step 2: Run，confirm FAIL**（check_exit 未實作）

- [ ] **Step 3: 實作** — 在 `OrFadeStrategy` 內加：

```python
    def check_exit(self, position, snapshot):
        if position.is_flat:
            return None
        ts = snapshot.timestamp                # 真實 bar 時間
        price = snapshot.price
        # 注意：check_exit 不 update _or（OR-mid 鎖後固定）、不增量 _session_bar
        is_long = (position.side == Side.LONG)

        def close_sig(reason):
            return Signal(direction=SignalDirection.CLOSE, strength=1.0,
                          stop_loss=0, take_profit=0, reason=reason, source=self.name)

        # 1) 盤末強平（不設 cooldown）
        if ts.time() >= self._force_close:
            return close_sig(f"盤末強平 @ {price:.0f}")
        # 2) ATR 凍結停損 + cooldown
        if position.stop_loss > 0:
            if is_long and price <= position.stop_loss:
                self._cooldown_until_bar = self._session_bar + self.cooldown
                return close_sig(f"停損 @ {price:.0f}")
            if (not is_long) and price >= position.stop_loss:
                self._cooldown_until_bar = self._session_bar + self.cooldown
                return close_sig(f"停損 @ {price:.0f}")
        # 3) OR-mid 停利
        if self._or.locked:
            mid = self._or.or_mid
            if is_long and price >= mid:
                return close_sig(f"回到OR-mid停利 @ {price:.0f}")
            if (not is_long) and price <= mid:
                return close_sig(f"回到OR-mid停利 @ {price:.0f}")
        # 4) 時間停損
        if position.bars_since_entry > self.max_bars:
            return close_sig(f"時間停損 {position.bars_since_entry}根")
        return None

    def get_parameters(self):
        return {
            "or_bars": self.or_bars, "vol_ratio_max": self.vol_ratio_max,
            "wait_bars": self.wait_bars, "sl_atr": self.sl_atr,
            "max_bars": self.max_bars, "cooldown": self.cooldown,
            "max_trades": self.max_trades, "allow_short": self.allow_short,
        }

    def reset(self):
        """引擎不會跨日呼叫；供手動測試/重啟用"""
        self._or = _OrSession(or_bars=self.or_bars)
        self._trades_today = 0
        self._cooldown_until_bar = -1
        self._session_bar = 0
        self._day = None
        self._pending_long = None
        self._pending_short = None
```

- [ ] **Step 4: Run** `python -m pytest tests/test_or_fade.py -v` → 21 PASS（15 prior + 6 new exit tests）

- [ ] **Step 5: Commit**
  ```bash
  git add strategy/or_fade.py tests/test_or_fade.py
  git commit -m "feat(or_fade): P2c exit logic + cooldown wiring (no cooldown on force_close)"
  ```

---

## Task 5（P2d）：引擎整合冒煙（雙變體真實 MXF）

**Files:** Append to `tests/test_or_fade.py`.

注意 `Side.LONG.value = "long"` 小寫——同 connors_rsi2 教訓。

- [ ] **Step 1: 寫測試**

```python
def test_engine_smoke_long_only():
    """P2d: OrFadeStrategy(allow_short=False) 真實 MXF 5m 短段"""
    import pandas as pd
    from pathlib import Path
    p = Path("data/vwap_fade/MXF_day_5m.parquet")
    if not p.exists():
        import pytest; pytest.skip("data 未準備")
    from core.gpu_indicators import precompute_all
    from backtest.fast_engine import FastBacktestEngine
    from strategy.or_fade import OrFadeStrategy
    df = pd.read_parquet(p).head(3000).reset_index(drop=True)
    ind = precompute_all(df, verbose=False)
    res = FastBacktestEngine(initial_balance=200_000, instrument="TMF").run(
        df, ind, OrFadeStrategy(allow_short=False), "balanced")
    # OR fade 的訊號比 RSI(2) 罕見得多；可能 trades=0，這時 skip 而非 fail
    days = df["datetime"].dt.date.nunique()
    if len(res.trades) == 0:
        import pytest; pytest.skip(f"OR fade 在 {days} 天上零訊號（vol_ratio_max=0.7 預設嚴）"
                                   f"——下游 grid 會放寬參數")
    avg = len(res.trades) / max(days, 1)
    assert avg < 10, f"過度交易 avg={avg:.2f}/day"
    sides = {t["side"] for t in res.trades}
    assert sides.issubset({"long"}), f"long-only 不該有 short，但出現 {sides}"


def test_engine_smoke_long_short():
    """allow_short=True 真實資料；至少一邊有單"""
    import pandas as pd
    from pathlib import Path
    p = Path("data/vwap_fade/MXF_day_5m.parquet")
    if not p.exists():
        import pytest; pytest.skip("data 未準備")
    from core.gpu_indicators import precompute_all
    from backtest.fast_engine import FastBacktestEngine
    from strategy.or_fade import OrFadeStrategy
    df = pd.read_parquet(p).head(3000).reset_index(drop=True)
    ind = precompute_all(df, verbose=False)
    res = FastBacktestEngine(initial_balance=200_000, instrument="TMF").run(
        df, ind, OrFadeStrategy(allow_short=True), "balanced")
    days = df["datetime"].dt.date.nunique()
    if len(res.trades) == 0:
        import pytest; pytest.skip(f"OR fade 在 {days} 天上零訊號")
    avg = len(res.trades) / max(days, 1)
    assert avg < 10
    sides = {t["side"] for t in res.trades}
    # 至少一邊有；若只有單側 implementer 報告紀錄即可
    assert "long" in sides or "short" in sides
```

> **重要**：若兩個冒煙 test 都 SKIP（零訊號），不算 FAIL，但實作者必須**報告**這件事。可能原因：預設 `vol_ratio_max=0.7` 過嚴 + COVID slice 量能特性。grid 會涵蓋 0.5/0.7/0.9，下游能放寬。但若實作者覺得參數設計有 bug 應該排查。

- [ ] **Step 2: Run** `python -m pytest tests/test_or_fade.py::test_engine_smoke_long_only tests/test_or_fade.py::test_engine_smoke_long_short -v`

- [ ] **Step 3: 觀察並記錄**：每變體的 trade count、avg/day、sides 分佈、WR、net pnl、樣本 3 trades。若 SKIP 也要報告原因。

- [ ] **Step 4: Commit**
  ```bash
  git add tests/test_or_fade.py
  git commit -m "test(or_fade): P2d engine smoke for both variants on real MXF data"
  ```

**Gate P2：** 兩變體都能跑（PASS 或合理 SKIP），頻率合理。

---

## Task 6（P3）：粗篩 Grid（324 combos × 2 變體 × 2 商品）

**Files:** Create `scripts/optimize_or_fade.py`；append a tiny test to `tests/test_or_fade.py`.

照抄 `scripts/optimize_connors_rsi2.py`，改 3 處（少 1 處因為 connors_rsi2 已是 dict-based + `--allow-short`、osculate 100% 相容）：

- [ ] **Step 1: 複製 + 改策略 import + 工廠**

```python
from strategy.or_fade import OrFadeStrategy
def _make_strategy(params: dict, allow_short: bool):
    return OrFadeStrategy(allow_short=allow_short, **params)
```
worker 內 `_worker(args)` 解包 `params, allow_short, shm_meta = args`、`strat = _make_strategy(params, allow_short)`。

- [ ] **Step 2: 改 PARAM_GRID + combo builder**

```python
PARAM_GRID = {
    "or_bars":       [3, 6, 12],
    "vol_ratio_max": [0.5, 0.7, 0.9],
    "wait_bars":     [0, 1],
    "sl_atr":        [1.5, 2.0, 2.5],
    "max_bars":      [12, 18, 24],
    "cooldown":      [3, 5],
}
# 3·3·2·3·3·2 = 324 combos（**不論 allow_short**：or_fade 的雙向邏輯切換不增 grid 維度）

def _build_param_combos(allow_short: bool) -> list[dict]:
    """OR fade 的 allow_short 不影響 grid 維度（與 connors_rsi2 的 rsi_high 不同）；
    兩個變體都跑完整 324 combos。"""
    import itertools
    keys = list(PARAM_GRID.keys())
    return [dict(zip(keys, v)) for v in itertools.product(*PARAM_GRID.values())]
```
> 注意：connors_rsi2 的 `_build_param_combos` 在 long-only 時會 pop `rsi_high`；**OR fade 不需要**——allow_short 切的是「是否評估上緣觸發」邏輯，不涉及任何 PARAM_GRID 欄位。

- [ ] **Step 3: 改輸出路徑** — `data/or_fade/grid_or_fade_{symbol}_{variant}_{ts}.csv` + `best_params_or_fade_{symbol}_{variant}_{ts}.json`（JSON 含 `allow_short` 給 Task 7）。

- [ ] **Step 4: `_run_one` helper**（給 Task 7 + 本任務 unit test 用）

```python
def _run_one(params: dict, df, split_idx: int, allow_short: bool = False) -> dict:
    """同 connors_rsi2 _run_one 結構；改策略 import"""
    from core.gpu_indicators import precompute_all
    from backtest.fast_engine import FastBacktestEngine
    from strategy.or_fade import OrFadeStrategy
    from scripts.optimize_strategy import _calc_metrics
    ind = precompute_all(df, verbose=False)
    eng = FastBacktestEngine(initial_balance=200_000, instrument="TMF")
    strat_t = OrFadeStrategy(allow_short=allow_short, **params)
    r_train = eng.run(df, ind, strat_t, "balanced", start_idx=0, end_idx=split_idx)
    m_train = _calc_metrics(r_train)
    if split_idx < len(df) - 10:
        strat_v = OrFadeStrategy(allow_short=allow_short, **params)
        r_test = eng.run(df, ind, strat_v, "balanced", start_idx=split_idx, end_idx=len(df))
        m_test = _calc_metrics(r_test)
    else:
        m_test = {"n": 0, "wr": 0.0, "pf": 0.0, "ret": 0.0, "dd": 0.0, "sharpe": 0.0}
    if m_test["n"] < 5:
        wf = -99.0
    else:
        wf = (m_test["pf"]*0.40 + min(m_test["wr"]/50.0, 2.0)*0.25
              + max(m_test["sharpe"], -5.0)*0.20 + max(0.0, 1.0 - m_test["dd"]/20.0)*0.15)
    return {
        **{f"train_{k}": v for k, v in m_train.items()},
        **{f"test_{k}": v for k, v in m_test.items()},
        "wf_score": round(wf, 4), **params, "allow_short": allow_short,
    }
```

- [ ] **Step 5: CLI**（同 connors_rsi2）
  ```python
  parser.add_argument("--symbol", choices=["MXF", "TXF"], required=True)
  parser.add_argument("--allow-short", action="store_true")
  parser.add_argument("--dry-run", action="store_true")
  ```
  CLI 名稱 / 用法 / dry-run 行為與 connors_rsi2 100% 一致；輸出檔名前綴改為 `or_fade`。

- [ ] **Step 6: Unit test**

```python
def test_optimize_run_one_smoke():
    """Task 6 smoke: _run_one direct call"""
    import pandas as pd
    from pathlib import Path
    p = Path("data/vwap_fade/MXF_day_5m.parquet")
    if not p.exists():
        import pytest; pytest.skip("data 未準備")
    from scripts.optimize_or_fade import _run_one
    df = pd.read_parquet(p).head(2000).reset_index(drop=True)
    res = _run_one(
        {"or_bars": 6, "vol_ratio_max": 0.7, "wait_bars": 0,
         "sl_atr": 2.0, "max_bars": 18, "cooldown": 3},
        df, split_idx=1000, allow_short=False,
    )
    assert "wf_score" in res and "test_n" in res
```

- [ ] **Step 7: Dry-run 驗證**
  ```
  python scripts/optimize_or_fade.py --symbol MXF --dry-run
  python scripts/optimize_or_fade.py --symbol MXF --allow-short --dry-run
  ```
  確認兩條路徑都跑出 CSV + JSON 到 `data/or_fade/`。

- [ ] **Step 8: 完整 file pytest** → 22 PASS（21 prior + 1 new optimize smoke）

- [ ] **Step 9: Commit**
  ```bash
  git add scripts/optimize_or_fade.py tests/test_or_fade.py
  git commit -m "feat(or_fade): P3 grid optimizer (parallel, dual variants, 324 combos)"
  ```

**注意：本任務不跑完整 4 個 grid run**（那是 Task 7、~30 分）。

---

## Task 7（P4+P5）：OOS 終驗 + Gate 匯總（orchestrator）

**Files:** Create `scripts/run_or_fade_experiments.py`.

**完全照抄 `scripts/run_connors_rsi2_experiments.py`**，逐步改：
- 所有 `connors_rsi2` 字串 → `or_fade`
- import `OrFadeStrategy`、`from scripts.optimize_or_fade import _run_one`
- `valid_keys` 改為 OR fade 構造子接受的：`{"or_bars","vol_ratio_max","wait_bars","sl_atr","max_bars","cooldown","max_trades","entry_window_end","force_close","point_value"}`
- `valid_perturb_keys` 改為純數值維度：`{"or_bars","vol_ratio_max","wait_bars","sl_atr","max_bars","cooldown"}`
- 輸出路徑 `data/or_fade/robustness_report_<ts>.md`

- [ ] **Step 1: 寫 orchestrator** — **完全沿用** `scripts/run_connors_rsi2_experiments.py` 的實際 function 簽名與結構（**不要**照本計畫的 pseudocode 猜函式 signature；直接讀那支現成 script 為準）。只改：
  - 所有 `connors_rsi2` 字串 → `or_fade`
  - import `OrFadeStrategy` 而非 `ConnorsRsi2Strategy`
  - `from scripts.optimize_or_fade import _run_one` 而非 connors_rsi2 版
  - `valid_keys` / `valid_perturb_keys` 改為 OR fade 構造子接受的欄位
  - 輸出路徑改 `data/or_fade/`

**特別注意 `step4_robustness` 的本地 perturb 實作**（因 `perturb_and_run` 寫死 vwap_fade）：

```python
from scripts.optimize_or_fade import _run_one as _run_one_orf
# ... 在 step4_robustness 內：
for dim, base_val in perturb_base.items():
    if not isinstance(base_val, (int, float)) or isinstance(base_val, bool):
        continue
    runs = []
    for f in factors:
        p = dict(perturb_base)
        v = base_val * f
        if isinstance(base_val, int):
            v = max(1, int(round(v)))
        p[dim] = v
        r = _run_one_orf(p, df_oos, split_idx=0, allow_short=best["allow_short"])
        runs.append(float(r.get("test_ret", 0.0)))
    perturb_results[dim] = runs
```

- [ ] **Step 2: 跑**
  ```
  python scripts/run_or_fade_experiments.py
  ```
  預估 30-40 分（4 grid runs × ~5-8 min × multicore + OOS + perturb）。報告寫到 `data/or_fade/robustness_report_<ts>.md`。

- [ ] **Step 3: 人工判讀報告**，填「觀察與建議」段。若任一變體全 Gate 過 → 進 paper；都不過 → **設計 §9：鎖死 MR、pivot C2**。

- [ ] **Step 4: Commit**
  ```bash
  # 確認 .gitignore 不擋 data/or_fade/*.md；若擋用 git add -f
  git add scripts/run_or_fade_experiments.py data/or_fade/robustness_report_*.md
  git commit -m "feat(or_fade): P4+P5 OOS experiments + gate summary report"
  ```

**Gate 表（兩變體獨立評分；同 connors_rsi2）：**

| Gate | 門檻 |
|---|---|
| P3 雙商品 | 各變體 MXF/TXF Top-10 每維參數區間重疊 |
| 4-1 MC | PF p5 > 1.0 / 淨利 p5 > 0 / MDD p95 < 12% |
| 4-2 擾動 | worst-dim stability < 0.3 |
| 4-3 Regime | 震盪日淨利 > 0 / 趨勢日淨利 > -50% × \|震盪日淨利\| |
| P5 OOS | PF > 1.2 / 頻率 1-3 筆/日 / \|r\| breakout < 0.3 |

**若兩變體都 FAIL → 設計 §9：鎖死 MR，不開第四個 MR 策略，pivot C2**（拿這套 pipeline 調優既有 breakout/orb）。

---

## 開發紀律提醒（呼應設計文件 §11）

引擎事實全部繼承 `2026-05-28-vwap-fade-backtest-plan.md` §1 表。本計畫新增的紀律：

1. **`_OrSession` 跨日 reset**（與 `_RsiState` 連續相反）；換日由 update() 內 `_maybe_reset()` 自動偵測。
2. **OR-mid 鎖後不變**——check_exit 純讀、不寫 `_or`、不增量 `_session_bar`。
3. **wait_bars=1 pending state 只活 1 根**——下一根評估後必清空（無論進場或失敗）；換日清空；max_trades 滿或 cooldown 期間進入也要清。
4. **force_close 不設 cooldown**（當日已結束、無意義）。
5. **_ew_start 動態 = 08:45 + or_bars × 5min**——在 `__init__` 算、不在 on_kbar 動態算。
6. **進場用 `snapshot.volume_ratio`**（已預算欄位、不要自算）。
7. **ATR stop 天然 protective-side**（同 connors_rsi2）；但 v1 接受 ATR stop > OR-mid（風險>報酬目標）的組合、不 clamp，由 grid 自然調 sl_atr。
8. **per-run `allow_short`**——OR fade 的 allow_short 切的是邏輯分支、不影響 grid 維度（與 connors_rsi2 的 rsi_high 不同）。

## 未來工作（不在範圍）
- 上線掛載（與 breakout / orb 用 `position_lock` 互斥）：Gate 過 + paper 驗證後再開計畫
- v2 grid：把 `max_trades` / `entry_window_end` 列入 grid
- ML meta-labeling：純規則過 Gate 後才考慮
- **若本策略 FAIL → 不再開第四個 MR、走 §9 C2**
