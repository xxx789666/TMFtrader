# vwap_fade 回測 + 優化 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把日內 VWAP 帶狀均值回歸策略 `vwap_fade` 從設計變成「可回測、過 Stage 7 穩健性 Gate、可上 paper」的實作，全程沿用現有 ultra-trader-src 引擎（回測=live 同碼）。

**Architecture:** 新增 `strategy/vwap_fade.py`（實作 `BaseStrategy`），用現有 `FastBacktestEngine` + `precompute_all` 跑回測；粗篩抄 `scripts/optimize_strategy.py` 的 GPU precompute + 多核並行骨架；穩健性三件套（Monte Carlo / 參數擾動 / Regime 切分）接同一套 harness。不改 LEAN、不分叉引擎、不動現有 breakout/orb。

**Tech Stack:** Python 3.12、pandas/numpy、pyarrow（parquet）、現有 `core/gpu_indicators`（Numba JIT/CUDA）、`ProcessPoolExecutor` + `shared_memory`、pytest。

**背景設計文件：** `ultra-trader-src/docs/strategies/2026-05-27-vwap-fade-design.md`

---

## 已驗證的引擎事實（實作時依此，勿憑設計文件臆測）

| 事實 | 出處 | 對實作的影響 |
|---|---|---|
| `FastBacktestEngine.run(df, indicators, strategy, risk_profile, start_idx, end_idx)` → `BacktestResult` | `backtest/fast_engine.py:54` | `start_idx/end_idx` 是切 train/test fold 的入口 |
| 出場 100% 由 `strategy.check_exit(pos, snapshot)` 驅動；引擎不會自動比對 `position.stop_loss` | `backtest/fast_engine.py:157` | SL/TP 邏輯全寫在 `check_exit` |
| 持倉期間引擎**只呼叫 `check_exit`**，`on_kbar` 有 `if pos.is_flat` 守衛 | `backtest/fast_engine.py:180` | VWAP 不能只在 `on_kbar` 更新；`self._current_bar_time` 會 stale |
| `snapshot.timestamp` = 引擎傳入的真實 bar 時間；`snapshot.adx/atr/price/volume/rsi/bb_*` 都有 | `core/gpu_indicators.py:670`、`670-702` | `check_exit` 讀 **`snapshot.timestamp`**（不是 `self._current_bar_time`）；regime 用 `snapshot.adx` |
| `snapshot` 沒有 high/low/open（只有 close=price） | `core/gpu_indicators.py:669` | `check_exit` 算 typical 只能用 close 近似 |
| 引擎跨日**不呼叫 `strategy.reset()`**（只重置 risk_manager） | `backtest/fast_engine.py:114-125` | 換日重置必須在 `on_kbar` 內自己用 bar 日期判斷 |
| 成本已進 `net_pnl`：`commission = (commission_rate + tax_rate) * 2 * qty`，`net_pnl = pnl - commission` | `core/position.py:235,94` | 手續費+稅已含；滑價另在引擎價格層（`fast_engine:161,193`） |
| TMF spec：point_value=10、commission=18、tax=7（皆單邊） | `core/instrument_config.py:26` | 來回成本=(18+7)×2=50 TWD/口=5 點 |
| `_calc_metrics(result)` 已算 PF/WR/ret/DD/Sharpe | `scripts/optimize_strategy.py:86` | 直接 import 重用，勿重寫 |
| 並行骨架：precompute_all → shared_memory → ProcessPoolExecutor，worker 內建策略 | `scripts/optimize_strategy.py:124-216` | P3 grid 照抄，換策略工廠 + PARAM_GRID |
| `MeanReversionStrategy` 是最接近的範本（BB+RSI，SL/TP 存 position、time-stop 用 `bars_since_entry`） | `strategy/mean_reversion.py` | 抄它的 check_exit 結構 |

### 核心設計決策（實作時遵守）

1. **VWAP 雙路徑更新**：`on_kbar`（flat bar）用精確 typical `(H+L+C)/3 × volume`；`check_exit`（held bar）用 `close × volume` 近似，確保**成交量累計不漏**。副作用：持倉那幾根的 typical 用 close 近似（max_bars≈24 根內，誤差極小，需在 Task 11 驗證）。
2. **停利用「移動」VWAP、停損「凍結」於進場**：因 VWAP 在 `check_exit` 持續更新，停利目標 = 當下 VWAP（回歸概念更真）；停損 = 進場時算好的 `min(k2·σ 帶, ATR 停損)`（凍結→風險已知，且供 PositionSizer 算口數）。
3. **MXF/TXF 當 TMF 代理**：回測餵 MXF/TXF 的**指數價格序列**（同一標的、史料更長），但引擎一律 `instrument="TMF"`，讓乘數/成本=TMF。**嚴禁**用 MXF 乘數跑（會把 PnL 與成本比例算錯）。
4. **不自動寫回策略檔**：`optimize_strategy.py` 的 `_apply_best_params()`（regex 改 signals.py）**不要移植**；vwap_fade 參數是建構子引數，最佳參數只輸出 CSV + JSON，手動帶入。

---

## 檔案結構

