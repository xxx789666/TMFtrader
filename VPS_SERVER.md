# VPS 伺服器資訊（GCP ultratrader-night）

> 建立：2026-05-12 ｜ 上線：2026-05-13（Day 1）
> 本檔記錄 VPS 規格、連線方式、檔案位置、運行 process、cron、安全層、緊急指令
> 機密（API key / 憑證 / TG token）不在本檔、見 `.env`（已 gitignore）

---

## 規格與位置

| 項 | 值 |
|---|---|
| 雲端商 | Google Cloud Platform (GCP) |
| Project | `project-ae93c5d6-cf6e-402d-969` |
| VM 名稱 | `ultratrader-night` |
| 地理位置 | **台灣彰化** —— `asia-east1-b` zone（asia-east1 region）|
| 靜態 IP（對外）| **35.221.239.245** |
| 內部 IP | 10.140.0.2 |
| 規格 | e2-small（2 vCPU / 2GB RAM）|
| 磁碟 | 50GB pd-balanced SSD |
| OS | Ubuntu 22.04 LTS（kernel 6.8.0-1054-gcp）|
| SSH 用戶 | `xx`（gcloud 自動建）|
| 計費帳號 | `billingAccounts/014C58-051BF3-CA0541`（`x011training@gmail.com`）|
| 試用額度 | US$300 / 90 天（用滿或到期後轉付費）|

---

## 連線方式

| 方式 | 指令 |
|---|---|
| GCP Console SSH（瀏覽器）| https://console.cloud.google.com → Compute Engine → `ultratrader-night` → SSH |
| gcloud CLI（WSL2）| `gcloud compute ssh ultratrader-night --zone=asia-east1-b` |
| 純 SSH（WSL2）| `ssh -i ~/.ssh/google_compute_engine xx@35.221.239.245` |
| Windows PowerShell（用 wsl 轉手）| `wsl ssh -i ~/.ssh/google_compute_engine xx@35.221.239.245` |

---

## VPS 內檔案結構

```
/home/xx/
└── ultra-trader-src/                  # 整個專案（從 永豐-自動化交易/ 同步來）
    ├── .env                           # SHIOAJI keys / cert path / TG token
    ├── .venv/                         # Python 3.12 venv
    ├── certs/cert.pfx                 # Shioaji 永豐憑證（2752 bytes、~2027/4 到期）
    ├── core/                          # broker / engine / risk / notify / market_data
    │   └── broker.py                  # ⚠️ 已 patch (fetch_contract=False + manual fetch_contracts)
    ├── strategy/                      # 策略邏輯
    ├── dashboard/                     # FastAPI 監測（port 8888）
    ├── deployed_strategies/           # B2 ML model 等
    │   └── tmf_orb_night/
    │       ├── orb_filter_b2.pkl      # XGBoost 模型
    │       └── selected_features_b2.txt
    ├── scripts/
    │   ├── start.py                   # 日盤 entry（FastAPI server）
    │   ├── paper_night_orb.py         # 夜盤 ORB B2 ML paper trading
    │   ├── watchdog.py                # 12-min 心跳監控
    │   ├── restart_day.sh             # 日盤啟動腳本
    │   ├── restart_night.sh           # 夜盤啟動腳本
    │   ├── start_watchdog.sh          # watchdog 啟動腳本
    │   └── shioaji_api_test.py        # API 開通測試腳本
    └── data/
        ├── performance/daily/         # 每日 _live.json / _live_night.json
        ├── paper_trading/             # paper_night_orb CSV
        ├── logs/                      # ultratrader_YYYYMMDD.log / cron.log / watchdog.log
        └── risk_state{,_night}.json   # 風控狀態（peak_equity / daily_loss / circuit_state）
```

---

## 運行的 process

