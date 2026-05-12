# UltraTrader

**台灣期貨自動交易系統 — 開源**

**[English](#english) | [繁體中文](#繁體中文)**

---

<a name="english"></a>

## What is this?

UltraTrader is an open-source algorithmic trading system for Taiwan futures (TAIFEX), built with Python and Shioaji (Sinopac Securities API).

Features:
- **3 built-in strategies**: Adaptive Momentum, Mean Reversion, Gold Trend
- **Full risk management**: Circuit breaker, position sizing, drawdown protection
- **Backtesting engine**: Historical data replay with detailed reports
- **Real-time dashboard**: FastAPI + WebSocket live monitoring
- **Market intelligence**: Multi-timeframe regime detection
- **3 trading modes**: Simulation (local) → Paper (Sinopac sandbox) → Live

Zero cloud dependencies. Runs entirely on your machine.

## Architecture

```
UltraTrader/
├── core/           # Engine, broker API, market data, position management
├── strategy/       # Trading strategies (momentum, mean_reversion, gold_trend)
├── risk/           # Circuit breaker, position sizing, drawdown limits
├── backtest/       # Backtesting engine + report generator
├── dashboard/      # FastAPI + WebSocket real-time UI
├── intelligence/   # Market regime classifier, signal scoring
├── scripts/        # CLI tools (start, backtest, diagnostics)
├── tests/          # Unit tests
└── data/           # Historical data, backtest results (gitignored)
```

## Quick Start

```bash
git clone https://github.com/ppcvote/ultra-trader.git
cd ultra-trader
pip install -r requirements.txt
cp .env.example .env
# Edit .env (simulation mode works without API keys)
python scripts/start.py
```

### Trading Modes

| Mode | What it does | Needs API key? |
|------|-------------|----------------|
| `simulation` | Local mock broker, fake ticks | No |
| `paper` | Sinopac sandbox, real market data | Yes |
| `live` | Real money, real orders | Yes + CA cert |

### Run Backtest

```bash
python scripts/backtest_runner.py
# Results saved to data/backtest_results/
```

### Dashboard

```bash
python scripts/start.py --port 8888
# Open http://localhost:8888
```

## Strategies

### Adaptive Momentum (Primary)
- 7-factor entry signals with multi-timeframe confirmation (5m/15m)
- Session-aware: higher thresholds at open, no new positions near close
- Chandelier Exit + continuous adaptive stop-loss
- Partial take-profit at 2x/3x/4x ATR (1/3 each)
- Slippage buffer on all stops

### Mean Reversion
- Bollinger Band extremes + RSI divergence
- Best in ranging/sideways markets
- Tight stops, quick exits

### Gold Trend
- Designed for gold futures (TGF)
- Trend-following with momentum filters

## Risk Management

- **Circuit Breaker**: Auto-stop trading after N consecutive losses or daily loss limit
- **Position Sizing**: Kelly criterion with configurable risk profiles (conservative/balanced/aggressive)
- **Drawdown Protection**: Max drawdown % triggers trading halt
- **Session Management**: No new positions in last 30 min, force close in last 5 min

## Tech Stack

- **Language**: Python 3.10+
- **Broker API**: [Shioaji](https://sinotrade.github.io/) (Sinopac Securities)
- **Dashboard**: FastAPI + WebSocket
- **Data**: pandas + numpy
- **Logging**: loguru

## Disclaimer

**This software is for educational and research purposes only.** Trading futures involves substantial risk of loss. Past performance (including backtests) does not guarantee future results. Use at your own risk. The authors are not responsible for any financial losses.

---

<a name="繁體中文"></a>

## 這是什麼？

UltraTrader 是開源的台灣期貨自動交易系統，使用 Python + 永豐證券 Shioaji API。

功能：
- **3 個內建策略**：自適應動量、均值回歸、黃金趨勢
- **完整風控**：熔斷機制、部位管理、回撤保護
- **回測引擎**：歷史數據回放 + 詳細報告
- **即時儀表板**：FastAPI + WebSocket 即時監控
- **市場情報**：多時框盤勢分類
- **3 種模式**：本地模擬 → 永豐模擬盤 → 實單

完全本地運行，不需要雲端。

## 快速開始

```bash
git clone https://github.com/ppcvote/ultra-trader.git
cd ultra-trader
pip install -r requirements.txt
cp .env.example .env
# 編輯 .env（模擬模式不需要 API key）
python scripts/start.py
```

## 策略簡介

### 自適應動量策略（主策略）
- 7 因子進場：多時框確認（5 分 K / 15 分 K）
- 盤別感知：開盤 15 分提高門檻、收盤前不開新倉
- Chandelier Exit + 連續自適應停損
- 分段停利：2x/3x/4x ATR 各出 1/3
- 含滑價修正

### 均值回歸策略
- 布林通道極端值 + RSI 背離
- 適合盤整行情
- 快進快出

### 黃金趨勢策略
- 針對黃金期貨（TGF）設計
- 趨勢跟隨 + 動量過濾

## 風控機制

- **熔斷**：連續虧損 N 次或單日虧損上限 → 自動停止交易
- **部位管理**：Kelly 公式 + 風險等級（保守/平衡/積極）
- **回撤保護**：最大回撤 % 觸發交易暫停
- **盤別管理**：收盤前 30 分不開新倉、收盤前 5 分強制平倉

## 免責聲明

**本軟體僅供教育和研究用途。** 期貨交易具有高度風險，可能導致重大損失。歷史績效（含回測）不代表未來表現。使用風險自負，作者不承擔任何財務損失責任。

---

## Credits

Built by [Ultra Lab](https://ultralab.tw) — a one-person AI product studio from Taiwan.

[![Discord](https://img.shields.io/discord/1459618830773911633?color=5865F2&label=Discord&logo=discord&logoColor=white)](https://discord.gg/ewS4rWXvWk)

## License

MIT
