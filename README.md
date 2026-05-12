# 永豐微台指 — 雲端部署包

> 打包時間：2026-05-12 | 版本：日盤 breakout v6b + 夜盤 ORB Phase 5

## 目錄結構

```
vps永豐微台指/
├── README.md                          ← 本檔（部署索引）
├── vps_zeabur_deploy.md               ← 完整部署 SOP（10 步驟）
├── ultra-trader-src/                  ← 程式主體（9.8 MB）
│   ├── core/                            引擎、broker、position、market_data
│   ├── strategy/                        breakout + ORB 策略邏輯
│   ├── risk/                            風控（manager、circuit_breaker、persistence）
│   ├── dashboard/                       監測 UI（FastAPI + WebSocket + Vue + lightweight-charts）
│   ├── intelligence/                    台股、國際盤、P/C Ratio 等情報收集
│   ├── scripts/                         自癒 watchdog、夜盤啟動腳本
│   ├── docs/                            部署/設計文件
│   ├── tests/                           pytest 單元測試
│   ├── data/
│   │   ├── state/                       Paper 持倉狀態（重啟恢復用）
│   │   ├── performance/daily/           交易日誌（日夜盤分檔 _live.json / _live_night.json）
│   │   ├── risk_state.json              日盤風控狀態
│   │   └── risk_state_night.json        夜盤風控狀態
│   ├── .env                             ⚠ 含 Shioaji API key + 憑證路徑 + TG token
│   ├── requirements.txt                 Python 依賴
│   └── *.bat / *.ps1                    Windows 啟動腳本（Linux 用 cron 取代）
├── deployed_strategies/                 ← 已部署策略配置（7.4 MB）
│   ├── tmf_orb_night/                     ORB ML 模型（B2，目前停用）
│   ├── tmf_breakout/                      breakout 策略說明
│   └── us_etf_orb_ml/
└── memory/                             ← Claude 操作知識（100 KB）
    ├── MEMORY.md                         主索引（建議優先讀）
    ├── 2026_05_04_bug_fixes.md           5/4 三個 bug 修復
    ├── 2026_05_05_fixes.md               5/5 策略狀態持久化
    ├── 2026_05_06_fixes.md               5/6 backtest 污染 + journal 碰撞
    ├── 2026_05_07_to_11_fixes.md         5/7-11 watchdog/dashboard/排程修復
    └── *.md                              其他過往修復紀錄
```

## ⚠️ 沒包進來的東西（需另外處理）

### 1. 歷史資料 `data/historical/` ← 2.6 GB

太大不放，**照 SOP Step 4-2 用 `gcloud scp` 直接上傳**：

```bash
gcloud compute scp --recurse \
  "C:\Users\xx\Desktop\永豐-自動化交易\ultra-trader-src\data\historical" \
  ultratrader-night:~/ultra-trader-src/data/ \
  --zone=asia-east1-b
```

包含：`tmf_5y_1m.parquet`、`tmf_5y_5m.parquet`、`ml/` 資料夾、`tmf_20260410_1m.csv` 等。
**沒這些 parquet，backtest 無法跑，但 paper/live 即時交易不受影響**（即時模式用 Shioaji 抓最新 K 棒）。

### 2. 永豐憑證 `.pfx`

**.env 裡寫的是 Windows 路徑** `C:/Users/xx/Downloads/Sinopac.pfx`，Linux 用要：

1. 把 `.pfx` 檔 scp 上 VM
2. 改 `.env` 的 `SHIOAJI_CA_PATH` 為 Linux 路徑（例 `/root/ultra-trader-src/certs/cert.pfx`）

### 3. 即時 log `data/logs/`

刻意排除（每天會自動生成新的）。

## 部署順序（精簡版）

詳細見 `vps_zeabur_deploy.md`，重點：

1. **GCP 開 VM**：asia-east1（台灣彰化）、e2-small、Ubuntu 22.04、50GB SSD
2. **SSH 進去裝 Python 3.12** + pip + 依賴
3. **上傳本資料夾**：用 `gcloud scp --recurse` 或 git clone
4. **單獨上傳 historical/**（2.6GB）
5. **單獨上傳憑證 pfx**
6. **修 .env 路徑** 為 Linux 格式
7. **修程式碼路徑**：`grep -rn "C:\\\\" --include="*.py"` 找硬編路徑改成 `Path(__file__).parent.parent`
8. **設 cron 排程**：日盤 08:35 啟動、夜盤 14:55 啟動（注意 GCP 預設 UTC，台灣時間要 −8）
9. **GCP 防火牆封 port 8888/8889** 只允許 127.0.0.1
10. **paper 跑 7 天驗證** → 切 `TRADING_MODE=live`

## 四大模組對照

| 你說的 | 對應的檔案/資料夾 |
|--------|------------------|
| **策略** | `ultra-trader-src/strategy/` + `deployed_strategies/` |
| **監測** | `ultra-trader-src/dashboard/`（FastAPI server, port 8888/8889 dashboard）|
| **自癒** | `ultra-trader-src/scripts/watchdog.py`（搜尋 `[Scan]` 心跳, 12 min 無 → 重啟）|
| **記憶** | `memory/`（Claude 操作知識）+ `data/state/`（持倉持久化）+ `data/risk_state*.json`（風控狀態）|

## 上線前必看的 memory

優先讀順序：

1. `memory/MEMORY.md` ← 全部索引 + 診斷順序
2. `memory/paper_trading_peak_equity_bug.md` ← peak_equity 跨重啟殘留陷阱
3. `memory/2026_05_07_to_11_fixes.md` ← 最近修復（含 INITIAL_BALANCE 漏算的反覆問題）
4. `memory/server_restart_method.md` ← Windows / Linux 不同重啟方式
5. `memory/shioaji_login_ban_diagnosis.md` ← Shioaji 登入失敗誤診教訓

## 上線後第一個禮拜檢查清單

每日（建議晨 7:00 看一次）：

- [ ] `cat data/risk_state_night.json` peak_equity 是否合理（≈ INITIAL_BALANCE）
- [ ] `grep "_execute_exit_inner" data/logs/ultratrader_$(date +%Y%m%d).log` 看出場紀錄
- [ ] TG 是否有「策略心跳異常」spam（若有 → 查 watchdog log）
- [ ] `curl localhost:8889/api/state | python -m json.tool | head -20` 看 balance 是否累計正確

每週：

- [ ] `data/performance/daily/` 對齊一週 P&L 跟 INITIAL_BALANCE 差距，如有 drift 需手動同步（root fix 待做）

---

*最後更新：2026-05-12 | 打包 by Claude*
