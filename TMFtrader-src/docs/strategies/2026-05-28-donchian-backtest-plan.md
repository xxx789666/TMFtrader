# Donchian Breakout 回測 + 優化 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 Donchian Breakout 從設計變成「可回測、過 Stage 7 穩健性 Gate、過差異化 Gate（|r| < 0.3 vs live BreakoutTrend）、可上 paper」的實作。雙變體並排。最大化重用前 3 支策略已建的基礎設施。

**Architecture:** 新增 `strategy/donchian.py`（`_DonchianState` helper + `DonchianStrategy(BaseStrategy)`），用既有 `FastBacktestEngine`；grid 抄 `scripts/optimize_or_fade.py`；穩健性三件套**直接 import** `scripts/robustness_vwap_fade.py`；OOS orchestrator 抄 `scripts/run_or_fade_experiments.py`。不動 core/、不動既有策略檔（含 BreakoutTrendStrategy）。

**Tech Stack:** Python 3.12、pandas/numpy、pyarrow、現有 `core/gpu_indicators`、`ProcessPoolExecutor` + `shared_memory`、pytest。

**背景設計文件：** `TMFtrader-src/docs/strategies/2026-05-28-donchian-design.md`
**引擎事實參考：** `vwap-fade-backtest-plan.md §1` + `or-fade-backtest-plan.md` 「重大差異」（整套**繼承**）

---

## 與 or_fade plan 的重大差異（實作前必讀）

| # | 差異 | 對任務的影響 |
|---|---|---|
| 1 | **`_DonchianState` 替代 `_OrSession`** | deque of (high, low)、maxlen=max(entry_n,exit_k)+1；提供 entry_n_high/low (**不含當前 bar**) + exit_k_high/low (**含當前 bar**) + warmup_ready。含/不含當前 bar 是 lookahead 防線、**TDD 必須釘死** |
| 2 | **無 wait_bars pending state** | 進場直接觸發、無 pending 機制 → 比 or_fade 簡單 |
| 3 | **無 vol_ratio 過濾** | 純結構觸發、無 volume 條件 → 比 or_fade 少一過濾維度 |
| 4 | **Trend 統計特性**：WR 30-50% 常態 | Gate 評 PF/Sharpe **不評 WR**；冒煙 test 別把 WR<50% 當 FAIL |
| 5 | **4-3 Regime 判讀對調** | trend「主場」= 趨勢日 (ADX>25)；split_by_regime 函式不改、Gate 解讀方向反轉 |
| 6 | **P5 相關性 Gate 必跑成、不再 best-effort** | 類名是 `BreakoutTrendStrategy`（不是 BreakoutStrategy）；前兩支 orchestrator 寫錯 deferred、本計畫修對 |
| 7 | **on_kbar 呼叫順序硬規定** | `_donchian.update(...)` → guards → entry checks；這順序讓 `[-entry_n-1:-1]` slice 正確排除剛 append 的當前 bar |
| 8 | **最小樣本門檻** | grid 結果若 `test_n < 100` → wf_score = -50.0（重 penalty）避免 N=30 偶遇好運過 gate |
| 9 | **Type B FAIL 判定** | `\|r\|` ≥ 0.6 vs BreakoutTrend → 視為「換包裝」、Gate 全過也不部署 |
| 10 | **絕對路徑** | 所有 git / Write 從 repo root 用絕對路徑（cwd 在 TMFtrader-src/ + 相對路徑 + unicode `永豐微台指` 會撞 INDEX 雙條目 bug） |
| 11 | **daily PnL keying 用 exit_time（不是 entry_time）** | 父 or_fade orchestrator step5 用 `entry_time`、但引擎的 `res_b.daily_pnl` 是用 `exit_time.strftime("%Y-%m-%d")` 當 key（`fast_engine.py:175`）。Donchian step5 改用 `exit_time` 才能對齊；否則跨日 close 的 trade 兩邊算進不同 bucket、corr 失真 |

---

## 可直接重用的設施（不重造）

| 資源 | 路徑 | 用法 |
|---|---|---|
| 資料 | `data/vwap_fade/*.parquet` | 同前 |
| 動態稅 | `core/position.py:240-244` | 同前 |
| grid 並行骨架 | `scripts/optimize_or_fade.py` | 抄、改 PARAM_GRID + 策略 import + 輸出路徑 |
| 單 backtest helper | `_run_one(params, df, split_idx, allow_short)` | 模式相同（donchian 版自己寫一個含 sample-size penalty） |
| 績效指標 | `from scripts.optimize_strategy import _calc_metrics` | 不重寫 |
| 三件套 | `from scripts.robustness_vwap_fade import monte_carlo, stability, split_by_regime` | 直接 import |
| OOS orchestrator | `scripts/run_or_fade_experiments.py` | 抄、改策略 import + 輸出路徑、**step5 改成必跑成功** |
| 成本 test | `tests/test_or_fade.py::test_cost_accounting_*` | 內容直接複製到 `tests/test_donchian.py` |
| 策略結構模板 | `strategy/or_fade.py` | `_maybe_daily_reset`、`_signal`、`check_exit` 結構照搬 |

---

## 檔案結構

| 檔案 | 職責 | 動作 |
|---|---|---|
| `strategy/donchian.py` | `_DonchianState` + `DonchianStrategy`（on_kbar + check_exit） | Create |
| `tests/test_donchian.py` | 策略單元測試 | Create |
| `scripts/optimize_donchian.py` | 粗篩 grid（抄 optimize_or_fade.py） | Create |
| `scripts/run_donchian_experiments.py` | OOS 實驗 + Gate 匯總 orchestrator | Create |
| `docs/strategies/2026-05-28-donchian-backtest-plan.md` | 本計畫 | （已存在） |

