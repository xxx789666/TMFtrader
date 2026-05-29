# Donchian Breakout 策略 設計文件（`donchian`）

- 日期：2026-05-28
- 狀態：設計（待回測驗證）
- 商品：微型臺指期貨 TMF（live）；回測用 MXF / TXF 長序列、TMF 2024-07+ 作 OOS
- 引擎：沿用 TMFtrader-src（`BaseStrategy` + `backtest/`）
- 前置：三支 MR 策略全 FAIL 後的第一支 trend-following 嘗試（C1）

---

## 1. 背景與目標

2026-05-28 一日內驗證：
- **三支獨立 MR alpha (VWAP/RSI(2)/OR-mid+vol) 全 FAIL** on TMF 2024-07~2026-05 OOS
- **既有 BreakoutTrendStrategy** 同份資料 PF **1.513**、WR 54.5%、Sharpe 2.474
- 結論：TMF 5m 日盤是 **trend-dominant 市場**、MR 不適用、trend 有 edge

**本策略 = 第二支獨立的 trend-following alpha**。BreakoutTrendStrategy 用 **ATR squeeze + EMA stack + ADX + RSI**（indicator-stacked）；本策略用 **純 N 根結構性 Donchian 突破**（零均線、零 ADX、零 squeeze、零 RSI）—— 概念最大化差異化，避免「同 edge 換包裝」。

**Stretch goal**：與既有 live BreakoutTrend 配對運作（若 |r| < 0.3），透過多 alpha 組合降低總組合 drawdown；若同 edge（|r| ≥ 0.6），不視為新策略、捨棄。

---

## 2. 範圍 / 非目標

| In Scope | Out of Scope |
|---|---|
| 日盤 08:45–13:45、5 分 K | 夜盤、跨日持倉 |
| Donchian 雙變體並排（long-only / long+short） | ML meta-labeling（先過 Gate） |
| 沿用 PositionSizer / risk_manager / position_lock | 修改 core / 既有策略 |
| 重用 pipeline（資料、grid、三件套） | EMA / ADX / squeeze（live BreakoutTrend 用過、差異化要求） |

---

## 3. 策略邏輯

### 3.1 Trend-following 統計特性提醒
與前 3 支 MR 截然不同：
- **WR 通常 30-50%**（losers 多但 winners 罕見且大）
- **PF > 1.2 靠 avg_win >> avg_loss**（trailing 讓贏單長、ATR stop 切短輸單）
- **評估 Gate 時不要拿 WR 判斷**——WR 低於 50% 不等於 FAIL；PF + Sharpe 才是真指標

### 3.2 `_DonchianState`（自維護 N+1 根滑動視窗）

```
class _DonchianState(entry_n, exit_k):
  _highs, _lows: deque(maxlen = max(entry_n, exit_k) + 1)
  _date

  update(dt, high, low):
    若換日 → 清空 deque (跨日不繼承、每日獨立 Donchian)
    append (high, low) 到 deque

  entry_n_high  →  max(_highs[-entry_n-1 : -1])   # **不含當前 bar**（避免 lookahead）
  entry_n_low   →  min(_lows [-entry_n-1 : -1])
  exit_k_high   →  max(_highs[-exit_k :])         # **含當前 bar**（trailing 即時反應）
  exit_k_low    →  min(_lows [-exit_k :])
  warmup_ready  →  len(deque) > entry_n
```

**含/不含當前 bar 的細節是 implementation 釘死的關鍵**：entry 用過去 N 根（不含當前 → 避免自我比較永遠 false breakout）；exit 用最近 K 根（含當前 → trailing 即時觸發）。TDD 必須覆蓋這個區別。

**`on_kbar` 的呼叫順序必須是**：`_donchian.update(...)` → 守衛（warmup/window/cap/cooldown） → 進場判定。`-entry_n-1:-1` slice 取最近 N 根「之前」的 bars，剛 append 的當前 bar 落在 `-1` 位置會被排除——這正是 lookahead-bias 防線。TDD 必有一條 case：「append 一根新高 → 立即查 entry_n_high 不應包含這根的 high」。

**`exit_k_high/low` partial-deque caveat**：若策略剛開市、deque 還沒滿 K 根、slice `_highs[-exit_k:]` 會回少於 K 根的結果。實務上不是問題——exit 只在持倉時呼叫、而持倉必先過 entry warmup（≥ entry_n+1 根 ≥ exit_k 一般情況），所以 exit_k 其實也已暖完。若 `exit_k > entry_n` 的極端 grid 組合可能不滿足，但 PARAM_GRID 設計上不會這樣（K < N 常識）。

### 3.3 進場（`on_kbar`，flat、entry_window 內、warmup 已過）