| 檔案 | 職責 | 動作 |
|---|---|---|
| `strategy/vwap_fade.py` | 策略本體（VWAP 引擎 + on_kbar + check_exit） | Create |
| `tests/test_vwap_fade.py` | 策略單元測試 | Create |
| `scripts/prepare_vwap_data.py` | MXF/TXF 1m → 日盤 5m parquet + TMF OOS | Create |
| `scripts/optimize_vwap_fade.py` | 粗篩 grid（抄 optimize_strategy.py） | Create |
| `scripts/robustness_vwap_fade.py` | Stage 7 三件套 + Gate 匯總 | Create |
| `tests/test_robustness_vwap.py` | MC/stability/regime 純函式測試 | Create |
| `docs/strategies/2026-05-28-vwap-fade-backtest-plan.md` | 本計畫 | （已存在） |

不改任何 core/ 或既有 strategy/ 檔案（零核心風險）。

---

## Task 1（P0）：成本會計驗證

**Files:**
- Test: `tests/test_vwap_fade.py`（新增 `test_cost_accounting`）

- [ ] **Step 1: 寫失敗測試** — 斷言來回成本與點數換算

```python
# tests/test_vwap_fade.py
from core.position import PositionManager, Side
from core.instrument_config import INSTRUMENT_SPECS

def test_cost_accounting():
    spec = INSTRUMENT_SPECS["TMF"]
    pm = PositionManager(instruments=["TMF"], configs={"TMF": spec}, initial_balance=200_000)
    # 進場 22000，平倉 22010（+10 點），1 口
    pm.open_position("TMF", Side.LONG, price=22000.0, quantity=1,
                     stop_loss=21950, take_profit=22050, timestamp=None)
    trade = pm.close_position("TMF", 22010.0, "test", None)
    # 毛利 = 10 點 × 10 元 × 1 口 = 100
    assert trade.pnl == 100.0
    # 來回成本 = (18 手續費 + 7 稅) × 2 邊 × 1 口 = 50
    assert trade.commission == 50.0
    # 淨利 = 100 - 50 = 50（成本確實有扣）
    assert trade.net_pnl == 50.0
```

- [ ] **Step 2: 跑測試確認通過或暴露問題**

Run: `cd ultra-trader-src; python -m pytest tests/test_vwap_fade.py::test_cost_accounting -v`
Expected: PASS（若 FAIL → 成本模型與假設不符，先釐清再往下，這正是 Gate 目的）

- [ ] **Step 3: 在本計畫頂部「核心設計決策」確認成本註記**（已寫，無需改碼）：來回 50 TWD/口 = 5 點；加滑價（引擎預設 1 點/邊）≈ 來回 7 點。tax=7 為固定值，對 TMF（實際 期交稅率 0.00002×指數×10 ≈ 4.4/邊 @22000）偏保守，可接受。

- [ ] **Step 4: Commit**

```bash
git add tests/test_vwap_fade.py
git commit -m "test(vwap_fade): P0 verify cost accounting in net_pnl"
```

**Gate P0：** `test_cost_accounting` PASS 才往下。

---

## Task 2（P1）：資料準備

**Files:**
- Create: `scripts/prepare_vwap_data.py`
- Test: `tests/test_vwap_fade.py`（新增 `test_session_resample`）

需求：MXF、TXF 1m（2020-03~2026-05）→ 濾日盤 08:45–13:45 → 聚合 5m → parquet；TMF 2024-07+ 另存 OOS。重用既有 `scripts/fetch_history_kbars.py` 取得 1m（史料已落地 `data/history/`，見 memory）。

- [ ] **Step 1: 寫失敗測試** — 純函式 `resample_day_session(df_1m)` 的正確性（用合成資料）

```python
def test_session_resample():
    import pandas as pd
    from scripts.prepare_vwap_data import resample_day_session
    # 造 1 天 1m 資料：08:40~13:50，含盤前/盤後雜訊
    idx = pd.date_range("2024-01-02 08:40", "2024-01-02 13:50", freq="1min")
    df = pd.DataFrame({
        "datetime": idx,
        "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 1,
    })
    out = resample_day_session(df, freq="5min")
    # 只保留 08:45~13:45；首根 = 08:45
    assert out["datetime"].dt.time.min().strftime("%H:%M") == "08:45"
    assert out["datetime"].dt.time.max().strftime("%H:%M") <= "13:45"
    # OHLC 聚合正確：每根 high=2, low=0.5, volume=5（5 根 1m 合一）
    assert (out["high"] == 2.0).all() and (out["low"] == 0.5).all()
    assert out["volume"].iloc[0] == 5
```

- [ ] **Step 2: 跑測試確認失敗** — Run: `python -m pytest tests/test_vwap_fade.py::test_session_resample -v`，Expected: FAIL（`resample_day_session` not defined）

- [ ] **Step 3: 實作 `prepare_vwap_data.py`**

