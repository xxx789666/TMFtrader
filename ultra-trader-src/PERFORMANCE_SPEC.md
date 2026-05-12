# UltraTrader 績效記錄 & 發文接口規格

## 目標
1. 結構化記錄每日/每週/每月績效數據（JSON）
2. 留好 API 接口，讓 MindThread 自動抓績效生成 Threads 文案
3. 零人工介入：交易結算 → 績效存檔 → 自動推文

---

## 一、績效數據 Schema

### 1.1 單筆交易記錄（已有，補強）

在現有 `Trade` dataclass (`core/position.py`) 加入：

```python
@dataclass
class Trade:
    # === 既有欄位 ===
    trade_id: str
    side: str             # BUY / SELL
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float            # 含手續費+稅
    exit_reason: str
    bars_held: int

    # === 新增欄位 ===
    strategy: str          # "momentum" / "mean_reversion"
    market_regime: str     # "TRENDING_UP" / "RANGING" / etc.
    signal_strength: float # 進場信號強度 0-1
    max_favorable: float   # 最大有利點數（MFE）
    max_adverse: float     # 最大不利點數（MAE）
```

### 1.2 每日績效摘要

```python
@dataclass
class DailyPerformance:
    date: str                    # "2026-03-05"
    trading_mode: str            # "simulation" / "paper" / "live"

    # 帳戶
    starting_balance: float
    ending_balance: float
    daily_pnl: float             # 當日損益（扣除手續費+稅）
    daily_return_pct: float      # 當日報酬率 %

    # 交易統計
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float              # 勝率 %
    avg_win: float               # 平均獲利（元）
    avg_loss: float              # 平均虧損（元）
    profit_factor: float         # 盈虧比
    largest_win: float
    largest_loss: float

    # 風險
    max_drawdown: float          # 當日最大回撤（元）
    max_drawdown_pct: float      # 當日最大回撤 %

    # 策略
    primary_strategy: str
    market_regime_summary: dict  # {"TRENDING_UP": 3, "RANGING": 2}

    # 交易明細
    trades: list[Trade]          # 當日所有交易

    # meta
    contract: str                # "MXF"
    created_at: str              # ISO timestamp
```

### 1.3 週/月績效摘要

```python
@dataclass
class PeriodPerformance:
    period_type: str             # "weekly" / "monthly"
    period_label: str            # "2026-W10" / "2026-03"
    start_date: str
    end_date: str

    starting_balance: float
    ending_balance: float
    total_pnl: float
    total_return_pct: float

    trading_days: int
    total_trades: int
    win_rate: float
    profit_factor: float
    max_drawdown: float
    max_drawdown_pct: float
    best_day_pnl: float
    worst_day_pnl: float
    avg_daily_pnl: float

    # 連勝/連敗
    max_consecutive_wins: int
    max_consecutive_losses: int

    # 每日明細引用
    daily_results: list[str]     # ["2026-03-01", "2026-03-02", ...]
```

---

## 二、存儲格式

### 檔案結構

```
data/
  performance/
    daily/
      2026-03-05.json          # DailyPerformance
      2026-03-06.json
    weekly/
      2026-W10.json            # PeriodPerformance
    monthly/
      2026-03.json             # PeriodPerformance
    cumulative.json            # 累計績效（啟動至今）
```

### cumulative.json（持續更新）

```json
{
  "first_trade_date": "2026-03-05",
  "last_updated": "2026-03-05T13:45:00",
  "trading_mode": "paper",
  "initial_balance": 43480,
  "current_balance": 44200,
  "total_pnl": 720,
  "total_return_pct": 1.66,
  "total_trading_days": 5,
  "total_trades": 42,
  "overall_win_rate": 57.1,
  "overall_profit_factor": 1.35,
  "max_drawdown": 1200,
  "max_drawdown_pct": 2.76,
  "sharpe_ratio": 1.2,
  "best_day": {"date": "2026-03-03", "pnl": 580},
  "worst_day": {"date": "2026-03-04", "pnl": -320},
  "current_streak": {"type": "win", "count": 3}
}
```

