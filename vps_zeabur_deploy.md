# UltraTrader 雲端部署指南

> 文件版本：2026-05-03 | 策略：Phase 5 夜盤 ORB（ML 停用，trail_dist=0.3）
> **確定方案：GCP asia-east1 台灣彰化 + Ubuntu 22.04 + e2-small**

---

## 為什麼選 GCP asia-east1 台灣彰化？

| 項目 | 說明 |
|------|------|
| **到 Shioaji 延遲** | **< 5ms**（台灣境內，同一座島）|
| **月費** | **NT$450**（e2-small，2 vCPU, 2GB RAM）|
| **台灣社群主流** | FinLab 官方教學明確指定「地區選台灣」|
| **Zeabur 比較** | Zeabur 無台灣機房（最近 Tokyo 40ms），月費又貴 2.5 倍 |

---

## 部署總覽（半天完成）

```
Step 1  建立 GCP 帳號 + 開 VM（30 分鐘）
Step 2  SSH 連線進入 VM（5 分鐘）
Step 3  安裝 Python 環境（15 分鐘）
Step 4  上傳程式碼與資料（30 分鐘）
Step 5  修正路徑（相對路徑）（30 分鐘）
Step 6  設定 .env 環境變數（10 分鐘）
Step 7  設定 crontab 排程（10 分鐘）
Step 8  防火牆設定（10 分鐘）
Step 9  Paper Trading 驗證（當晚）
Step 10 切換實單（觀察 1 週後）
```

---

## Step 1：建立 GCP VM

### 1-1 申請 GCP 帳號