```python
"""P1：MXF/TXF 1m -> 日盤 08:45-13:45 -> 5m parquet；TMF 2024-07+ 另存 OOS。
用法：python scripts/prepare_vwap_data.py
"""
import sys; sys.path.insert(0, ".")
from pathlib import Path
import pandas as pd

SESSION_START = "08:45"
SESSION_END   = "13:45"
ROOT = Path(__file__).parent.parent
OUT  = ROOT / "data" / "vwap_fade"; OUT.mkdir(parents=True, exist_ok=True)

def resample_day_session(df_1m: pd.DataFrame, freq: str = "5min") -> pd.DataFrame:
    df = df_1m.copy()
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.set_index("datetime").sort_index()
    df = df.between_time(SESSION_START, SESSION_END, inclusive="left")  # 08:45<=t<13:45
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    # 以日為界 resample，避免跨日邊界洩漏
    parts = []
    for _, day in df.groupby(df.index.date):
        r = day.resample(freq, label="left", closed="left").agg(agg).dropna(subset=["open"])
        parts.append(r)
    out = pd.concat(parts).reset_index()
    return out

def build(symbol: str, raw_1m_path: Path):
    df1 = pd.read_parquet(raw_1m_path)         # columns: datetime, open, high, low, close, volume
    df5 = resample_day_session(df1)
    # 品質 Gate
    completeness = 1 - df5[["open","high","low","close"]].isna().any(axis=1).mean()
    vol_zero_ratio = (df5["volume"] <= 0).mean()
    df5.to_parquet(OUT / f"{symbol}_day_5m.parquet", index=False)
    print(f"{symbol}: {len(df5)} bars  completeness={completeness:.3%}  zero-vol={vol_zero_ratio:.3%}")
    return completeness, vol_zero_ratio

if __name__ == "__main__":
    # raw 1m 由 fetch_history_kbars.py 產出，路徑依實際落地調整
    for sym in ["MXF", "TXF"]:
        build(sym, ROOT / "data" / "history" / f"{sym}_1m.parquet")
    # TMF OOS：2024-07 之後
    tmf = pd.read_parquet(ROOT / "data" / "history" / "TMF_1m.parquet")
    tmf["datetime"] = pd.to_datetime(tmf["datetime"])
    tmf_oos = tmf[tmf["datetime"] >= "2024-07-01"]
    resample_day_session(tmf_oos).to_parquet(OUT / "TMF_oos_day_5m.parquet", index=False)
    print("TMF OOS saved")
```

- [ ] **Step 4: 跑測試確認通過** — Run: `python -m pytest tests/test_vwap_fade.py::test_session_resample -v`，Expected: PASS

- [ ] **Step 5: 跑真實資料準備並檢查 Gate** — Run: `python scripts/prepare_vwap_data.py`
  - **Gate P1：** completeness > 99%；列印 zero-vol 比例 → **若某商品 5m 量資料 zero-vol > 5%，VWAP 改用 anchored TWAP（typical 不加權）**，並在 Task 3 開 `use_volume=False` 分支。

- [ ] **Step 6: Commit**

```bash
git add scripts/prepare_vwap_data.py tests/test_vwap_fade.py
git commit -m "feat(vwap_fade): P1 day-session 5m data prep + quality gate"
```

---

## Task 3（P2a）：VWAP/σ session 引擎（純邏輯，先 TDD）

**Files:**
- Create: `strategy/vwap_fade.py`（先只放 `_SessionVwap` helper）
- Test: `tests/test_vwap_fade.py`

- [ ] **Step 1: 寫失敗測試**

```python
def test_session_vwap_cumulative_and_reset():
    from strategy.vwap_fade import _SessionVwap
    sv = _SessionVwap(sigma_window=20, use_volume=True)
    from datetime import datetime
    # 第一天兩根
    sv.update(datetime(2024,1,2,9,0), high=10, low=8, close=9, volume=2)   # typical=9
    sv.update(datetime(2024,1,2,9,5), high=12, low=10, close=11, volume=2) # typical=11
    # VWAP = (9*2 + 11*2)/(2+2) = 10
    assert abs(sv.vwap - 10.0) < 1e-9
    # 換日 → 自動 reset
    sv.update(datetime(2024,1,3,9,0), high=20, low=20, close=20, volume=1)
    assert abs(sv.vwap - 20.0) < 1e-9   # 新一天只剩這根

def test_session_vwap_close_proxy_update():
    from strategy.vwap_fade import _SessionVwap
    from datetime import datetime
    sv = _SessionVwap(sigma_window=20, use_volume=True)
    sv.update(datetime(2024,1,2,9,0), high=10, low=8, close=9, volume=2)
    # 持倉期間用 close-proxy（無 high/low）
    sv.update_close_proxy(datetime(2024,1,2,9,5), close=11, volume=2)
    # VWAP = (9*2 + 11*2)/4 = 10（成交量沒漏）
    assert abs(sv.vwap - 10.0) < 1e-9
```

- [ ] **Step 2: 跑測試確認失敗**（module not found）

- [ ] **Step 3: 實作 `_SessionVwap`**

```python
# strategy/vwap_fade.py（檔首）
from collections import deque
from datetime import datetime, time
from typing import Optional
import math

from strategy.base import BaseStrategy, Signal, SignalDirection
from core.market_data import KBar, MarketSnapshot
from core.position import Position, Side


class _SessionVwap:
    """每日 reset 的 session VWAP + 偏離 σ。換日由呼叫端傳入的日期自動偵測。"""
    def __init__(self, sigma_window: int = 20, use_volume: bool = True):
        self.sigma_window = sigma_window
        self.use_volume = use_volume
        self._date = None
        self._cum_pv = 0.0   # Σ(typical × v)
        self._cum_v = 0.0
        self._dev = deque(maxlen=sigma_window) if sigma_window > 0 else None
        self.session_bar = 0

    def _maybe_reset(self, dt: datetime):
        d = dt.date()
        if d != self._date:
            self._date = d
            self._cum_pv = 0.0; self._cum_v = 0.0
            if self._dev is not None: self._dev.clear()
            self.session_bar = 0

    def _accumulate(self, typical: float, volume: float, close: float):
        v = volume if (self.use_volume and volume > 0) else 1.0
        self._cum_pv += typical * v
        self._cum_v  += v
        self.session_bar += 1
        if self._dev is not None:
            self._dev.append(close - self.vwap)

    def update(self, dt: datetime, high: float, low: float, close: float, volume: float):
        """flat bar：精確 typical=(H+L+C)/3"""
        self._maybe_reset(dt)
        self._accumulate((high + low + close) / 3.0, volume, close)

    def update_close_proxy(self, dt: datetime, close: float, volume: float):
        """held bar：只有 close，用 close 當 typical（成交量不漏）"""
        self._maybe_reset(dt)
        self._accumulate(close, volume, close)

    @property
    def vwap(self) -> float:
        return self._cum_pv / self._cum_v if self._cum_v > 0 else 0.0

    @property
    def sigma(self) -> float:
        if self._dev is None or len(self._dev) < 2:
            return 0.0
        m = sum(self._dev) / len(self._dev)
        return math.sqrt(sum((x - m) ** 2 for x in self._dev) / (len(self._dev) - 1))
```