| Process | 角色 | 觸發 |
|---|---|---|
| `python3.12 scripts/start.py --no-browser` | 日盤 FastAPI server（port 8888）| cron 每週一~五 08:30 |
| `python3.12 scripts/paper_night_orb.py --threshold 0.40` | 夜盤 ORB B2 ML paper | cron 每週一~五 14:55 |
| `python3.12 scripts/watchdog.py --night` | 12-min 心跳 + 自癒重啟 | cron 每週一~五 14:56 |

確認用：
```bash
pgrep -fa "scripts/start.py|paper_night_orb.py|watchdog.py"
```

---

## Crontab 排程（UTC → 台北時間 -8）

```cron
# === 日盤 ===
30 0 * * 1-5    /home/xx/ultra-trader-src/scripts/restart_day.sh        # 08:30 TST 啟動
45 5 * * 1-5    pkill -f 'api.server.*8888'                              # 13:45 TST 關閉

# === 夜盤 ===
55 6 * * 1-5    /home/xx/ultra-trader-src/scripts/restart_night.sh      # 14:55 TST 啟動
56 6 * * 1-5    /home/xx/ultra-trader-src/scripts/start_watchdog.sh     # 14:56 TST watchdog
10 21 * * 0-4   pkill -f 'api.server.*8889'                              # 05:10 TST(隔日) 關
10 21 * * 0-4   pkill -f 'watchdog.py.*--night'                          # 05:10 TST 關 watchdog
```

看 / 改：`crontab -l` / `crontab -e`

---

## 延遲（為什麼選台灣彰化）

| 條件 | 延遲 |
|---|---|
| asia-east1（台灣彰化）→ 永豐 Shioaji 機房（台北 / 新北）| **< 5ms** |
| Vultr / Linode Tokyo → 永豐 | ~40ms |
| 桌機家用網路 → 永豐 | ~5–15ms（看 ISP） |

VPS 機房骨幹比家用網路更穩定、且 < 5ms 完全符合永豐 API 同意書條 6「需穩定、快速連線」的合規要求。

---

## 月費估算

| 項目 | 月費 |
|---|---|
| e2-small（2 vCPU / 2GB）| ~US$13 (~NT$420) |
| 50GB pd-balanced 磁碟 | ~US$5 (~NT$160) |
| 靜態 IP | ~US$1 (~NT$30) |
| 對外流量 | ~US$1 (~NT$30) |
| **合計** | **~US$20 / NT$640** |

> 試用期 90 天 $300 額度、實際燒掉約 $60 / 90 天、剩 $240 不會延長試用期、到期前要在帳單頁手動點「升級為付費」否則 VM 停。
> 試用到期日：依 5/12 註冊算，**~2026-08-10**。

---

## 安全層

| 層 | 規則 |
|---|---|
| GCP 雲端 firewall | `deny-trading-ports`：對外 DENY 8888、8889 |
| Linux 本機 ufw | 除 SSH 22 全 DENY |
| Shioaji API 同意書條 6 | 強制有線網路 → 機房骨幹 ✓ |
| Shioaji API 同意書條 9 | 行情禁轉散布 → 防火牆已擋 + 日誌不對外 |
| Secret 管理 | `.env` 不入 git；TG token、Shioaji key、cert path 都從 env var 讀 |
| SSH key | `~/.ssh/google_compute_engine`（gcloud 自動產、僅本機）|

---

## 常用指令速查

### 連線後第一件事（看健康）
```bash
date && uptime                          # 時間 + load
free -m && df -h /                      # RAM / 磁碟
pgrep -fa "start.py|paper_night_orb|watchdog.py"   # 確認 process
tail -50 ~/ultra-trader-src/data/logs/ultratrader_$(date +%Y%m%d).log
```

### 看當日交易
```bash
cat ~/ultra-trader-src/data/performance/daily/$(date +%F)_live.json | python3 -m json.tool
cat ~/ultra-trader-src/data/performance/daily/$(date +%F)_live_night.json | python3 -m json.tool 2>/dev/null
```