```
if warmup and in entry_window:
    if  kbar.close > _donchian.entry_n_high:                BUY,  stop = close - sl_atr * atr
    if  allow_short and kbar.close < _donchian.entry_n_low: SELL, stop = close + sl_atr * atr
```

- **無 regime filter**（學前 3 支教訓，4-3 Gate 全 testable）
- 進場條件用 `kbar.close`（不是 high/low）——避免 wick 觸發但 close 未過的假突破
- ATR stop 天然 protective-side（同 or_fade / connors_rsi2）
- `Signal.take_profit = 0`（出場由 check_exit 接管）
- `_trades_today += 1`、cooldown 守衛

### 3.4 出場（`check_exit` first-fires-wins；不 update state、不 increment session_bar）

1. **盤末強平**：`snapshot.timestamp.time() >= force_close` — **不設 cooldown**（當日已結束）
2. **ATR 凍結停損** + 設 `cooldown_until_bar = _session_bar + cooldown`
3. **Donchian K-bar trailing exit**（核心 trend exit）：
   - long: `snapshot.price < _donchian.exit_k_low`
   - short: `snapshot.price > _donchian.exit_k_high`
4. **時間停損**：`position.bars_since_entry > max_bars`

> `check_exit` 讀 `_donchian.exit_k_low/high` 是純讀，**不**呼叫 `update()`（state 已由 `on_kbar` 在當前 bar update 完）。

### 3.5 雙變體
建構子 `allow_short: bool`，per-run 固定（不進 grid）。跑 4 grids：`{MXF, TXF} × {allow_short=False, True}`，獨立 Gate 評分。

---

## 4. 參數與 grid

| 參數 | 預設 | grid 候選 | 說明 |
|---|---|---|---|
| `entry_n` | 20 | [10, 20, 30] | 進場 N 根高/低；N=30 = 半個 session |
| `exit_k` | 10 | [5, 10, 15] | 出場 K 根對向極值；K < N 常見 |
| `sl_atr` | 2.0 | [1.5, 2.0, 2.5] | ATR 硬停損倍數 |
| `max_bars` | 48 | [24, 48] | 時間停損；48 ≈ 4hr ≈「無時間停損」（force_close 接管） |
| `cooldown` | 3 | [3, 5] | 停損後冷卻根數 |
| `max_trades` | 5 | 固定（v1 不調） | trend 訊號比 MR 罕；5/日已足 |
| `entry_window` | (dyn_start, "12:00") | 固定 | start = 08:45 + entry_n × 5min |
| `force_close` | "13:25" | 固定 | 不留倉 |
| `allow_short` | per-run | False / True 各跑一輪 | 不進 grid（避免 cherry-pick 偏見） |

**grid 大小**：3·3·3·2·2 = **108 combos / variant**。兩商品 × 兩變體 = **4 runs**，預估 **20-30 分**。

`entry_n` 各值對應的 entry window 寬度（@entry_window_end="12:00"）：

| entry_n | start | 可進場分鐘 | 可進場 bars |
|---|---|---|---|
| 10 | 09:35 | 145 | 29 |
| 20 | 10:25 | 95 | 19 |
| 30 | 11:15 | 45 | 9 |

N=30 進場 bars 少、樣本可能不足，gate 評估時要注意。

**最小樣本門檻**：grid 結果若某 combo 的 `test_n < 100`（OOS 不到百筆），不參與 Top-10 排名（避免 N=30 在 9 bars/day 上偶遇好運過 gate）。實作在 wf_score 計算時加 sample-size penalty 或直接 filter。

---

## 5. 系統整合

- 新檔 `strategy/donchian.py`，實作 `BaseStrategy`
- 沿用 `risk.PositionSizer` + `risk_manager`
- 引擎 `FastBacktestEngine`、`instrument="TMF"`（MXF/TXF 代理同前）
- 與 BreakoutTrendStrategy / ORB / 三 MR artifacts 共存：`position_lock` 互斥
- **核心 0 改動**

---

## 6. 回測與驗證計畫（重用 pipeline）

| 階段 | 動作 | 重用 | 新增 |
|---|---|---|---|
| **P0** 成本 | 動態稅 net_pnl test | 同 or_fade Task 1 內容 | 新 test 檔 `tests/test_donchian.py` |
| **P1** 資料 | 已備 | `data/vwap_fade/*.parquet` | 無 |
| **P2** 策略 | `_DonchianState` + on_kbar + check_exit TDD | `or_fade` 結構模板 | Donchian 含/不含當前 bar 邊界 TDD |
| **P3** grid | `scripts/optimize_donchian.py` | 抄 `optimize_or_fade.py`（dict-based + --allow-short）| 改 PARAM_GRID + 策略 import |
| **P4-1/2/3 三件套** | OOS trades 處理 | `scripts.robustness_vwap_fade` 直接 import | 無 |
| **P5** OOS + Gate 報告 | TMF OOS + **與 BreakoutTrend 相關性 (核心 §9)** | 抄 `run_or_fade_experiments.py` 並修 BreakoutTrendStrategy 名稱 | step5 相關性計算改為強制執行（不再 best-effort） |