---

## 三、新增模組：`core/performance.py`

```python
class PerformanceTracker:
    """
    績效記錄器 — 掛在 TradingEngine 上，自動記錄。
    """

    def __init__(self, data_dir: str = "data/performance"):
        self.data_dir = data_dir
        self.today_trades: list[Trade] = []
        self.starting_balance: float = 0

    # === 核心方法 ===

    def on_trade_closed(self, trade: Trade):
        """每筆交易結束時呼叫（engine 呼叫）"""
        self.today_trades.append(trade)
        self._save_incremental()

    def on_session_end(self, ending_balance: float):
        """
        每日收盤（13:45 日盤 / 05:00 夜盤）時呼叫
        1. 生成 DailyPerformance
        2. 存 daily JSON
        3. 更新 cumulative.json
        4. 若週五 → 生成 weekly
        5. 若月底 → 生成 monthly
        """

    def get_daily_summary(self, date: str) -> DailyPerformance:
        """讀取指定日期績效"""

    def get_cumulative(self) -> dict:
        """讀取累計績效"""

    def get_post_content(self, date: str = None) -> dict:
        """
        生成發文用的結構化數據（給 MindThread API 用）
        Returns: {
            "headline": "+720 元 | 勝率 57% | 連勝 3 場",
            "body": "完整文案...",
            "metrics": { ... },
            "hashtags": ["#期貨", "#自動交易", "#UltraTrade"]
        }
        """

    # === 內部 ===

    def _save_incremental(self):
        """每筆交易即時存檔（防止意外斷線丟失）"""

    def _calculate_drawdown(self, trades: list) -> tuple[float, float]:
        """計算最大回撤"""

    def _update_cumulative(self, daily: DailyPerformance):
        """更新 cumulative.json"""
```

---

## 四、API 接口（Dashboard 擴充）

在 `dashboard/app.py` 新增：

```python
# === 績效 API ===

@app.get("/api/performance/daily/{date}")
async def get_daily_performance(date: str):
    """取得指定日期績效 JSON"""

@app.get("/api/performance/daily/latest")
async def get_latest_daily():
    """取得最新一天的績效"""

@app.get("/api/performance/cumulative")
async def get_cumulative():
    """取得累計績效"""

@app.get("/api/performance/weekly/{week}")
async def get_weekly_performance(week: str):
    """取得指定週績效，如 2026-W10"""

@app.get("/api/performance/monthly/{month}")
async def get_monthly_performance(month: str):
    """取得指定月績效，如 2026-03"""

# === MindThread 發文接口 ===

@app.get("/api/publish/daily-post")
async def get_daily_post_content(date: str = None):
    """
    生成當日績效文案（結構化）
    MindThread 排程 job 呼叫此接口 → 拿到文案 → 寫入 Firestore library

    Response:
    {
      "post_content": "【3/5 日結】\n\n微台指 日盤\n...",
      "metrics": { "pnl": 720, "win_rate": 57.1, ... },
      "ready": true
    }
    """

@app.post("/api/publish/push-to-mindthread")
async def push_to_mindthread(date: str = None):
    """
    主動推送績效文案到 MindThread Firestore library
    （需要設定 FIREBASE_CREDENTIALS 環境變數）

    1. 呼叫 get_daily_post_content() 生成文案
    2. 寫入 Firestore: library/{docId}
    3. 回傳結果

    Body (optional):
    {
      "account_id": "ultratrade_account_id",
      "user_id": "firebase_user_id"
    }
    """
```

---

## 五、MindThread 整合流程

### 方案 A：Pull 模式（MindThread 來抓）
```
每日收盤
  → UltraTrader 存 daily JSON
  → MindThread cron job 呼叫 GET /api/publish/daily-post
  → 拿到文案 → 寫入 Firestore library
  → MindThread 排程自動發文
```