- [ ] **Step 4: 跑測試確認通過** — Run: `python -m pytest tests/test_vwap_fade.py -k session_vwap -v`，Expected: PASS

- [ ] **Step 5: Commit** — `git commit -m "feat(vwap_fade): P2a session VWAP/sigma engine"`

---

## Task 4（P2b）：進場邏輯 `on_kbar`

**Files:** Modify `strategy/vwap_fade.py`；Test `tests/test_vwap_fade.py`

- [ ] **Step 1: 寫失敗測試** — 用合成 kbar/snapshot 驗證：(a) 強趨勢日（adx>adx_max）不進場；(b) 偏離 < k·σ 不進場；(c) 偏離 ≥ k·σ 且 adx OK 在 entry_window 內做多/空；(d) warm-up 期不進場；(e) max_trades 上限

```python
def _make_snap(price, adx, atr, ts):
    from core.market_data import MarketSnapshot
    s = MarketSnapshot(); s.price=price; s.adx=adx; s.atr=atr; s.timestamp=ts; s.volume=100
    return s

def test_entry_blocked_by_adx():
    from strategy.vwap_fade import VwapFadeStrategy
    from core.market_data import KBar
    from datetime import datetime
    strat = VwapFadeStrategy(k=2.0, adx_max=30, min_warmup=1)
    # 先灌幾根建立 VWAP/σ（flat）
    for m in range(0, 30, 5):
        ts = datetime(2024,1,2,9,m)
        strat.on_kbar(KBar(ts, 100,101,99,100,100), _make_snap(100,20,5,ts))
    # 製造大偏離但 adx 超標 → 不進場
    ts = datetime(2024,1,2,10,0)
    sig = strat.on_kbar(KBar(ts, 80,80,80,80,100), _make_snap(80,45,5,ts))
    assert sig is None

def test_entry_long_on_deviation():
    from strategy.vwap_fade import VwapFadeStrategy
    from core.market_data import KBar
    from datetime import datetime
    strat = VwapFadeStrategy(k=2.0, adx_max=40, min_warmup=3, entry_window=("09:00","13:00"))
    prices = [100,101,99,100,102,98,101,99,100,101]
    for i, p in enumerate(prices):
        ts = datetime(2024,1,2,9,i*5)
        strat.on_kbar(KBar(ts, p,p+1,p-1,p,100), _make_snap(p,20,2,ts))
    ts = datetime(2024,1,2,10,0)
    sig = strat.on_kbar(KBar(ts, 90,90,90,90,100), _make_snap(90,20,2,ts))  # 遠低於 VWAP
    assert sig is not None and sig.direction == SignalDirection.BUY
    assert sig.stop_loss < 90  # 多單停損在下方
```

- [ ] **Step 2: 跑測試確認失敗**

- [ ] **Step 3: 實作 `VwapFadeStrategy.__init__` + `on_kbar`**

```python
class VwapFadeStrategy(BaseStrategy):
    def __init__(
        self,
        k: float = 2.0,                 # 進場帶 σ 倍數
        k2: float = 3.0,                # 停損帶 σ 倍數
        sigma_window: int = 20,         # σ 視窗（0=全日累計）
        adx_max: float = 35.0,          # 強趨勢過濾上限
        sl_atr: float = 2.0,            # ATR 停損倍數（與 k2 帶取緊者）
        max_bars: int = 24,             # 時間停損（5m×24≈2hr）
        max_trades: int = 6,            # 單日上限
        cooldown: int = 3,              # 停損後冷卻根數
        min_warmup: int = 3,            # σ 暖身根數
        entry_window: tuple = ("09:00", "13:00"),
        force_close: str = "13:25",
        use_volume: bool = True,
        point_value: float = 10.0,
    ):
        self.k=k; self.k2=k2; self.adx_max=adx_max; self.sl_atr=sl_atr
        self.max_bars=max_bars; self.max_trades=max_trades; self.cooldown=cooldown
        self.min_warmup=min_warmup; self.point_value=point_value
        self._ew_start = time.fromisoformat(entry_window[0])
        self._ew_end   = time.fromisoformat(entry_window[1])
        self._force_close = time.fromisoformat(force_close)
        self._vwap = _SessionVwap(sigma_window=sigma_window, use_volume=use_volume)
        self._trades_today = 0
        self._cooldown_until_bar = -1
        self._day = None
        self._last_adx = 0.0  # 供 regime 記錄

    @property
    def name(self) -> str:
        return "vwap_fade"

    def _maybe_daily_reset(self, dt: datetime):
        if dt.date() != self._day:
            self._day = dt.date()
            self._trades_today = 0
            self._cooldown_until_bar = -1

    def on_kbar(self, kbar: KBar, snapshot: MarketSnapshot) -> Optional[Signal]:
        ts = kbar.datetime
        self._maybe_daily_reset(ts)
        # VWAP 精確更新（flat bar 才會進到這）
        self._vwap.update(ts, kbar.high, kbar.low, kbar.close, kbar.volume)
        self._last_adx = snapshot.adx

        # 暖身 / entry_window / 過度交易 / 冷卻
        if self._vwap.session_bar < self.min_warmup:
            return None
        if not (self._ew_start <= ts.time() < self._ew_end):
            return None
        if self._trades_today >= self.max_trades:
            return None
        if self._vwap.session_bar <= self._cooldown_until_bar:
            return None
        # regime 過濾
        if snapshot.adx > self.adx_max:
            return None

        sigma = self._vwap.sigma
        if sigma <= 0:
            return None
        vwap = self._vwap.vwap
        close = kbar.close
        atr = max(snapshot.atr, 1.0)

        upper = vwap + self.k * sigma
        lower = vwap - self.k * sigma
        if close <= lower:
            # 做多：停損取 k2 帶 vs ATR 較緊（離進場近）者
            sl_band = vwap - self.k2 * sigma
            sl_atrp = close - self.sl_atr * atr
            stop = max(sl_band, sl_atrp)
            return self._signal(SignalDirection.BUY, close, stop, vwap, sigma)
        if close >= upper:
            sl_band = vwap + self.k2 * sigma
            sl_atrp = close + self.sl_atr * atr
            stop = min(sl_band, sl_atrp)
            return self._signal(SignalDirection.SELL, close, stop, vwap, sigma)
        return None

    def _signal(self, direction, price, stop, vwap, sigma) -> Signal:
        return Signal(
            direction=direction, strength=1.0,
            stop_loss=round(stop, 1), take_profit=round(vwap, 1),
            reason=f"vwap_fade {direction.value} dev={price-vwap:+.1f} sigma={sigma:.2f} adx={self._last_adx:.0f}",
            source=self.name,
        )
```