1. 前往 [console.cloud.google.com](https://console.cloud.google.com)
2. 使用 Google 帳號登入
3. 啟用免費試用（**$300 美元額度，90 天**）→ 可先免費測試
4. 填寫信用卡（驗證用，試用期不扣款）

確認清單：
- [ ] Google 帳號已登入
- [ ] 免費試用已啟用，顯示 $300 額度

---

### 1-2 建立 VM 執行個體

1. 左側選單 → **Compute Engine** → **VM 執行個體** → **建立執行個體**
2. 填入以下設定：

| 設定項目 | 選擇值 |
|---------|--------|
| **名稱** | `ultratrader-night` |
| **區域（Region）** | `asia-east1`（台灣）⬅️ 必選 |
| **可用區（Zone）** | `asia-east1-b` |
| **機器系列** | E2 |
| **機器類型** | `e2-small`（2 vCPU, 2GB RAM）|
| **開機磁碟** | Ubuntu 22.04 LTS，**50GB** SSD |
| **防火牆** | 允許 HTTP、HTTPS 流量 |

3. 點 **建立**，等待約 1 分鐘

確認清單：
- [ ] 區域確認為 `asia-east1`（台灣彰化）
- [ ] 機器類型為 `e2-small`
- [ ] VM 狀態顯示綠色（執行中）
- [ ] 記下**外部 IP 位址**

---

### 1-3 設定靜態 IP（避免重啟後 IP 改變）

1. **VPC 網路** → **外部 IP 位址**
2. 找到 `ultratrader-night` 的 IP → 類型改為 **靜態**
3. 命名：`ultratrader-ip`

確認清單：
- [ ] IP 類型已改為靜態
- [ ] 記下固定 IP（之後 SSH 連線用）

---

## Step 2：SSH 連線進入 VM

### 方法 A：瀏覽器直接 SSH（最簡單）

GCP Console → VM 執行個體 → 點 `ultratrader-night` 旁邊的 **SSH** 按鈕

→ 瀏覽器直接開啟終端機，不需安裝任何工具

確認清單：
- [ ] 終端機視窗成功開啟
- [ ] 顯示 `username@ultratrader-night:~$`

### 方法 B：本機 SSH（進階）

```bash
# 安裝 gcloud CLI 後
gcloud compute ssh ultratrader-night --zone=asia-east1-b
```

---

## Step 3：安裝 Python 3.12 環境

在 VM 終端機執行：

```bash
# 更新套件清單
sudo apt update && sudo apt upgrade -y

# 安裝必要工具
sudo apt install -y git curl wget unzip screen htop

# 安裝 Python 3.12
sudo apt install -y software-properties-common
sudo add-apt-repository ppa:deadsnakes/ppa -y
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3.12-dev

# 確認版本
python3.12 --version

# 安裝 pip
curl -sS https://bootstrap.pypa.io/get-pip.py | python3.12
python3.12 -m pip install --upgrade pip

# 建立 alias 方便使用
echo "alias python=python3.12" >> ~/.bashrc
echo "alias pip=pip3.12" >> ~/.bashrc  # 注：pip3.12 路徑可能不同，視情況調整
source ~/.bashrc
```

確認清單：
- [ ] `python3.12 --version` 顯示 3.12.x
- [ ] `pip --version` 正常

---

## Step 4：上傳程式碼與資料

### 4-1 從 GitHub clone 程式碼

```bash
# 在 VM 上執行
cd ~
git clone https://github.com/<你的帳號>/ultra-trader-src.git
cd ultra-trader-src

# 安裝 Python 依賴
pip install -r requirements.txt
```

> 若尚未把程式碼推上 GitHub，先在本機執行：
> ```bash
> git init && git add . && git commit -m "init"
> git remote add origin https://github.com/<帳號>/<repo>.git
> git push -u origin main
> ```

確認清單：
- [ ] `git clone` 完成
- [ ] `pip install -r requirements.txt` 無 Error
- [ ] `python -c "import shioaji; print(shioaji.__version__)"` 正常輸出

---

### 4-2 上傳大型資料檔（本機 → VM）

```bash
# 在【本機】執行，將 data/historical/ 上傳到 VM
gcloud compute scp --recurse \
  "C:\Users\xx\Desktop\永豐-自動化交易\ultra-trader-src\data\historical" \
  ultratrader-night:~/ultra-trader-src/data/ \
  --zone=asia-east1-b
```

或用 **rsync**（若已設 SSH key）：
```bash
rsync -avz --progress \
  data/historical/ \
  <VM外部IP>:~/ultra-trader-src/data/historical/
```

確認清單：
- [ ] `tmf_5y_1m.parquet` 已上傳（約 200MB）
- [ ] `data/historical/ml/TMF_night_5m.parquet` 已上傳
- [ ] `data/historical/ml/TMF_night_features.parquet` 已上傳
- [ ] 在 VM 確認：`ls -lh ~/ultra-trader-src/data/historical/`

---

### 4-3 上傳永豐憑證

```bash
# 在【本機】執行
gcloud compute scp \
  "C:\ekey\551\<身分證號>\S\<憑證>.pfx" \
  ultratrader-night:~/ultra-trader-src/certs/cert.pfx \
  --zone=asia-east1-b
```

確認清單：
- [ ] `~/ultra-trader-src/certs/cert.pfx` 存在
- [ ] 憑證檔案大小合理（通常 2~10KB）

---

## Step 5：修正路徑（Windows → Linux 相對路徑）

這是唯一需要改程式碼的地方。在 VM 上執行：

```bash
cd ~/ultra-trader-src
grep -rn "C:\\\\Users\|C:/Users\|Desktop" --include="*.py" | grep -v ".pyc"
```

確認有哪些檔案需要修改，然後逐一修正：

### core/engine.py

```bash
# 確認目前 PROJECT_ROOT 定義
grep -n "PROJECT_ROOT" core/engine.py
```

若有硬碼路徑：
```python
# 修改前（Windows 絕對路徑）
PROJECT_ROOT = Path("C:/Users/xx/Desktop/永豐-自動化交易/ultra-trader-src")

# 修改後（相對路徑，自動偵測）
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
```

### .env 憑證路徑

```bash
# 修改 .env 裡的憑證路徑
sed -i 's|C:.*cert.pfx|/root/ultra-trader-src/certs/cert.pfx|g' .env
```

確認清單：
- [ ] `grep -r "C:\\\\" --include="*.py"` 沒有任何輸出（無殘留 Windows 路徑）
- [ ] `python -c "from core.engine import TradingEngine; print('OK')"` 無 ImportError

---

## Step 6：設定 .env 環境變數

```bash
cd ~/ultra-trader-src
nano .env
```

確認以下欄位正確：

```ini
# 交易模式（先用 paper，確認正常再改 live）
TRADING_MODE=paper
INITIAL_BALANCE=<你的帳戶實際權益>

# 永豐 API
SHIOAJI_API_KEY=<你的 API Key>
SHIOAJI_SECRET_KEY=<你的 Secret Key>
SHIOAJI_CERT_PATH=/root/ultra-trader-src/certs/cert.pfx
SHIOAJI_CERT_PASS=<憑證密碼>

# Telegram 通知
TELEGRAM_BOT_TOKEN=<Bot Token>
TELEGRAM_CHAT_ID=<Chat ID>

# 時區
TZ=Asia/Taipei
```

存檔：`Ctrl+O` → Enter → `Ctrl+X`

確認清單：
- [ ] `TRADING_MODE=paper`（切實單前不要改）
- [ ] API Key 與 Secret 已填入
- [ ] 憑證路徑為 Linux 格式（`/root/...`）
- [ ] TG 通知 token 已填入

---

## Step 7：設定 crontab 排程

取代 Windows Task Scheduler，用 Linux crontab：

```bash
crontab -e
# 選擇編輯器（選 1 = nano）
```

加入以下排程（台灣時間 UTC+8，GCP VM 預設 UTC，需 -8）：

```cron
# UltraTrader 夜盤 ORB — 每天 14:55 台灣時間重啟 server
# 14:55 TST = 06:55 UTC
55 6 * * * /root/ultra-trader-src/scripts/restart_night.sh >> /root/ultra-trader-src/data/logs/cron.log 2>&1

# 每天 05:10 台灣時間關閉夜盤（04:00 強制平倉後，等 K 棒結算）
# 05:10 TST = 21:10 UTC（前一天）
10 21 * * * pkill -f "api.server.*8889" >> /root/ultra-trader-src/data/logs/cron.log 2>&1
```

建立啟動腳本：

```bash
cat > ~/ultra-trader-src/scripts/restart_night.sh << 'EOF'
#!/bin/bash
cd /root/ultra-trader-src

# 設定時區
export TZ=Asia/Taipei

# 停止舊 server
pkill -f "api.server.*8889" 2>/dev/null
sleep 3

# 啟動新 server（背景執行）
nohup python3.12 -m api.server --mode night --port 8889 \
  >> data/logs/ultratrader_$(date +%Y%m%d).log 2>&1 &

echo "[$(date)] Server restarted, PID=$!"
EOF

chmod +x ~/ultra-trader-src/scripts/restart_night.sh
```

確認清單：
- [ ] `crontab -l` 確認排程已加入
- [ ] 手動執行一次：`bash ~/ultra-trader-src/scripts/restart_night.sh`
- [ ] `ps aux | grep api.server` 確認 server 在跑

---

## Step 8：防火牆設定

### GCP 防火牆（Console 設定）

GCP Console → **VPC 網路** → **防火牆** → **建立防火牆規則**：

| 規則 | 設定 |
|------|------|
| 名稱 | `block-trading-ports` |
| 方向 | 輸入 |
| 動作 | 拒絕 |
| 目標 | 所有執行個體 |
| 來源 IP | `0.0.0.0/0` |
| 通訊協定/Port | TCP: 8888, 8889, 3456 |

> 這樣 API server port 只有 VM 自己能存取，外部無法直接打入。

### VM 內部防火牆（ufw）

```bash
sudo apt install -y ufw

# 只允許 SSH（GCP 需要保留 22）
sudo ufw allow 22/tcp
sudo ufw allow from 127.0.0.1 to any port 8888,8889

# 啟用
sudo ufw enable
sudo ufw status
```

確認清單：
- [ ] Port 8888、8889 外部無法存取
- [ ] Port 22 (SSH) 仍可連線
- [ ] 從外部測試：`curl http://<VM IP>:8889/api/health` → 連線被拒（正確）

---

## Step 9：Paper Trading 驗證

```bash
# 手動啟動（第一次測試）
cd ~/ultra-trader-src
python3.12 -m api.server --mode night --port 8889 &

# 查看 log（即時）
tail -f data/logs/ultratrader_$(date +%Y%m%d).log
```

等待以下 log 出現（依時間）：

```
14:55  [Engine] trading_mode=paper 連線成功
21:30  [ORB] Session 開始，等待區間建立...
22:15  [ORB] 區間建立 High=XXXXX Low=XXXXX Width=XXX
22:xx  [ORB] 訊號觸發 LONG/SHORT @ XXXXX
04:00  [ORB] 盤末強制平倉
```

確認清單：
- [ ] Shioaji 登入成功（無 `not allow` 錯誤）
- [ ] TG 收到「Server 啟動」通知
- [ ] 22:15 後有 ORB 區間建立 log
- [ ] 至少 1 筆 Paper 交易完整記錄（進場→出場→損益）
- [ ] `cat data/risk_state_night.json` peak_equity ≈ INITIAL_BALANCE

連續正常運行 **7 天**後進行 Step 10。

---

## Step 10：切換實單

```bash
# 編輯 .env
nano ~/ultra-trader-src/.env
```

修改：
```ini
TRADING_MODE=live
INITIAL_BALANCE=<真實帳戶餘額>
```

重啟 server：
```bash
bash ~/ultra-trader-src/scripts/restart_night.sh
```

驗證：
```bash
# 確認 trading_mode=live
curl -s http://localhost:8889/api/state | python3 -m json.tool | grep trading_mode
```

確認清單：
- [ ] Paper 模式已連續正常 7 天
- [ ] `risk_state_night.json` 已確認無殘留 peak_equity
- [ ] `trading_mode=live` 出現在 log
- [ ] TG 收到「實單模式啟動」通知
- [ ] 第一筆進場後，永豐 App 確認成交回報

---

## 日常維運指令

```bash
# 查看即時 log
tail -f ~/ultra-trader-src/data/logs/ultratrader_$(date +%Y%m%d).log

# 查看 ORB 訊號
grep "\[ORB\]" ~/ultra-trader-src/data/logs/ultratrader_$(date +%Y%m%d).log

# 查看風控狀態
cat ~/ultra-trader-src/data/risk_state_night.json

# 確認 server 在跑
ps aux | grep api.server

# 手動重啟
bash ~/ultra-trader-src/scripts/restart_night.sh

# 緊急平倉
curl -X POST http://localhost:8889/api/close_all

# 緊急切回 paper
sed -i 's/TRADING_MODE=live/TRADING_MODE=paper/' .env
bash ~/ultra-trader-src/scripts/restart_night.sh
```

---

## 費用明細

| 項目 | 費用/月 | 說明 |
|------|---------|------|
| e2-small VM | ~NT$350 | 2 vCPU, 2GB RAM |
| Persistent Disk 50GB | ~NT$100 | 標準 SSD |
| 外部 IP（靜態）| ~NT$30 | 固定 IP |
| 網路流量 | ~NT$10 | 出口流量極小 |
| **合計** | **~NT$490/月** | 約 $16 USD |

> GCP 新帳號有 **$300 免費額度**（90 天），可以先免費試跑 3 個月。

---

## 其他平台對照（參考）

| 平台 | 地點 | 延遲 | 月費 | OS | 推薦度 |
|------|------|------|------|----|--------|
| **GCP asia-east1** | **台灣彰化** | **<5ms** | **NT$490** | Linux | ⭐⭐⭐⭐⭐ |
| Azure Taiwan North | 台灣台北 | <5ms | NT$900~1,700 | Windows | ⭐⭐⭐⭐ |
| Zeabur | 日本Tokyo | ~40ms | NT$900~1,200 | Linux | ⭐⭐⭐ |
| Vultr Tokyo | 日本Tokyo | ~40ms | NT$380 | Windows | ⭐⭐ |

---

*最後更新：2026-05-03 | UltraTrader Phase 5 | TMF 夜盤 ORB 策略*