### 看風控狀態
```bash
cat ~/ultra-trader-src/data/risk_state.json
cat ~/ultra-trader-src/data/risk_state_night.json
```

### 手動重啟
```bash
bash ~/ultra-trader-src/scripts/restart_day.sh
bash ~/ultra-trader-src/scripts/restart_night.sh
bash ~/ultra-trader-src/scripts/start_watchdog.sh
```

### 緊急平倉（live 期）
```bash
# 切回 paper（不平倉、新單轉 paper）
sed -i 's/TRADING_MODE=live/TRADING_MODE=paper/' ~/ultra-trader-src/.env
bash ~/ultra-trader-src/scripts/restart_night.sh

# 立即關掉所有 trader
pkill -f 'paper_night_orb.py|scripts/start.py'

# SSH tunnel + 全平倉（需 dashboard API 有此端點、待確認）
ssh -L 8889:localhost:8889 -i ~/.ssh/google_compute_engine xx@35.221.239.245 &
curl -X POST http://localhost:8889/api/close_all
```

### 看 cron log
```bash
tail -50 ~/ultra-trader-src/data/logs/cron.log
journalctl -u cron.service --since "1 hour ago"
```

---

## 從本機（WSL2 Hermes orchestrator）連 VPS

`scripts/.env.sync` 設定：
```
VPS_HOST=xx@35.221.239.245
VPS_PROJECT_DIR=/home/xx/ultra-trader-src
SSH_KEY=$HOME/.ssh/google_compute_engine
SYNC_DAYS=14
TMF_DATA_ROOT=$HOME/vps_trader/ultra-trader-src/data
```

每週六 09:00 Windows Task Scheduler 觸發、rsync 拉近 14 天 daily JSON 到本機、餵 Hermes 產覆盤推 TG。

---

## 已知問題與修法

| 問題 | 修法 | 紀錄 |
|---|---|---|
| `fetch_contract=True` 強制下載 Options、新 IP 沒權限 | `core/broker.py` 改 `fetch_contract=False` + 手動 `fetch_contracts()` try/except | commit `cfdd758` |
| Shioaji 從 Windows IP `Sign data is timeout` | 改在 VPS IP 跑 API 開通測試 | 已通過、`signed=True` |
| Cron 預設用 UTC、不是 Asia/Taipei | crontab 內所有時間 -8（08:30 TST = 00:30 UTC） | crontab 已套 |
| WSL2 連 VPS 第一次 SSH 要 `accept-new` | `-o StrictHostKeyChecking=accept-new` 自動接受 | `.env.sync` 已含 |
| 期貨 API 同意書 + 開通測試 | 5/12 簽 + 5/13 09:21 通過 `signed=True` | memory `sinopac-api-consent` |

---

## 緊急聯絡

- **永豐期貨客服**：0800-038-123（IP 風控 / API 服務問題）
- **GCP support**：免費試用期內限 Stackdriver / Issue tracker；付費後可開 case
- **Telegram bot 失效**：@BotFather → `/mybots` → 看 bot 狀態 / token

---

## 變更紀錄

| 日期 | 事件 |
|---|---|
| 2026-05-12 21:30 | VM 開機、IP 升靜態、安裝 Python 3.12 + deps |
| 2026-05-12 22:03 | 夜盤 paper_night_orb.py 首次啟動 |
| 2026-05-13 08:30 | cron 自動啟動日盤、broker Options bug 浮現 |
| 2026-05-13 09:15 | Shioaji API 開通測試（VPS IP）跑通 |
| 2026-05-13 09:21 | `futopt_account.signed=True` 驗證審核完成 |
| 2026-05-13 09:23 | broker.py patch 完、日盤重啟成功 |
| 2026-05-13 09:00–10:00 | Hermes orchestrator `.env.sync` 改連 VPS、rsync 通 |
| 2026-05-19 (Mon)（目標）| `TRADING_MODE=live` 切實單 🚀 |