- [ ] **Step 4: 跑測試確認通過** — Run: `python -m pytest tests/test_vwap_fade.py -k entry -v`

- [ ] **Step 5: Commit** — `git commit -m "feat(vwap_fade): P2b entry logic on_kbar"`

---

## Task 5（P2c）：出場邏輯 `check_exit`

**Files:** Modify `strategy/vwap_fade.py`；Test `tests/test_vwap_fade.py`

關鍵：用 `snapshot.timestamp`（非 `self._current_bar_time`）；持倉期間用 `update_close_proxy` 維持 VWAP；停利=移動 VWAP，停損=凍結 `position.stop_loss`，時間停損=`position.bars_since_entry`，盤末=force_close。

- [ ] **Step 1: 寫失敗測試** — (a) 價回到 VWAP → 停利；(b) 價破凍結停損 → 停損；(c) 持倉超 max_bars → 時間停損；(d) 到 13:25 → 強平

```python
def _pos(side, entry, stop, tp, bars):
    # 用 PositionManager 建真實 Position（Position dataclass 沒有 instrument 欄位，
    # 不可 Position(instrument=...)；instrument 屬於 PositionManager/Trade）
    from core.position import PositionManager, Side
    from core.instrument_config import INSTRUMENT_SPECS
    pm = PositionManager(instruments=["TMF"], configs={"TMF": INSTRUMENT_SPECS["TMF"]},
                         initial_balance=200_000)
    pm.open_position("TMF", side, price=entry, quantity=1,
                     stop_loss=stop, take_profit=tp, timestamp=None)
    p = pm.positions["TMF"]
    p.bars_since_entry = bars   # 手動設持倉根數供時間停損測試
    return p

def test_exit_force_close_at_1325():
    from strategy.vwap_fade import VwapFadeStrategy
    from core.position import Side
    from datetime import datetime
    strat = VwapFadeStrategy()
    strat._vwap.update(datetime(2024,1,2,9,0), 100,100,100,100)  # 建一點 VWAP
    snap = _make_snap(100, 20, 5, datetime(2024,1,2,13,25))
    sig = strat.check_exit(_pos(Side.LONG, 95, 90, 101, 5), snap)
    assert sig is not None and sig.direction == SignalDirection.CLOSE and "盤末" in sig.reason
```

> 註：`Position` 的實際建構方式以 `core/position.py` 為準；測試若不便直接 new，改用 `PositionManager.open_position(...)` 取回 `pm.positions["TMF"]`。

- [ ] **Step 2: 跑測試確認失敗**

- [ ] **Step 3: 實作 `check_exit`**

```python
    def check_exit(self, position: "Position", snapshot: MarketSnapshot) -> Optional[Signal]:
        if position.is_flat:
            return None
        ts = snapshot.timestamp           # 真實 bar 時間（不可用 self._current_bar_time）
        price = snapshot.price
        # 持倉期間維持 VWAP（close-proxy，量不漏）
        self._vwap.update_close_proxy(ts, price, snapshot.volume)
        is_long = (position.side == Side.LONG)

        def close_sig(reason: str) -> Signal:
            return Signal(direction=SignalDirection.CLOSE, strength=1.0,
                          stop_loss=0, take_profit=0, reason=reason, source=self.name)

        # 1) 盤末強平
        if ts.time() >= self._force_close:
            return close_sig(f"盤末強平 @ {price:.0f}")
        # 2) 凍結停損
        if position.stop_loss > 0:
            if is_long and price <= position.stop_loss:
                return close_sig(f"停損 @ {price:.0f}")
            if (not is_long) and price >= position.stop_loss:
                return close_sig(f"停損 @ {price:.0f}")
        # 3) 移動 VWAP 停利
        vwap = self._vwap.vwap
        if vwap > 0:
            if is_long and price >= vwap:
                return close_sig(f"回到VWAP停利 @ {price:.0f}")
            if (not is_long) and price <= vwap:
                return close_sig(f"回到VWAP停利 @ {price:.0f}")
        # 4) 時間停損
        if position.bars_since_entry > self.max_bars:
            return close_sig(f"時間停損 {position.bars_since_entry}根")
        return None

    def get_parameters(self) -> dict:
        return {"k": self.k, "k2": self.k2, "adx_max": self.adx_max,
                "max_bars": self.max_bars, "max_trades": self.max_trades}

    def reset(self):
        self._vwap = _SessionVwap(sigma_window=self._vwap.sigma_window,
                                  use_volume=self._vwap.use_volume)
        self._trades_today = 0; self._cooldown_until_bar = -1; self._day = None
```

