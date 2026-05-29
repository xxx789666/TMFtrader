# 永豐-自動化交易 — 已部署策略

> ⚠️ **這個資料夾是 backup 副本** — 真實運行的檔案在 `TMFtrader-src/` 下。
> 修改這裡的檔案**不會**自動同步回去。改完要手動 copy 到 source of truth 才會生效。

最後同步：2026-04-16

---

## 🎯 當前運行策略

| 策略 | 商品 | 模式 | 狀態 |
|------|------|------|------|
| **TMF Breakout**（ATR 壓縮突破）| TMF（微型台指期近月 TMFR1）| Paper Trading | 運行中 |

- **券商**：永豐金證券（Shioaji API）
- **資金**：NT$ 200,000（模擬）
- **時段**：日盤 08:45–13:45 + 夜盤 15:00–05:00
- **部署機**：本機 Windows（port 8888）

---

## 📁 資料夾結構

```
deployed_strategies/
├── README.md                          ← 你正在讀
├── memory.md                          ← 變更紀錄（每次改動在此留痕）
│
├── tmf_breakout/                      ← 策略檔案
│   ├── breakout.py                    主策略（ATR 壓縮 + ADX 突破）
│   ├── base.py                        策略基底類別
│   ├── filters.py                     市場狀態分類器（RegimeClassifier）
│   └── 策略說明.md                    策略規則文件
│
├── automation/                        ← 自動化 + 自癒腳本
│   ├── start.py                       主 server 啟動腳本（FastAPI + uvicorn）
│   ├── watchdog.py                    自癒看門狗（60s 輪詢，斷線自動重啟 + TG 通知）
│   ├── restart_trader.bat             手動重啟 trader
│   └── start_watchdog.bat             手動啟動 watchdog
│
└── startup/                           ← 開機自啟（Windows Startup folder）
    ├── TMFtrader-Server.bat         開機自啟 server
    └── TMFtrader-Watchdog.bat       開機自啟 watchdog
```

---

## 🔄 真實檔案位置（source of truth）

如果此資料夾與下面不一致，**以下面為準**：

### 策略原始碼
```
TMFtrader-src/strategy/breakout.py
TMFtrader-src/strategy/base.py
TMFtrader-src/strategy/filters.py
```

### 自動化腳本（實際在跑的）
```
TMFtrader-src/scripts/start.py       ← port 8888 主 server
TMFtrader-src/scripts/watchdog.py    ← 自癒看門狗
```

### 開機自啟（真正被 Windows 執行的）
```
C:\Users\xx\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\TMFtrader-Server.bat
C:\Users\xx\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\TMFtrader-Watchdog.bat
```

### 策略說明
```
策略說明.md                             ← 專案根目錄
```

---

## ⚙️ 自癒機制（watchdog.py）

每 60 秒輪詢一次，負責：

| 檢查項目 | 觸發條件 | 動作 |
|---------|---------|------|
| 伺服器在線 | GET /api/state 失敗 | 自動重啟 server |
| 熔斷器狀態 | state ≠ active（非交接期） | 自動呼叫 /api/engine/resume |
| Solace Tick | fallback_active = true | 首次發 TG 斷線通知；恢復時發 TG 恢復通知 |
| Fallback 超時 | fallback > 10 分鐘 | 自動重啟 server（持倉時跳過） |
| Scan 心跳 | [Scan] log > 12 分鐘未更新 | 自動重啟 server（持倉時跳過） |

**安全限制**：
- 收盤前窗口不重啟（13:25–13:45、04:45–05:00）
- 重啟冷卻：120 秒
- 持倉中的 scan 超時不重啟

**通知管道**：Telegram Bot（token / chat_id 從 `.env` 讀 `TG_BOT_TOKEN` + `TG_CHAT_ID`，禁止入 git），時間戳使用 **UTC+8**。

---

## 🚀 操作指令

### 啟動 server（手動）
```bash
cd TMFtrader-src
nohup python scripts/start.py --no-browser > /tmp/trader.log 2>&1 &
```

### 啟動 watchdog（手動）
```bash
cd TMFtrader-src
nohup pythonw scripts/watchdog.py > /dev/null 2>&1 &
```

### 正確停止 server（Windows Git Bash）
```bash
# 1. 找 PID
netstat -ano | grep ":8888"
# 2. 用絕對路徑 + 雙斜線避開 Git Bash 路徑轉換
/c/Windows/System32/taskkill.exe //F //PID <pid>
```

### Dashboard
http://localhost:8888

---

## 📝 變更紀錄

所有改動請記在 [`memory.md`](./memory.md)。格式見該檔案開頭範本。