### 方案 B：Push 模式（UltraTrader 主動推）⬅ 建議
```
每日收盤
  → UltraTrader on_session_end()
  → PerformanceTracker 生成 DailyPerformance
  → 自動呼叫 push_to_mindthread()
  → 直接寫入 Firestore library
  → MindThread 排程自動發文
```

### Push 模式需要的環境變數

```env
# .env 新增
MINDTHREAD_ENABLED=true
MINDTHREAD_ACCOUNT_ID=<ultratrade 的 Firestore account doc ID>
MINDTHREAD_USER_ID=<Firebase user ID>
FIREBASE_CREDENTIALS=<path to service account JSON>
```

---

## 六、文案模板（AI system_prompt 生成）

UltraTrade 帳號的 `system_prompt` 建議：

```
你是 UltraTrade 的績效播報員。
根據提供的交易數據，生成一篇 Threads 短文。

格式規範：
1. 第一行：【日期 日結/週結/月結】
2. 第二行：空行
3. 損益數字用醒目格式
4. 包含：損益、勝率、交易筆數、最大獲利/虧損
5. 結尾一句反思或市場觀察
6. 不加 hashtag（系統自動加）

語氣：
- 數據導向，不吹噓
- 賺錢不炫耀，賠錢不迴避
- 像寫交易日記，給自己看的那種真實

範例（獲利日）：
【3/5 日結】

微台指 紙上交易
今日 +$720（+1.66%）

6 筆交易 / 勝率 67%
最大獲利：+$380（動能突破）
最大虧損：-$120（假突破停損）

今天盤勢偏多但波動不大
ADX 只有 21，信號品質普通
能小賺已經不錯

累計報酬：+1.66%（第 5 天）

範例（虧損日）：
【3/4 日結】

微台指 紙上交易
今日 -$320（-0.73%）

8 筆交易 / 勝率 37%
連續假突破 3 次

盤整盤硬做動能策略
本來就該被打臉
明天看 ADX 再決定要不要出手

累計報酬：+0.92%（第 4 天）
```

---

## 七、Engine 整合點

### 在 `core/engine.py` 掛入 PerformanceTracker

```python
class TradingEngine:
    def initialize(self):
        # ... 現有初始化 ...
        self.performance = PerformanceTracker(
            data_dir="data/performance"
        )
        self.performance.starting_balance = self.initial_balance

    def _on_trade_closed(self, trade: Trade):
        # ... 現有處理 ...
        self.performance.on_trade_closed(trade)

    def _on_session_end(self):
        # 日盤 13:45 或夜盤 05:00 觸發
        self.performance.on_session_end(
            ending_balance=self.position_manager.current_balance
        )
```

---

## 八、實作優先序

| 順序 | 項目 | 預估 |
|------|------|------|
| 1 | `core/performance.py` — PerformanceTracker 類 | 核心 |
| 2 | Trade dataclass 補強 (strategy, MFE, MAE) | 小改 |
| 3 | Engine 整合（on_trade_closed, on_session_end） | 小改 |
| 4 | daily/cumulative JSON 存儲 | 含在 1 |
| 5 | Dashboard API endpoints | 中等 |
| 6 | MindThread push 接口 | 中等 |
| 7 | weekly/monthly 自動彙總 | 後補 |
| 8 | Threads 帳號建立 + system_prompt | 最後 |

---

## 九、注意事項

1. **Paper 模式也要記錄** — 紙上交易的績效同樣有發文價值（「測試第 N 天」系列）
2. **虧損也要發** — 真實感 = 信任感 = 高互動。只發賺錢的會被當詐騙
3. **法規提示** — 每篇結尾自動加「以上為紙上交易/模擬績效，非投資建議」
4. **時區** — 所有時間用 Asia/Taipei，跟 MindThread 一致
5. **防重複發文** — push 時檢查該日期是否已發過