- [ ] **Step 4: 接線過度交易計數**（回 Task 4 的 `on_kbar`）— 在 `return self._signal(...)` 前加 `self._trades_today += 1`。

- [ ] **Step 5: 接線停損冷卻**（state-machine 唯一容易漏的點，必做）— 在 `check_exit` 回「停損」`close_sig` 之前，設 `self._cooldown_until_bar = self._vwap.session_bar + self.cooldown`。策略無法直接得知成交，故以「check_exit 判定停損」當代理觸發冷卻。

- [ ] **Step 6: 寫冷卻單元測試**（避免冷卻靜默失效——Task 6 冒煙測試的 `<10 筆/日` 上限抓不到壞掉的冷卻）

```python
def test_cooldown_blocks_reentry_after_stop():
    from strategy.vwap_fade import VwapFadeStrategy
    from core.position import Side
    from datetime import datetime
    strat = VwapFadeStrategy(cooldown=3, min_warmup=1, adx_max=99)
    # 建 VWAP/σ + 觸發一次停損
    for i in range(5):
        ts = datetime(2024,1,2,9,i*5)
        strat.on_kbar(_kbar(ts,100), _make_snap(100,20,2,ts))
    strat.check_exit(_pos(Side.LONG, 100, 105, 99, 2),
                     _make_snap(104, 20, 2, datetime(2024,1,2,9,30)))  # 多單破停損
    bar_at_stop = strat._vwap.session_bar
    assert strat._cooldown_until_bar == bar_at_stop + 3
```

- [ ] **Step 7: 跑測試確認通過** — Run: `python -m pytest tests/test_vwap_fade.py -k "exit or cooldown" -v`

- [ ] **Step 8: Commit** — `git commit -m "feat(vwap_fade): P2c exit logic + stop cooldown wiring"`

---

## Task 6（P2d）：引擎整合冒煙測試

**Files:** Test `tests/test_vwap_fade.py`

- [ ] **Step 1: 寫整合測試** — 用 P1 的一小段 MXF 5m 資料，跑 `FastBacktestEngine`，斷言「有產生交易」且頻率合理

```python
def test_engine_smoke_runs():
    import pandas as pd
    from pathlib import Path
    from core.gpu_indicators import precompute_all
    from backtest.fast_engine import FastBacktestEngine
    from strategy.vwap_fade import VwapFadeStrategy
    p = Path("data/vwap_fade/MXF_day_5m.parquet")
    if not p.exists():
        import pytest; pytest.skip("資料未準備，先跑 prepare_vwap_data.py")
    df = pd.read_parquet(p).head(3000)
    ind = precompute_all(df, verbose=False)
    res = FastBacktestEngine(initial_balance=200_000, instrument="TMF").run(
        df, ind, VwapFadeStrategy(), "balanced")
    assert len(res.trades) > 0          # 有進場（補進場機會是本策略目的）
    days = df["datetime"].dt.date.nunique()
    assert len(res.trades) / max(days,1) < 10   # 不過度交易（max_trades 把關生效）
```

- [ ] **Step 2: 跑測試** — Run: `python -m pytest tests/test_vwap_fade.py::test_engine_smoke_runs -v`
  - 若 0 交易：放寬 `k` 或檢查 σ 暖身；若爆量：檢查 `max_trades`/`cooldown` 接線。

- [ ] **Step 3: Commit** — `git commit -m "test(vwap_fade): P2d engine integration smoke"`

**Gate P2：** 單元 + 整合測試全綠；策略能在真實 5m 資料上產生「1–3 筆/日量級」的交易。

---

## Task 7（P3）：粗篩 Grid（抄 optimize_strategy.py）

**Files:** Create `scripts/optimize_vwap_fade.py`

照抄 `scripts/optimize_strategy.py` 的 precompute→shared_memory→ProcessPoolExecutor 骨架，改四處：

- [ ] **Step 1: 改策略工廠 + worker 引數解包** — `_worker` 內把 `_make_strategy(...)` 換成直接建構（vwap_fade 參數即建構子引數，無需 closure hack）。**注意第四個改動點**：參考檔的 `_worker` 解包固定三元組 `(sl, sig, trail, shm_meta)`、`args_list` 也是三元組，移植時要改成傳 `(params_dict, shm_meta)`、worker 內 `params, shm_meta = args` 再 `VwapFadeStrategy(**params)`。

```python
from strategy.vwap_fade import VwapFadeStrategy
def _make_strategy(params: dict):
    return VwapFadeStrategy(**params)

# _worker 簽名與 args_list 對應改成 dict-based：
#   args = (params_dict, shm_meta);  params, shm_meta = args
#   combos = [dict(zip(PARAM_GRID, v)) for v in itertools.product(*PARAM_GRID.values())]
```