零改動：core/、既有 strategy/*.py、既有 backtest/*、`scripts/robustness_vwap_fade.py`、`scripts/optimize_*.py`（or_fade/connors_rsi2/vwap_fade/strategy momentum）、`data/vwap_fade/*`。

---

## ⚠️ 絕對路徑備忘（每個 task 都受影響）

之前一個 session 在 `TMFtrader-src/` subdir + 相對路徑撞 unicode 路徑 bug、INDEX 出現雙條目。每個 task 的 git / Write 都遵守：

- **所有 Bash 操作（git/cd/pytest）從 repo root 起算**：
  ```bash
  cd "C:/Users/xx/Desktop/vps永豐微台指" && <command>
  ```
  或 `<command>` 使用相對 repo root 的路徑（如 `git add TMFtrader-src/strategy/donchian.py`）
- **Write tool 用絕對 Windows 路徑**：
  ```
  C:\Users\xx\Desktop\vps永豐微台指\TMFtrader-src\<...>
  ```
- pytest 從 TMFtrader-src/ 跑：
  ```bash
  cd "C:/Users/xx/Desktop/vps永豐微台指/TMFtrader-src" && python -m pytest <...>
  ```
  pytest 路徑問題影響小、但 git 嚴格用 repo root

---

## Task 1（P0）：成本會計驗證（動態稅）

**Files:** Test `tests/test_donchian.py`（新檔，**絕對路徑 Write**）

內容與 `tests/test_or_fade.py` 的 P0 兩個 test 相同——驗證引擎、不是驗證策略；分檔避免跨檔 import test helpers。

- [ ] **Step 1: Write 測試** — 用絕對路徑 `C:\Users\xx\Desktop\vps永豐微台指\TMFtrader-src\tests\test_donchian.py`

```python
"""
Donchian Breakout 策略測試
P0: 成本會計驗證（動態稅模型，commit a95ae44 起）
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import math
from core.position import PositionManager, Side
from core.instrument_config import INSTRUMENT_SPECS


def test_cost_accounting_dynamic_tax():
    """@22000 → commission=46, net_pnl=54"""
    spec = INSTRUMENT_SPECS["TMF"]
    pm = PositionManager(instruments=["TMF"], configs={"TMF": spec}, initial_balance=200_000)
    pm.open_position("TMF", Side.LONG, price=22000.0, quantity=1,
                     stop_loss=21950, take_profit=22050, timestamp=None)
    trade = pm.close_position("TMF", 22010.0, "test", None)
    assert trade.pnl == 100.0
    assert trade.commission == 46.0
    assert trade.net_pnl == 54.0


def test_cost_accounting_high_price_tax_scales():
    """@45000 → 動態縮放"""
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

- [ ] **Step 2: Run from repo root**
  ```bash
  cd "C:/Users/xx/Desktop/vps永豐微台指/TMFtrader-src" && python -m pytest tests/test_donchian.py -v
  ```
  Expected: 2 PASS。FAIL → stop, report exact actuals, 不要 mutate assertion。

- [ ] **Step 3: Commit from repo root**
  ```bash
  cd "C:/Users/xx/Desktop/vps永豐微台指" && \
  git add TMFtrader-src/tests/test_donchian.py && \
  git commit -m "test(donchian): P0 verify dynamic tax in net_pnl"
  ```

**Gate P0：** 2 PASS 才往下。

---

## Task 2（P2a）：`_DonchianState` helper（純邏輯 TDD，含 lookahead 防線）

**Files:** Create `strategy/donchian.py`（this task: ONLY `_DonchianState` + imports）；append to `tests/test_donchian.py`.

- [ ] **Step 1: Write 失敗測試** — append 至 `tests/test_donchian.py`

```python
def test_donchian_not_warmup_initially():
    from strategy.donchian import _DonchianState
    import math
    s = _DonchianState(entry_n=3, exit_k=2)
    assert s.warmup_ready is False
    assert math.isnan(s.entry_n_high)
    assert math.isnan(s.exit_k_high)   # 0 bars → nan


def test_donchian_warmup_after_n_plus_1_bars():
    """warmup_ready 需 len > entry_n（要 entry_n+1 根才能算 entry_n 個 prior bars）"""
    from strategy.donchian import _DonchianState
    from datetime import datetime
    s = _DonchianState(entry_n=3, exit_k=2)
    for i, (h, l) in enumerate([(10, 8), (11, 7), (12, 6)]):
        s.update(datetime(2024, 1, 2, 9, i*5), h, l)
    assert s.warmup_ready is False   # 3 bars, 不夠
    s.update(datetime(2024, 1, 2, 9, 15), 9, 5)   # 第 4 根
    assert s.warmup_ready is True
    # deque 已 append 4 根、maxlen=max(3,2)+1=4，deque = [(10,8),(11,7),(12,6),(9,5)]
    # entry_n_high = max of bars[-4:-1] 的 high = max(10,11,12) = 12（第 4 根 high=9 不算）
    # entry_n_low  = min of bars[-4:-1] 的 low  = min(8,7,6)  = 6（第 4 根 low=5 不算）
    assert s.entry_n_high == 12
    assert s.entry_n_low  == 6


def test_donchian_entry_EXCLUDES_current_bar():
    """KEY lookahead 防線：剛 append 的 bar 的 high 不應出現在 entry_n_high"""
    from strategy.donchian import _DonchianState
    from datetime import datetime
    s = _DonchianState(entry_n=3, exit_k=2)
    for i, (h, l) in enumerate([(10, 8), (11, 7), (12, 6), (13, 5)]):
        s.update(datetime(2024, 1, 2, 9, i*5), h, l)
    assert s.warmup_ready
    # 此時 deque = [(10,8),(11,7),(12,6),(13,5)]（maxlen=4）
    # entry_n_high = max of bars[-4:-1] = max of [(10),(11),(12)] = 12（**NOT 13!**）
    assert s.entry_n_high == 12, "entry_n_high MUST exclude current bar 13 (lookahead防線)"
    # Append 一根極端新高
    s.update(datetime(2024, 1, 2, 9, 20), 99, 1)
    # deque = [(11,7),(12,6),(13,5),(99,1)]（maxlen=4 推掉最舊）
    # entry_n_high = max of bars[-4:-1] = max of [(11),(12),(13)] = 13（**NOT 99!**）
    assert s.entry_n_high == 13, "after new append, entry_n_high still EXCLUDES current bar"


def test_donchian_exit_INCLUDES_current_bar():
    """exit_k_high/low 含當前 bar（trailing 即時反應）"""
    from strategy.donchian import _DonchianState
    from datetime import datetime
    s = _DonchianState(entry_n=3, exit_k=2)
    for i, (h, l) in enumerate([(10, 8), (11, 7), (12, 6)]):
        s.update(datetime(2024, 1, 2, 9, i*5), h, l)
    # exit_k=2，含當前 → exit_k_high = max of last 2 highs = max(11, 12) = 12
    assert s.exit_k_high == 12
    assert s.exit_k_low  == 6
    # Append 新高
    s.update(datetime(2024, 1, 2, 9, 15), 99, 5)
    # exit_k_high = max of last 2 = max(12, 99) = 99（**INCLUDES current!**）
    assert s.exit_k_high == 99, "exit_k_high MUST include current bar 99"
    assert s.exit_k_low  == 5    # min of last 2 = min(6, 5) = 5


def test_donchian_resets_across_days():
    from strategy.donchian import _DonchianState
    from datetime import datetime
    import math
    s = _DonchianState(entry_n=2, exit_k=2)
    for i, (h, l) in enumerate([(10, 8), (11, 7), (12, 6)]):
        s.update(datetime(2024, 1, 2, 9, i*5), h, l)
    assert s.warmup_ready
    # 換日第一根
    s.update(datetime(2024, 1, 3, 9, 0), 20, 18)
    assert s.warmup_ready is False   # reset 後只 1 根
    assert math.isnan(s.entry_n_high)
    # 第 2, 3 根
    s.update(datetime(2024, 1, 3, 9, 5), 22, 17)
    s.update(datetime(2024, 1, 3, 9, 10), 21, 19)
    assert s.warmup_ready   # 3 根 > entry_n=2
    # entry_n_high = max of bars[-3:-1] = max([(20),(22)]) = 22
    assert s.entry_n_high == 22


def test_donchian_invalid_params():
    from strategy.donchian import _DonchianState
    import pytest
    with pytest.raises(ValueError):
        _DonchianState(entry_n=0, exit_k=2)
    with pytest.raises(ValueError):
        _DonchianState(entry_n=2, exit_k=0)
```

_(註解已在 code block 內統一、無 placeholder fix-up 步驟)_

- [ ] **Step 2: Run** — `python -m pytest tests/test_donchian.py -k donchian -v`，Expected: FAIL（module not found）

- [ ] **Step 3: Create `strategy/donchian.py`** — 用絕對路徑 Write
  `C:\Users\xx\Desktop\vps永豐微台指\TMFtrader-src\strategy\donchian.py`

```python
"""
Donchian Breakout 策略（vwap_fade §9 C1 後第一支 trend-following）。
  - _DonchianState   : N+K+1 根滑動視窗、entry 不含當前 bar、exit 含當前 bar
  - DonchianStrategy : 結構性突破進場 + opposite K-bar trailing 出場（Task P2b/P2c，待後續任務）
"""

from collections import deque
from datetime import datetime, time
from typing import Optional
import math

# Task P2b/P2c 進出場邏輯所需 import（待後續任務取消註解）
# from strategy.base import BaseStrategy, Signal, SignalDirection
# from core.market_data import KBar, MarketSnapshot
# from core.position import Position, Side


class _DonchianState:
    """Rolling Donchian state with lookahead-safe entry slicing.

    Attributes
    ----------
    warmup_ready : True 當 deque 有 > entry_n 根 bar（才有 entry_n 個 prior bars 可比）
    entry_n_high : max(highs of bars [-entry_n-1 : -1])  ← **不含**當前 bar
    entry_n_low  : min(lows  of bars [-entry_n-1 : -1])
    exit_k_high  : max(highs of bars [-exit_k :])        ← **含**當前 bar
    exit_k_low   : min(lows  of bars [-exit_k :])
    跨日由 update() 內 _maybe_reset 自動清空 deque。
    """

    def __init__(self, entry_n: int, exit_k: int):
        if entry_n < 1:
            raise ValueError(f"entry_n must be >= 1, got {entry_n}")
        if exit_k < 1:
            raise ValueError(f"exit_k must be >= 1, got {exit_k}")
        self.entry_n = entry_n
        self.exit_k = exit_k
        cap = max(entry_n, exit_k) + 1
        self._highs: deque = deque(maxlen=cap)
        self._lows:  deque = deque(maxlen=cap)
        self._date = None

    def _maybe_reset(self, dt: datetime) -> None:
        d = dt.date()
        if d != self._date:
            self._date = d
            self._highs.clear()
            self._lows.clear()

    def update(self, dt: datetime, high: float, low: float) -> None:
        """收一根 K。每次呼叫先做換日 reset 偵測。"""
        self._maybe_reset(dt)
        self._highs.append(high)
        self._lows.append(low)

    @property
    def warmup_ready(self) -> bool:
        # 需 entry_n+1 根 bar 才能取 entry_n 個 prior bars
        return len(self._highs) > self.entry_n

    @property
    def entry_n_high(self) -> float:
        if len(self._highs) <= self.entry_n:
            return float("nan")
        # 不含當前 bar：[-entry_n-1 : -1]
        return max(list(self._highs)[-self.entry_n - 1 : -1])

    @property
    def entry_n_low(self) -> float:
        if len(self._lows) <= self.entry_n:
            return float("nan")
        return min(list(self._lows)[-self.entry_n - 1 : -1])

    @property
    def exit_k_high(self) -> float:
        if len(self._highs) == 0:
            return float("nan")
        # 含當前 bar：[-exit_k :]
        return max(list(self._highs)[-self.exit_k :])

    @property
    def exit_k_low(self) -> float:
        if len(self._lows) == 0:
            return float("nan")
        return min(list(self._lows)[-self.exit_k :])


# ── DonchianStrategy 由 Task P2b/P2c 新增於此 ────────────────────────────────
```

- [ ] **Step 4: Run** — `python -m pytest tests/test_donchian.py -k donchian -v` → 6 PASS

完整 file: `python -m pytest tests/test_donchian.py -v` → 8 PASS（2 P0 + 6 donchian）

- [ ] **Step 5: Commit from repo root**
  ```bash
  cd "C:/Users/xx/Desktop/vps永豐微台指" && \
  git add TMFtrader-src/strategy/donchian.py TMFtrader-src/tests/test_donchian.py && \
  git commit -m "feat(donchian): P2a _DonchianState helper with lookahead-safe slicing"
  ```

---

## Task 3（P2b）：`DonchianStrategy.__init__` + `on_kbar`（呼叫順序鐵律）

**Files:** Modify `strategy/donchian.py`；append to `tests/test_donchian.py`.

### `on_kbar` 呼叫順序（鐵律、不可改）

```
1. _maybe_daily_reset(ts)
2. _donchian.update(ts, kbar.high, kbar.low)   ← UPDATE FIRST
3. _session_bar += 1
4. 守衛：warmup → entry_window → max_trades → cooldown
5. 進場判定：使用 _donchian.entry_n_high（不含剛 append 的當前 bar）
```

如果違反順序（例如先讀 entry_n_high 再 update），lookahead 防線失效。

- [ ] **Step 1: Write 失敗測試** — append helpers + 進場 tests

```python
# ── Shared helpers (used by Tasks 4, 5) ────────────────────────────────────
def _make_snap(price, adx, atr, ts):
    from core.market_data import MarketSnapshot
    s = MarketSnapshot()
    s.price = price; s.adx = adx; s.atr = atr; s.timestamp = ts; s.volume = 100
    return s


def _kbar(ts, o=None, h=None, l=None, c=None, vol=100):
    """O/H/L/C 可指定；Donchian 對 high/low 敏感。"""
    from core.market_data import KBar
    if c is None: c = 100.0
    if o is None: o = c
    if h is None: h = c + 1
    if l is None: l = c - 1
    return KBar(ts, o, h, l, c, vol)


# ── Task 3 entry tests ─────────────────────────────────────────────────────
def test_entry_window_dynamic_start():
    """entry_n=20 → _ew_start = 08:45 + 20*5min = 10:25"""
    from strategy.donchian import DonchianStrategy
    from datetime import time
    assert DonchianStrategy(entry_n=10)._ew_start == time(9, 35)
    assert DonchianStrategy(entry_n=20)._ew_start == time(10, 25)
    assert DonchianStrategy(entry_n=30)._ew_start == time(11, 15)


def test_entry_blocked_until_warmup():
    """warmup 未達 → 即使 close > 過去高也不進場（warmup 條件 = len > entry_n）"""
    from strategy.donchian import DonchianStrategy
    from datetime import datetime
    strat = DonchianStrategy(entry_n=3, allow_short=False)
    # 只灌 3 根（不到 entry_n+1）
    for i, p in enumerate([100, 101, 102]):
        ts = datetime(2024, 1, 2, 9, 35 + i*5)   # 9:35 = _ew_start for entry_n=3 → not exactly, but post-warmup focus
        strat.on_kbar(_kbar(ts, c=p, h=p, l=p-1), _make_snap(p, 30, 2, ts))
    assert strat._trades_today == 0


def test_entry_long_on_breakout_above_n_bar_high():
    """收 N+1 根之後 close > prior-N-bar high → BUY"""
    from strategy.donchian import DonchianStrategy
    from strategy.base import SignalDirection
    from datetime import datetime
    strat = DonchianStrategy(entry_n=3, exit_k=2, sl_atr=2.0, allow_short=False)
    # 灌 4 根，最高 = 105（OR 觸發後續突破會 close > 105）
    # entry_n=3 → _ew_start = 08:45 + 15min = 09:00
    prices = [100, 102, 105, 103]
    for i, p in enumerate(prices):
        ts = datetime(2024, 1, 2, 9, 0 + i*5)
        strat.on_kbar(_kbar(ts, c=p, h=p, l=p-1), _make_snap(p, 30, 2, ts))
    # 此時 deque 中 highs = [100, 102, 105, 103]（maxlen=4）
    # entry_n_high = max of bars[-4:-1] = max(100, 102, 105) = 105
    # 下一根 close=107 > 105 → BUY
    ts = datetime(2024, 1, 2, 9, 20)
    sig = strat.on_kbar(_kbar(ts, c=107, h=107, l=106), _make_snap(107, 30, 2, ts))
    assert sig is not None and sig.direction == SignalDirection.BUY
    assert sig.stop_loss < 107   # ATR stop 在進場價下方


def test_no_entry_when_close_inside_range():
    """close 在 N-bar range 內 → 不進場"""
    from strategy.donchian import DonchianStrategy
    from datetime import datetime
    strat = DonchianStrategy(entry_n=3, allow_short=True)
    for i, p in enumerate([100, 102, 105, 103]):
        ts = datetime(2024, 1, 2, 9, 0 + i*5)
        strat.on_kbar(_kbar(ts, c=p, h=p, l=p-1), _make_snap(p, 30, 2, ts))
    # 下一根 close=104（介於 99 和 105 之間）→ 不進場
    ts = datetime(2024, 1, 2, 9, 20)
    sig = strat.on_kbar(_kbar(ts, c=104, h=104, l=103), _make_snap(104, 30, 2, ts))
    assert sig is None


def test_short_blocked_when_allow_short_false():
    """allow_short=False → close < N-bar low 也不進場"""
    from strategy.donchian import DonchianStrategy
    from datetime import datetime
    strat = DonchianStrategy(entry_n=3, allow_short=False)
    for i, p in enumerate([100, 102, 105, 103]):
        ts = datetime(2024, 1, 2, 9, 0 + i*5)
        strat.on_kbar(_kbar(ts, c=p, h=p, l=p-1), _make_snap(p, 30, 2, ts))
    # 下一根 close=95（< prior-N low=99）→ 但 allow_short=False
    ts = datetime(2024, 1, 2, 9, 20)
    sig = strat.on_kbar(_kbar(ts, c=95, h=95, l=94), _make_snap(95, 30, 2, ts))
    assert sig is None


def test_short_fires_when_allow_short_true():
    """allow_short=True + close < N-bar low → SELL"""
    from strategy.donchian import DonchianStrategy
    from strategy.base import SignalDirection
    from datetime import datetime
    strat = DonchianStrategy(entry_n=3, sl_atr=2.0, allow_short=True)
    for i, p in enumerate([100, 102, 105, 103]):
        ts = datetime(2024, 1, 2, 9, 0 + i*5)
        strat.on_kbar(_kbar(ts, c=p, h=p, l=p-1), _make_snap(p, 30, 2, ts))
    # entry_n_low = min of bars[-4:-1] lows = min(99, 101, 104) = 99
    ts = datetime(2024, 1, 2, 9, 20)
    sig = strat.on_kbar(_kbar(ts, c=98, h=98, l=97), _make_snap(98, 30, 2, ts))
    assert sig is not None and sig.direction == SignalDirection.SELL
    assert sig.stop_loss > 98   # 空單 stop 在進場價上方


def test_entry_window_blocks_pre_open():
    """entry_n=3 → _ew_start=09:00；08:55 即使突破也不進場"""
    from strategy.donchian import DonchianStrategy
    from datetime import datetime
    strat = DonchianStrategy(entry_n=3, allow_short=False)
    # 全部在 _ew_start 之前
    prices = [100, 102, 105, 103, 107]
    for i, p in enumerate(prices):
        ts = datetime(2024, 1, 2, 8, 35 + i*5)   # 08:35~08:55
        strat.on_kbar(_kbar(ts, c=p, h=p, l=p-1), _make_snap(p, 30, 2, ts))
    assert strat._trades_today == 0


def test_max_trades_per_day_caps():
    """單日 _trades_today >= max_trades 後不再進新場"""
    from strategy.donchian import DonchianStrategy
    from datetime import datetime
    strat = DonchianStrategy(entry_n=2, max_trades=2, allow_short=False)
    # 灌一連串明顯遞增的 close（每根都會破前 N 高）
    # entry_n=2 → _ew_start = 08:55、第 3 根（08:55）才能進
    base = 100.0
    for i in range(20):
        # 確保 entry_n=2 -> _ew_start=08:55，從 08:45 起 第 3 根 = 08:55，可進
        ts = datetime(2024, 1, 2, 8, 45 + i*5 if (45 + i*5) < 60 else 0 + (i*5 + 45 - 60))
        # 簡化：用 ascending time
        ts = datetime(2024, 1, 2, 9, i*5)
        p = base + i * 2
        strat.on_kbar(_kbar(ts, c=p, h=p, l=p-1), _make_snap(p, 30, 2, ts))
    assert strat._trades_today == 2


def test_daily_reset_clears_state_but_donchian_resets_via_update():
    """跨日：_trades_today=0、_session_bar=1（第一根後）；_donchian 自己 reset"""
    from strategy.donchian import DonchianStrategy
    from datetime import datetime
    strat = DonchianStrategy(entry_n=2, allow_short=False)
    for i, p in enumerate([100, 102, 105]):
        ts = datetime(2024, 1, 2, 9, i*5)
        strat.on_kbar(_kbar(ts, c=p, h=p, l=p-1), _make_snap(p, 30, 2, ts))
    # 換日第一根
    ts2 = datetime(2024, 1, 3, 8, 45)
    strat.on_kbar(_kbar(ts2, c=200, h=200, l=199), _make_snap(200, 30, 2, ts2))
    assert strat._trades_today == 0
    assert strat._session_bar == 1
    assert strat._donchian.warmup_ready is False   # 換日後重 warmup
```

- [ ] **Step 2: Run, confirm FAIL**

- [ ] **Step 3: Implement** — Modify `strategy/donchian.py`：取消 3 個 import 註解，加入：

```python
class DonchianStrategy(BaseStrategy):
    def __init__(
        self,
        entry_n: int = 20,
        exit_k: int = 10,
        sl_atr: float = 2.0,
        max_bars: int = 48,
        cooldown: int = 3,
        max_trades: int = 5,
        entry_window_end: str = "12:00",
        force_close: str = "13:25",
        allow_short: bool = False,
        point_value: float = 10.0,
    ):
        self.entry_n = entry_n
        self.exit_k = exit_k
        self.sl_atr = sl_atr
        self.max_bars = max_bars
        self.cooldown = cooldown
        self.max_trades = max_trades
        self.allow_short = allow_short
        self.point_value = point_value

        # _ew_start 動態 = 08:45 + entry_n*5min
        ew_start_min = 8*60 + 45 + entry_n * 5
        self._ew_start = time(ew_start_min // 60, ew_start_min % 60)
        self._ew_end = time.fromisoformat(entry_window_end)
        self._force_close = time.fromisoformat(force_close)

        self._donchian = _DonchianState(entry_n=entry_n, exit_k=exit_k)
        self._trades_today = 0
        self._cooldown_until_bar = -1
        self._session_bar = 0
        self._day = None

    @property
    def name(self) -> str:
        return "donchian"

    def _maybe_daily_reset(self, dt: datetime) -> None:
        d = dt.date()
        if d != self._day:
            self._day = d
            self._trades_today = 0
            self._cooldown_until_bar = -1
            self._session_bar = 0
            # _donchian 由 update() 內 _maybe_reset 自動清空

    def on_kbar(self, kbar, snapshot):
        ts = kbar.datetime
        # ── 鐵律順序：reset → update → session_bar → guards → entry ──────
        self._maybe_daily_reset(ts)
        self._donchian.update(ts, kbar.high, kbar.low)   # UPDATE FIRST
        self._session_bar += 1

        # 守衛
        if not self._donchian.warmup_ready:
            return None
        if not (self._ew_start <= ts.time() < self._ew_end):
            return None
        if self._trades_today >= self.max_trades:
            return None
        if self._session_bar <= self._cooldown_until_bar:
            return None

        # 進場（entry_n_high/low 不含當前 bar）
        atr = max(snapshot.atr, 1.0)
        close = kbar.close
        if close > self._donchian.entry_n_high:
            stop = close - self.sl_atr * atr
            return self._signal(SignalDirection.BUY, close, stop)
        if self.allow_short and close < self._donchian.entry_n_low:
            stop = close + self.sl_atr * atr
            return self._signal(SignalDirection.SELL, close, stop)
        return None

    def _signal(self, direction, price, stop):
        self._trades_today += 1
        return Signal(
            direction=direction, strength=1.0,
            stop_loss=round(stop, 1), take_profit=0,
            reason=f"donchian {direction.value} N={self.entry_n}",
            source=self.name,
        )
```

- [ ] **Step 4: Run** — `python -m pytest tests/test_donchian.py -v` → 17 PASS（2 P0 + 6 state + 9 entry）

- [ ] **Step 5: Commit from repo root**
  ```bash
  cd "C:/Users/xx/Desktop/vps永豐微台指" && \
  git add TMFtrader-src/strategy/donchian.py TMFtrader-src/tests/test_donchian.py && \
  git commit -m "feat(donchian): P2b on_kbar with strict update-then-guards order"
  ```

---

## Task 4（P2c）：`check_exit` + cooldown 接線

**Files:** Modify `strategy/donchian.py`；append to `tests/test_donchian.py`.

關鍵：用 `snapshot.timestamp`（非 `self._current_bar_time`）；**不 update `_donchian`、不 increment `_session_bar`**；ATR stop 設 cooldown、force_close **不**設 cooldown。

- [ ] **Step 1: Write 失敗測試**

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


def _strat_with_warmed_donchian(entry_n=3, exit_k=2, allow_short=False, cooldown=3, max_bars=48):
    """灌 entry_n+1 根、讓 warmup 過、回 strat。

    注意：warmup 過程中、若 close 突破 prior-N-bar high、會自然觸發 entry signal。
    回來的 strat 可能 `_trades_today >= 1`。Task 4 exit tests 不檢查 _trades_today、不影響。
    `_session_bar == entry_n+1` 是穩定的（每 on_kbar 增量、無 check_exit 干擾）。"""
    from strategy.donchian import DonchianStrategy
    from datetime import datetime
    strat = DonchianStrategy(entry_n=entry_n, exit_k=exit_k, allow_short=allow_short,
                             cooldown=cooldown, max_bars=max_bars)
    for i in range(entry_n + 1):
        ts = datetime(2024, 1, 2, 9, i*5)
        # 灌 100, 102, 105, 103 之類有明顯範圍的 bars
        p = 100 + i * 2
        strat.on_kbar(_kbar(ts, c=p, h=p+1, l=p-1), _make_snap(p, 30, 2, ts))
    assert strat._donchian.warmup_ready
    return strat


def test_exit_force_close_no_cooldown():
    from strategy.base import SignalDirection
    from core.position import Side
    from datetime import datetime
    strat = _strat_with_warmed_donchian()
    cd_before = strat._cooldown_until_bar
    snap = _make_snap(105, 30, 2, datetime(2024, 1, 2, 13, 25))
    sig = strat.check_exit(_pos(Side.LONG, 104, 100, 5), snap)
    assert sig is not None and sig.direction == SignalDirection.CLOSE and "盤末" in sig.reason
    assert strat._cooldown_until_bar == cd_before   # NOT set on force_close


def test_exit_atr_stop_long_sets_cooldown():
    from core.position import Side
    from datetime import datetime
    strat = _strat_with_warmed_donchian(cooldown=3)
    bar_at_stop = strat._session_bar
    snap = _make_snap(99, 30, 2, datetime(2024, 1, 2, 10, 0))
    sig = strat.check_exit(_pos(Side.LONG, 104, 100, 3), snap)
    assert sig is not None and "停損" in sig.reason
    assert strat._cooldown_until_bar == bar_at_stop + 3


def test_exit_atr_stop_short_sets_cooldown():
    from core.position import Side
    from datetime import datetime
    strat = _strat_with_warmed_donchian(cooldown=3, allow_short=True)
    bar_at_stop = strat._session_bar
    snap = _make_snap(112, 30, 2, datetime(2024, 1, 2, 10, 0))
    sig = strat.check_exit(_pos(Side.SHORT, 105, 110, 3), snap)
    assert sig is not None and "停損" in sig.reason
    assert strat._cooldown_until_bar == bar_at_stop + 3


def test_exit_donchian_k_low_trailing_long():
    """多單 price < exit_k_low → trailing 觸發"""
    from core.position import Side
    from datetime import datetime
    strat = _strat_with_warmed_donchian(exit_k=2)
    # 灌好的 bars: highs/lows from _strat_with_warmed_donchian (4 bars, l = 99, 101, 104, 105)
    # exit_k_low = min of last 2 lows = min(104, 105) = 104（含當前）
    # 多單 entry 100, 當前 price 103 < 104 → 觸發
    snap = _make_snap(103, 30, 2, datetime(2024, 1, 2, 10, 0))
    sig = strat.check_exit(_pos(Side.LONG, 100, 90, 3), snap)
    assert sig is not None and "Donchian" in sig.reason


def test_exit_donchian_k_high_trailing_short():
    """空單 price > exit_k_high → trailing 觸發"""
    from core.position import Side
    from datetime import datetime
    strat = _strat_with_warmed_donchian(exit_k=2, allow_short=True)
    # exit_k_high = max of last 2 highs = max(105, 106) = 106
    # 空單 entry 100, 當前 price 107 > 106 → 觸發
    snap = _make_snap(107, 30, 2, datetime(2024, 1, 2, 10, 0))
    sig = strat.check_exit(_pos(Side.SHORT, 100, 110, 3), snap)
    assert sig is not None and "Donchian" in sig.reason


def test_exit_time_stop():
    """time stop 觸發（避開 stop 與 trailing）"""
    from core.position import Side
    from datetime import datetime
    # 用大 stop、大 exit_k 避免其他出場提前觸發
    strat = _strat_with_warmed_donchian(exit_k=2, max_bars=18)
    snap = _make_snap(110, 30, 2, datetime(2024, 1, 2, 11, 0))
    # 多單 entry 100, stop 80（很遠），exit_k_low = max trailing （價 110 > exit_k_low）不觸發
    # bars=19 > max_bars=18 → time stop
    sig = strat.check_exit(_pos(Side.LONG, 100, 80, 19), snap)
    assert sig is not None and "時間停損" in sig.reason


def test_check_exit_does_not_mutate_state():
    """invariant：check_exit 不 update _donchian、不 increment _session_bar"""
    from core.position import Side
    from datetime import datetime
    strat = _strat_with_warmed_donchian()
    high_before = strat._donchian.entry_n_high
    low_before  = strat._donchian.entry_n_low
    session_bar_before = strat._session_bar
    snap = _make_snap(103, 30, 2, datetime(2024, 1, 2, 10, 0))
    strat.check_exit(_pos(Side.LONG, 100, 90, 3), snap)
    assert strat._donchian.entry_n_high == high_before
    assert strat._donchian.entry_n_low  == low_before
    assert strat._session_bar == session_bar_before
```

- [ ] **Step 2: Run, confirm FAIL**（check_exit 未實作）

- [ ] **Step 3: Implement** — 在 `DonchianStrategy` 內加：

```python
    def check_exit(self, position, snapshot):
        if position.is_flat:
            return None
        ts = snapshot.timestamp                # 真實 bar 時間
        price = snapshot.price
        # 注意：不 update _donchian（state 已由 on_kbar update 完）、不 increment _session_bar
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
        # 3) Donchian K-bar trailing exit
        if is_long:
            ek_low = self._donchian.exit_k_low
            if not math.isnan(ek_low) and price < ek_low:
                return close_sig(f"Donchian K-low trail @ {price:.0f}")
        else:
            ek_high = self._donchian.exit_k_high
            if not math.isnan(ek_high) and price > ek_high:
                return close_sig(f"Donchian K-high trail @ {price:.0f}")
        # 4) 時間停損
        if position.bars_since_entry > self.max_bars:
            return close_sig(f"時間停損 {position.bars_since_entry}根")
        return None

    def get_parameters(self):
        return {
            "entry_n": self.entry_n, "exit_k": self.exit_k, "sl_atr": self.sl_atr,
            "max_bars": self.max_bars, "cooldown": self.cooldown,
            "max_trades": self.max_trades, "allow_short": self.allow_short,
        }

    def reset(self):
        """引擎不會跨日呼叫；供手動測試/重啟用"""
        self._donchian = _DonchianState(entry_n=self.entry_n, exit_k=self.exit_k)
        self._trades_today = 0
        self._cooldown_until_bar = -1
        self._session_bar = 0
        self._day = None
```

- [ ] **Step 4: Run** — `python -m pytest tests/test_donchian.py -v` → 24 PASS（17 prior + 7 exit）

- [ ] **Step 5: Commit from repo root**
  ```bash
  cd "C:/Users/xx/Desktop/vps永豐微台指" && \
  git add TMFtrader-src/strategy/donchian.py TMFtrader-src/tests/test_donchian.py && \
  git commit -m "feat(donchian): P2c check_exit + Donchian K-bar trailing"
  ```

---

## Task 5（P2d）：引擎冒煙（雙變體真實 MXF）

**Files:** Append to `tests/test_donchian.py`.

> Trend 訊號比 MR 罕；零交易 SKIP 可接受。**WR 不是判定 PASS/FAIL 標準**——只看「有交易」+「頻率合理」。

- [ ] **Step 1: Write 測試**

```python
def test_engine_smoke_long_only():
    """P2d: DonchianStrategy(allow_short=False) 真實 MXF 5m 短段"""
    import pandas as pd
    from pathlib import Path
    p = Path("data/vwap_fade/MXF_day_5m.parquet")
    if not p.exists():
        import pytest; pytest.skip("data 未準備")
    from core.gpu_indicators import precompute_all
    from backtest.fast_engine import FastBacktestEngine
    from strategy.donchian import DonchianStrategy
    df = pd.read_parquet(p).head(3000).reset_index(drop=True)
    ind = precompute_all(df, verbose=False)
    res = FastBacktestEngine(initial_balance=200_000, instrument="TMF").run(
        df, ind, DonchianStrategy(allow_short=False), "balanced")
    days = df["datetime"].dt.date.nunique()
    if len(res.trades) == 0:
        import pytest; pytest.skip(f"Donchian 在 {days} 天上零訊號（trend 罕見、預設 entry_n=20 嚴）")
    avg = len(res.trades) / max(days, 1)
    assert avg < 10, f"過度交易 avg={avg:.2f}/day"
    sides = {t["side"] for t in res.trades}
    # Side enum value = lowercase string
    assert sides.issubset({"long"}), f"long-only 不該有 short，但出現 {sides}"


def test_engine_smoke_long_short():
    import pandas as pd
    from pathlib import Path
    p = Path("data/vwap_fade/MXF_day_5m.parquet")
    if not p.exists():
        import pytest; pytest.skip("data 未準備")
    from core.gpu_indicators import precompute_all
    from backtest.fast_engine import FastBacktestEngine
    from strategy.donchian import DonchianStrategy
    df = pd.read_parquet(p).head(3000).reset_index(drop=True)
    ind = precompute_all(df, verbose=False)
    res = FastBacktestEngine(initial_balance=200_000, instrument="TMF").run(
        df, ind, DonchianStrategy(allow_short=True), "balanced")
    days = df["datetime"].dt.date.nunique()
    if len(res.trades) == 0:
        import pytest; pytest.skip(f"Donchian 雙向在 {days} 天上零訊號")
    avg = len(res.trades) / max(days, 1)
    assert avg < 10
    sides = {t["side"] for t in res.trades}
    assert "long" in sides or "short" in sides
```

- [ ] **Step 2: Run** — `python -m pytest tests/test_donchian.py::test_engine_smoke_long_only tests/test_donchian.py::test_engine_smoke_long_short -v`

- [ ] **Step 3: 記錄觀察**（不入測試斷言）：每變體的 trade count、avg/day、side 分佈、WR、net PnL、sample 3 trades。trend 預期 WR 可能 30-50%、別當 bug。

- [ ] **Step 4: Commit from repo root**
  ```bash
  cd "C:/Users/xx/Desktop/vps永豐微台指" && \
  git add TMFtrader-src/tests/test_donchian.py && \
  git commit -m "test(donchian): P2d engine smoke for both variants on real MXF data"
  ```

**Gate P2：** 兩變體都能跑（PASS 或合理 SKIP）+ 頻率 < 10/日。

---

## Task 6（P3）：粗篩 Grid（108 combos × 2 變體 × 2 商品 = 4 runs）

**Files:** Create `scripts/optimize_donchian.py`；append a tiny test to `tests/test_donchian.py`.

照抄 `scripts/optimize_or_fade.py`，改 4 處 + 加 sample-size penalty：

- [ ] **Step 1: 複製 + 改策略 import**
  ```python
  from strategy.donchian import DonchianStrategy
  def _make_strategy(params: dict, allow_short: bool):
      return DonchianStrategy(allow_short=allow_short, **params)
  ```
  worker 內 `_worker(args)` 解包 `(params, allow_short, shm_meta)`、instantiate `DonchianStrategy(allow_short=allow_short, **params)`。

- [ ] **Step 2: 改 PARAM_GRID**
  ```python
  PARAM_GRID = {
      "entry_n":  [10, 20, 30],
      "exit_k":   [5, 10, 15],
      "sl_atr":   [1.5, 2.0, 2.5],
      "max_bars": [24, 48],
      "cooldown": [3, 5],
  }
  # 3·3·3·2·2 = 108 combos / variant
  ```
  `_build_param_combos(allow_short)` 不需要 prune 任何 dim（與 or_fade 相同）。

- [ ] **Step 3: 改輸出路徑**
  - `data/donchian/grid_donchian_{symbol}_{variant}_{ts}.csv`
  - `data/donchian/best_params_donchian_{symbol}_{variant}_{ts}.json`
  - JSON 含 `allow_short` flag

- [ ] **Step 4: CLI 同 or_fade**（`--symbol`, `--allow-short`, `--dry-run`）

- [ ] **Step 5: `_run_one` helper 加 sample-size penalty**（重點新增）

```python
def _run_one(params: dict, df, split_idx: int, allow_short: bool = False) -> dict:
    from core.gpu_indicators import precompute_all
    from backtest.fast_engine import FastBacktestEngine
    from strategy.donchian import DonchianStrategy
    from scripts.optimize_strategy import _calc_metrics
    ind = precompute_all(df, verbose=False)
    eng = FastBacktestEngine(initial_balance=200_000, instrument="TMF")

    strat_t = DonchianStrategy(allow_short=allow_short, **params)
    r_train = eng.run(df, ind, strat_t, "balanced", start_idx=0, end_idx=split_idx)
    m_train = _calc_metrics(r_train)

    if split_idx < len(df) - 10:
        strat_v = DonchianStrategy(allow_short=allow_short, **params)
        r_test = eng.run(df, ind, strat_v, "balanced", start_idx=split_idx, end_idx=len(df))
        m_test = _calc_metrics(r_test)
    else:
        m_test = {"n": 0, "wr": 0.0, "pf": 0.0, "ret": 0.0, "dd": 0.0, "sharpe": 0.0}

    # Sample-size penalty: 過少樣本不可信
    if m_test["n"] < 5:
        wf = -99.0
    elif m_test["n"] < 100:
        wf = -50.0   # 重 penalty、實際上排不進 Top-10
    else:
        wf = (m_test["pf"] * 0.40
              + min(m_test["wr"] / 50.0, 2.0) * 0.25
              + max(m_test["sharpe"], -5.0) * 0.20
              + max(0.0, 1.0 - m_test["dd"] / 20.0) * 0.15)

    return {
        **{f"train_{k}": v for k, v in m_train.items()},
        **{f"test_{k}": v for k, v in m_test.items()},
        "wf_score": round(wf, 4), **params, "allow_short": allow_short,
    }
```

- [ ] **Step 6: 不移植 `_apply_best_params`**（同 or_fade）

- [ ] **Step 7: Unit test**

```python
def test_optimize_run_one_smoke():
    import pandas as pd
    from pathlib import Path
    p = Path("data/vwap_fade/MXF_day_5m.parquet")
    if not p.exists():
        import pytest; pytest.skip("data 未準備")
    from scripts.optimize_donchian import _run_one
    df = pd.read_parquet(p).head(2000).reset_index(drop=True)
    res = _run_one(
        {"entry_n": 20, "exit_k": 10, "sl_atr": 2.0, "max_bars": 48, "cooldown": 3},
        df, split_idx=1000, allow_short=False,
    )
    assert "wf_score" in res and "test_n" in res
```

- [ ] **Step 8: Dry-run 驗證 from `TMFtrader-src/`**
  ```bash
  cd "C:/Users/xx/Desktop/vps永豐微台指/TMFtrader-src" && \
  python scripts/optimize_donchian.py --symbol MXF --dry-run && \
  python scripts/optimize_donchian.py --symbol MXF --allow-short --dry-run
  ```
  確認兩條路徑都跑出 CSV + JSON 到 `data/donchian/`。

- [ ] **Step 9: 完整 file pytest** → 25 PASS（24 prior + 1 new）

- [ ] **Step 10: Commit from repo root**
  ```bash
  cd "C:/Users/xx/Desktop/vps永豐微台指" && \
  git add TMFtrader-src/scripts/optimize_donchian.py TMFtrader-src/tests/test_donchian.py && \
  git commit -m "feat(donchian): P3 grid optimizer (108 combos, dual variants, sample-size penalty)"
  ```

**注意：本任務不跑完整 4 grid run**（那是 Task 7、~30 分）。

---

## Task 7（P4+P5）：OOS orchestrator + Gate 匯總（含 BreakoutTrend 相關性必跑）

**Files:** Create `scripts/run_donchian_experiments.py`.

**完全沿用** `scripts/run_or_fade_experiments.py` 的實際 function 簽名與結構（**讀那支 script 為準**、不照本計畫 pseudocode 猜）。只改：
- 所有 `or_fade` 字串 → `donchian`
- import `DonchianStrategy`、`from scripts.optimize_donchian import _run_one`
- `valid_keys = {"entry_n","exit_k","sl_atr","max_bars","cooldown","max_trades","entry_window_end","force_close","point_value"}`
- `valid_perturb_keys = {"entry_n","exit_k","sl_atr","max_bars","cooldown"}`
- 輸出路徑 `data/donchian/`
- **step5 相關性必須跑成、不再 best-effort**

### Step 5 相關性實作（核心修正、不可漏）

```python
def step5_breakout_correlation(trades: list, df_oos) -> float:
    """強制執行、不再 try/except 吞錯。類名是 BreakoutTrendStrategy（複製錯就 fail 顯眼）。"""
    from core.gpu_indicators import precompute_all
    from backtest.fast_engine import FastBacktestEngine
    from strategy.breakout import BreakoutTrendStrategy   # ← 名稱要對
    import pandas as pd
    import numpy as np

    ind = precompute_all(df_oos, verbose=False)
    res_b = FastBacktestEngine(initial_balance=200_000, instrument="TMF").run(
        df_oos, ind, BreakoutTrendStrategy(), "balanced")

    # 兩者各自的 daily PnL → 對齊算 Pearson r
    c_dp = {}
    for t in trades:
        d = pd.to_datetime(t["exit_time"]).date().isoformat()
        c_dp[d] = c_dp.get(d, 0) + t["pnl"]

    days = sorted(set(res_b.daily_pnl) | set(c_dp.keys()))
    b_series = np.array([res_b.daily_pnl.get(d, 0) for d in days], dtype=float)
    c_series = np.array([c_dp.get(d, 0) for d in days], dtype=float)
    if len(b_series) < 5 or b_series.std() < 1e-9 or c_series.std() < 1e-9:
        return float("nan")
    return float(np.corrcoef(b_series, c_series)[0, 1])
```

> 不再 try/except 包整段——若 BreakoutTrendStrategy import 或 backtest 出錯，**讓它顯眼地 fail**，立刻知道要修。

### Gate 表（4-3 對調 + |r| 必跑）

| Gate | 門檻 |
|---|---|
| P0 成本 | net_pnl 扣 (18 + dyn_tax) × 2 per 口 |
| P3 雙商品 | 各變體 MXF/TXF Top-10 每維重疊（`test_n ≥ 100` 才參與 ranking） |
| 4-1 MC | PF p5 > 1.0、淨利 p5 > 0、MDD p95 < 12% |
| 4-2 擾動 | worst-dim stability < 0.3 |
| **4-3 Regime（對調）** | **趨勢日 (ADX>25) 淨利 > 0**（主場！）、震盪日淨利 > -50%×\|趨勢日\| |
| P5 OOS | PF > 1.2、頻率 0.5-3 筆/日（寬於 MR）、**\|r\| BreakoutTrend < 0.3 (必跑)** |

### Step 6 report 改動
在 markdown 的「觀察與建議」段、必須明示 |r| 結果與 FAIL 分類：

```python
# step6_write_report 內：
if abs(r) >= 0.6:
    typeb = "**Type B FAIL: |r| ≥ 0.6 → 換包裝、改 C2 放大 breakout 倉位**"
elif abs(r) >= 0.3:
    typeb = "intermediate: |r| 0.3-0.6、看組合 Sharpe 改善決定"
else:
    typeb = "differentiation gate PASS"
```

- [ ] **Step 1: Write orchestrator** — 用絕對路徑
  `C:\Users\xx\Desktop\vps永豐微台指\TMFtrader-src\scripts\run_donchian_experiments.py`

  讀 `scripts/run_or_fade_experiments.py` 確認實際簽名後逐段改。本地 perturb 實作改成 `from scripts.optimize_donchian import _run_one as _run_one_d`。

- [ ] **Step 2: Run**
  ```bash
  cd "C:/Users/xx/Desktop/vps永豐微台指/TMFtrader-src" && \
  python scripts/run_donchian_experiments.py
  ```
  預估 30-40 分。報告寫到 `data/donchian/robustness_report_<ts>.md`。

- [ ] **Step 3: 人工判讀**，分流：
  - Type A FAIL（PF<1.2 + 4-3 趨勢日虧 + MC p5<1）→ 試 C1.b momentum
  - Type B FAIL（|r| ≥ 0.6）→ 改 C2 放大 breakout 倉位
  - PASS（PF>1.2 + |r|<0.3 + 4-3 趨勢日正）→ paper 驗證

- [ ] **Step 4: Commit from repo root**
  ```bash
  cd "C:/Users/xx/Desktop/vps永豐微台指" && \
  # .gitignore 預檢過：data/donchian/*.md 不在 ignore（or_fade report 順利進 commit 42abc23 同 path pattern），無需 -f
  # CSV/JSON 也不在 ignore、但靠明示 git add 特定檔避免誤入（**勿用 git add -A**）
  git add TMFtrader-src/scripts/run_donchian_experiments.py \
          TMFtrader-src/data/donchian/robustness_report_*.md && \
  git commit -m "feat(donchian): P4+P5 OOS experiments + gate summary report"
  ```

---

## 開發紀律提醒

引擎事實全部繼承 `2026-05-28-vwap-fade-backtest-plan.md` §1 + `2026-05-28-or-fade-backtest-plan.md` 「重大差異」。本計畫新增的紀律：

1. **`on_kbar` 鐵律順序**：`_maybe_daily_reset` → `_donchian.update(...)` → `_session_bar += 1` → 守衛 → 進場。順序錯了 = lookahead 防線失效。
2. **`entry_n_high/low` 不含當前 bar；`exit_k_high/low` 含當前 bar**——TDD 釘死。
3. **`reset` `_donchian` 跨日**（與 `_RsiState` 連續相反、與 `_SessionVwap` / `_OrSession` 一致）。
4. **WR 不評**——trend WR 30-50% 是常態，Gate 看 PF/Sharpe/MDD。
5. **進場條件用 `kbar.close`**（不是 high/low）。
6. **進場 stop 天然 protective-side**（ATR-only，同前 3 支）。
7. **`_session_bar` 只在 `on_kbar` 增量**（check_exit 不動）。
8. **per-run `allow_short`**——不進 grid（同 or_fade）。
9. **Sample-size penalty**：wf_score 對 `test_n < 100` 重 penalty `-50`（避免 N=30 樣本 < 100 偶遇好運）。
10. **BreakoutTrendStrategy（不是 BreakoutStrategy）**——orchestrator step5 import 要對；前兩支 silently deferred 是因為這個名稱錯誤。
11. **4-3 Regime 判讀對調**：trend 「主場」= 趨勢日；`split_by_regime` 函式不改、Gate 評時方向反轉。
12. **Type B FAIL**：|r| ≥ 0.6 = 換包裝、不部署、轉 C2 放大 breakout 倉位。
13. **絕對路徑紀律**：所有 git / Write 操作從 repo root 用絕對路徑；之前撞過 unicode `永豐微台指` 路徑 + cwd subdir + 相對路徑 → INDEX 雙條目 bug。

## 未來工作（不在範圍）
- 上線掛載（與 BreakoutTrend / ORB 用 `position_lock` 互斥）：Gate 過 + paper 驗證後再開計畫
- v2 grid：把 `max_trades` / `entry_window_end` 列入 grid
- ML meta-labeling：純規則過 Gate 後才考慮
- **若 Type A FAIL → C1.b momentum + filter；若 Type B FAIL → C2 加深 breakout 既有 edge（v1 不在範圍）**