---

## 7. 成功標準（Gate）

| Gate | 門檻 |
|---|---|
| P0 成本 | `net_pnl` 扣 `(18 + dyn_tax) × 2` per 口 |
| P3 雙商品（per 變體） | MXF/TXF Top-10 每維參數區間重疊 |
| 4-1 MC | PF p5 > 1.0、淨利 p5 > 0、MDD p95 < 12% |
| 4-2 擾動 | worst-dim stability < 0.3 |
| 4-3 Regime | 趨勢日 (ADX>25) 淨利 > 0（**主場！與 MR 相反**）；震盪日 (ADX<20) 淨利 > -50% × \|趨勢日淨利\| |
| P5 OOS | PF > 1.2、頻率 0.5–3 筆/日（**trend 訊號稀疏**，比 MR 寬）、\|r\| BreakoutTrend < 0.3（**核心 differentiation gate**） |

> **4-3 主場判定相反**：MR 策略的「主場」是震盪日；trend 的「主場」是趨勢日。Gate 邏輯對稱反轉、`split_by_regime` 函式無需改、只是判讀標準對調。

---

## 8. 風險與緩解

| 風險 | 緩解 |
|---|---|
| Donchian 在低波時 N-bar 高頻 false breakout | grid 跨 N 範圍、選穩定區間 |
| 與 BreakoutTrend 相關性高（換包裝） | §9 |r| Gate 嚴格守住、不過就捨棄 |
| 5m 太短、N=10 雜訊多 | grid 含 N=20/30、穩健結果應主導 |
| WR 低被誤判為 FAIL | §3.1 明示 trend 統計特性；Gate 評 PF/Sharpe 不評 WR |

---

## 9. 失敗判定（明確退場條件）

兩種 FAIL：

**Type A：策略本身沒 edge**
- 雙變體都 FAIL（PF<1.2 + 4-3 趨勢日虧 + MC PF p5 < 1.0）
- → 結構性 Donchian 在 TMF 5m 沒 edge
- 下一步：試其他 trend 角度（C1.b momentum + filter 或 C1.c pullback to MA）

**Type B：與 live 換包裝**
- 自己 Gate 全過、PF 漂亮，但 **\|r\| ≥ 0.6 vs BreakoutTrend**
- → 不是新 edge、只是同一個 trend edge 換 trigger
- 下一步：放大 BreakoutTrend 倉位（C2 path）而非部署 Donchian

**Intermediate：0.3 ≤ \|r\| < 0.6**
- 中度相關、邊際 diversification
- 看 PF 大小 + 組合 Sharpe 是否真改善決定

---

## 10. 工作量預估

比 or_fade **略少**（無 wait_bars pending state、無 vol_ratio filter）：
- 策略本體 ~100 行
- 7 個 task（同前模式）
- 預估 **4-5 小時**到 Gate 評估完成

---

## 11. 開發紀律提醒

引擎事實全部繼承 `2026-05-28-vwap-fade-backtest-plan.md` §1 表 + `2026-05-28-or-fade-backtest-plan.md` 的「重大差異」章節。本策略新增的紀律：

1. **Donchian `entry_n_high` 不含當前 bar；`exit_k_low/high` 含當前 bar**——TDD 必須釘死這個區別（避免 entry lookahead bias、確保 exit 即時反應）。
2. **進場條件用 `kbar.close`**（不是 high/low）——避免 wick 突破但 close 沒過的假訊號。
3. **WR 低 (30-50%) 不等於 FAIL**——Gate 評估只看 PF/Sharpe/MDD，不評 WR。
4. **`_DonchianState` 跨日 reset**（與 `_RsiState` 連續相反、與 `_SessionVwap` / `_OrSession` 一致）。
5. **進場 stop 天然 protective-side**（ATR-only，同 or_fade / connors_rsi2）。
6. **`_session_bar` 只在 on_kbar 增量**（check_exit 不動），保持 cooldown 乾淨語意。
7. **P5 相關性 Gate 必須跑成**（不是 best-effort）——`BreakoutTrendStrategy` 是真實 class 名稱（不是 BreakoutStrategy）、修 orchestrator 的 step5 import。
8. **regime split 判讀對調**：trend-following 的「主場」是趨勢日，與 MR 相反。

---

## 後續步驟

1. 本設計經 spec 審查 + 你複核。
2. 進 `writing-plans` 產出實作計畫。
3. 依計畫實作 → grid → Gate → 與 BreakoutTrend 相關性判定。
4. **Type A FAIL → C1.b momentum；Type B → 改 C2 放大 breakout；Pass → paper 驗證**。