- [ ] **Step 2: 改 PARAM_GRID + split**

```python
PARAM_GRID = {
    "k":            [1.5, 2.0, 2.5],
    "k2":           [2.5, 3.0, 3.5],
    "sigma_window": [20, 0],          # 0 = 全日累計
    "adx_max":      [25, 30, 35],
    "max_bars":     [18, 24, 36],
}
# split：train 2020~2023、test 2024+
SPLIT_DATE = "2024-01-01"
```

- [ ] **Step 3: 重用 `_calc_metrics`、移除 `_apply_best_params`** — `from scripts.optimize_strategy import _calc_metrics`；只輸出 `data/vwap_fade/grid_<SYMBOL>_<ts>.csv` 與 `best_params_<SYMBOL>.json`，**不寫回任何策略檔**。

- [ ] **Step 4: 接受 `--symbol` 參數，跑兩商品**
  - Run: `python scripts/optimize_vwap_fade.py --symbol MXF`
  - Run: `python scripts/optimize_vwap_fade.py --symbol TXF`
  - 引擎一律 `instrument="TMF"`（代理規則，見核心決策 3）

- [ ] **Step 5: 比對雙商品 Top 候選**
  - **Gate P3：** MXF 與 TXF 各自 Top-10（依 wf_score）的最佳參數**區間要重疊**（例如 k、adx_max 落在相同子區間）。不重疊 = 過擬合 → 縮參數、回頭檢視策略。

- [ ] **Step 6: Commit** — `git commit -m "feat(vwap_fade): P3 grid optimizer (parallel, dual-instrument)"`

---

## Task 8（P4-1）：Monte Carlo（搬 phase5 5-1）

**Files:** Create `scripts/robustness_vwap_fade.py`；Test `tests/test_robustness_vwap.py`

- [ ] **Step 1: 寫失敗測試** — `monte_carlo(pnl_array, n)` 回 `(pf_p5, net_p5, mdd_p95)`

```python
def test_monte_carlo_percentiles():
    import numpy as np
    from scripts.robustness_vwap_fade import monte_carlo
    rng = np.random.default_rng(0)
    pnl = rng.normal(50, 200, size=120)   # 正期望
    pf_p5, net_p5, mdd_p95 = monte_carlo(pnl, n=2000, seed=42)
    assert net_p5 == net_p5  # 非 NaN
    assert pf_p5 > 0 and mdd_p95 > 0
```

- [ ] **Step 2: 跑測試確認失敗**

- [ ] **Step 3: 實作 `monte_carlo`**（邏輯搬 `phase5_robustness.py:67-100`，泛化成吃 pnl 陣列；shuffle 交易順序重排）

```python
import numpy as np
def monte_carlo(pnl: np.ndarray, n: int = 5000, seed: int = 42, init_eq: float = 200_000):
    rng = np.random.default_rng(seed)
    pfs, nets, mdds = [], [], []
    for _ in range(n):
        s = rng.permutation(pnl)
        gp = s[s > 0].sum(); gl = abs(s[s < 0].sum())
        pfs.append(gp / gl if gl > 1e-6 else 999.0)
        nets.append(float(s.sum()))
        eq = np.concatenate([[init_eq], init_eq + np.cumsum(s)])
        pk = np.maximum.accumulate(eq)
        mdds.append(float(((pk - eq) / pk * 100).max()))
    return (float(np.percentile(pfs, 5)),
            float(np.percentile(nets, 5)),
            float(np.percentile(mdds, 95)))
```

- [ ] **Step 4: 跑測試確認通過**
- [ ] **Step 5: Commit** — `git commit -m "feat(vwap_fade): P4-1 Monte Carlo robustness"`

**Gate 4-1：** PF p5 > 1.0、淨利 p5 > 0、MDD p95 < 12%。

---

## Task 9（P4-2）：參數擾動

**Files:** Modify `scripts/robustness_vwap_fade.py`；Test `tests/test_robustness_vwap.py`

- [ ] **Step 1: 寫失敗測試** — `stability(returns)` = std/|mean|

```python
def test_stability_metric():
    from scripts.robustness_vwap_fade import stability
    assert stability([100,102,98,101,99]) < 0.3     # 穩
    assert stability([100,10,200,-50,300]) > 0.5     # 不穩
```

- [ ] **Step 2: 跑測試確認失敗**

- [ ] **Step 3: 實作 `stability` + `perturb_and_run`**
  - `stability(rets) = np.std(rets)/abs(np.mean(rets))`
  - `perturb_and_run`：對 best 參數每個**數值**維度 ×[0.9,0.95,1.0,1.05,1.1,1.2]，用 **Task 7 同一個並行 harness**（import `optimize_vwap_fade` 的 run-one helper，或直接呼叫 `FastBacktestEngine`）重跑 OOS，收集每組淨利報酬率 → `stability(...)`。**不是** phase5 的 MFE/MAE 回放。

- [ ] **Step 4: 跑測試確認通過**
- [ ] **Step 5: 跑真實擾動** — Run: `python scripts/robustness_vwap_fade.py --stage perturb`
- [ ] **Step 6: Commit** — `git commit -m "feat(vwap_fade): P4-2 parameter perturbation stability"`

**Gate 4-2：** stability < 0.3。

---

## Task 10（P4-3）：Regime 切分（MR 命門）

