# 目前運行中策略總覽

> 最後更新：2026-04-19

---

## 策略一覽

| 項目 | 日盤策略 | 夜盤策略 |
|------|---------|---------|
| **策略名稱** | Breakout Trend v6b | ORB B2 |
| **交易標的** | TMF（台灣美債 3x ETF） | TMFN（TMF 夜盤） |
| **交易時段** | 08:45 – 13:30 TST | 21:30 – 04:00 TST |
| **K棒時框** | 5 分K | 5 分K |
| **每月均交易次數** | ~1.9 筆/月 | ~3.6 筆/月 |
| **風險等級** | tmf_3x | tmf_3x |
| **交易模式** | Paper（永豐模擬盤） | Paper（永豐模擬盤） |
| **模擬預算** | 200,000 | 200,000 |
| **Dashboard** | http://localhost:8888 | http://localhost:8889 |
| **啟動日期** | 2026-04-14 | 2026-04-19 |
| **自動排程** | 無（手動啟動） | 工作排程器 Mon–Fri 20:55 |

**合計月均交易：~5.5 筆/月**

---

## 策略一：Breakout Trend v6b（日盤）

- **版本**：第 6 版 b
- **核心邏輯**：價格突破前高/前低 + 趨勢過濾（EMA200）
- **ML 層**：Breakout ML Meta-Label Filter
  - 訓練商品：16 instruments（SPXL/TECL/QQQM/IVV/TMF 等），1,194 trades
  - Stage 7 全 PASS（MC / Label Shuffle / Perturbation 7/7 / LOIO AUC=0.825）
  - TMF OOS 17 筆：WR 76.5% → 76.9%，avg_R +33.4%，PF 3.88 → 4.91
- **模型檔**：`optimizer/results/breakout_filter.pkl`
- **啟動腳本**：`scripts/start.py`（由 `.env` 讀取設定，port 8888）
- **重訓條件**：尚未設定，Paper Trading 觀察中（目標 3 個月）

---

## 策略二：ORB B2（夜盤）

- **版本**：第 2 版（B2）
- **核心邏輯**：Opening Range Breakout（前 30 分鐘高低點突破）
- **Layer 1 過濾器**：
  - ORB 寬度：3–5 ATR
  - 高量假突破過濾：`max_breakout_vol_ratio = 2.0`（B2 新增）
  - 訓練樣本：507 → 135 筆，WR 48.4% → **55.6%**
- **ML 層（B2）**：
  - 訓練商品：TMF_night + 9 US ETF，閾值 0.40
  - OOS 9.5 個月（2025-07 ~ 2026-04）：N=34，WR=50%，avg_R=+0.300，Total R=+10.2R
  - Stage 7：3/4 PASS（7.3 Perturbation FAIL）
  - LOIO TMF AUC：0.621
- **模型檔**：`optimizer/results_orb/orb_filter_b2.pkl`
- **啟動腳本**：`start_night_server.bat`（port 8889）
- **自動排程**：工作排程器任務 `TMFtrader-NightORB`，每週一至五 20:55
- **重訓條件**：TMF OOS 累積 ≥ 150 筆 → 訓練 B3

---

## 伺服器管理

```bash
# 查看夜盤排程狀態
schtasks /query /tn "TMFtrader-NightORB" /fo LIST

# 手動立即執行夜盤
schtasks /run /tn "TMFtrader-NightORB"

# 檢查 port 是否監聽
netstat -ano | findstr ":8888"
netstat -ano | findstr ":8889"
```

---

## 績效追蹤

| 策略 | Paper 起始日 | OOS WR | avg_R | 備註 |
|------|------------|--------|-------|------|
| Breakout v6b | 2026-04-14 | 76.9% | +0.677 | ML filter 觀察中 |
| ORB B2 | 2026-04-19 | 50.0% | +0.300 | 累積至 150 筆重訓 |