**Files:** Modify `scripts/robustness_vwap_fade.py`；Test `tests/test_robustness_vwap.py`

進場時的 ADX 不在 `BacktestResult.trades` 裡 → **用 trade 的 `entry_time` join 回 5m bar 的 `adx`**（precompute 出的 `indicators["adx"]`），無需改引擎。

- [ ] **Step 1: 寫失敗測試** — `split_by_regime(trades, bar_df_with_adx)` 回 `{"trend": pnl[], "range": pnl[]}`（trend: adx>25；range: adx<20）

```python
def test_split_by_regime():
    import pandas as pd
    from scripts.robustness_vwap_fade import split_by_regime
    bars = pd.DataFrame({"datetime": pd.to_datetime(
        ["2024-01-02 09:00","2024-01-02 09:05"]), "adx":[15, 30]})
    trades = [{"entry_time":"2024-01-02T09:00:00","pnl":100},
              {"entry_time":"2024-01-02T09:05:00","pnl":-50}]
    out = split_by_regime(trades, bars)
    assert out["range"] == [100] and out["trend"] == [-50]
```

- [ ] **Step 2: 跑測試確認失敗**

- [ ] **Step 3: 實作 `split_by_regime`**（`entry_time` 最近鄰對齊到 bar，取該 bar 的 adx 分桶；20–25 之間視為中性丟棄或歸 range，二擇一並註明）

- [ ] **Step 4: 跑測試確認通過**
- [ ] **Step 5: 在 OOS trades 上跑分桶並列印各 regime 的 net/PF**
- [ ] **Step 6: Commit** — `git commit -m "feat(vwap_fade): P4-3 regime split (trend vs range)"`

**Gate 4-3（MR 命門）：** 震盪日（adx<20）**明顯獲利**；趨勢日（adx>25）**不可災難性虧損**（淨虧不超過震盪日獲利的某比例，門檻在報告裡定）。
> 註：SOP Stage 7 的 Label Shuffle 是 ML-only（檢測資料洩漏），規則型策略無 label/model → **不適用，跳過**。

---

## Task 11（P5）：OOS 終驗 + Gate 匯總報告

**Files:** Modify `scripts/robustness_vwap_fade.py`（新增 `--stage all` 匯總）

- [ ] **Step 1: 在 TMF OOS（`TMF_oos_day_5m.parquet`, 2024-07+）跑 best 參數**，`instrument="TMF"`，產出 trades + 指標（重用 `_calc_metrics`）。

- [ ] **Step 2: 驗證 VWAP 近似誤差**（呼應核心決策 1）— 另算一版「精確 session VWAP」（用完整 OHLC 向量化，groupby 日 + cumsum），比對策略自維護版的進場點偏差；**Gate：進場訊號重疊率 > 95%**，否則升級為精確 VWAP 預算指標（見「未來工作」）。

- [ ] **Step 3: 與 breakout 相關性** — 取同期 breakout 日報酬序列與 vwap_fade 日報酬序列算 Pearson r；**Gate：|r| < 0.3**（低相關＝有分散效果）。

- [ ] **Step 4: 匯總所有 Gate 成一張表**（仿 `phase5_robustness.py` 結尾的總結列印），輸出 `data/vwap_fade/robustness_report_<ts>.md`：

| Gate | 門檻 | 結果 |
|---|---|---|
| P0 成本 | net_pnl 確實扣 50/口 | — |
| P3 雙商品 | MXF/TXF 參數區間重疊 | — |
| 4-1 MC | PF p5>1.0 / 淨利 p5>0 / MDD p95<12% | — |
| 4-2 擾動 | stability<0.3 | — |
| 4-3 Regime | 震盪日正 / 趨勢日不災難 | — |
| P5 OOS | PF>1.2 / 頻率 1–3 筆日 / 與 breakout \|r\|<0.3 | — |
| P5 VWAP 近似 | 進場重疊率>95% | — |

- [ ] **Step 5: Commit** — `git commit -m "feat(vwap_fade): P5 OOS validation + gate summary report"`

**Gate P5（上 paper 前最終）：** 上表全綠才接既有 paper 流程；任一未過 → 回對應 Task 修，不上 paper。

---

## Task 12（選配/Stretch）：Rolling Walk-Forward

> 既有 optimizer 是單刀切 train/test。規則型策略**無 retrain**，rolling WF 的意義 = 每個 fold 重選 best 參數、檢查「被選出的參數在 fold 間是否穩定」。

- [ ] 外層迴圈：以季為 fold，train=前 N 季、test=下 1 季，逐季 re-select best 參數
- [ ] 記錄各 fold 的 best 參數；**Gate：fold 間 best 參數不大跳動**（同 Task 9 的 stability 概念套在「被選參數」上）
- [ ] 若 v1 的單刀切 + 雙商品重疊已足夠擋過擬合，本任務可延後

---

## 未來工作（不在本計畫範圍）

- **精確 session VWAP 進 indicator 層**：把 session-anchored VWAP/σ 加進 `core/gpu_indicators.precompute_all`（回測）**與** `core/market_data.IndicatorEngine`（live），讓 `snapshot.vwap` 在兩條路徑一致 → 消除 close-proxy 近似、保持回測=live 同碼。僅在 Task 11 Step 2 的近似誤差 Gate 未過時才需要。
- **分批停利 scale_out**：設計文件 §3.3 選配，v1 先單一目標。
- **上線掛載**（日盤 pipeline / start.py + position_lock 與 breakout 互斥）：paper 驗過後另開實作計畫。
