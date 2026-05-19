# 🚀 5/20 (週三) 切實單 — 早上照念版

> 直接從上到下執行、每一步都有指令 + 驗證點。看到 ✅ 才進下一步。

---

## 7:30 起床、先準備

- [ ] 永豐 App 已開、密碼能登入
- [ ] 手機在身邊
- [ ] 筆電 SSH 已連 VPS：
      ```
      wsl ssh -i ~/.ssh/google_compute_engine xx@35.221.239.245
      ```

---

## 7:40 — 入金（如還沒做）

打開永豐 App → 期貨 → 出入金 → 從證券帳戶或銀行帳戶轉到「**期貨保證金**」

最低建議：
- 跑 1 口微台：**35,000-40,000 NTD**
- 跑 1 口小台：**70,000-80,000 NTD**

**驗證**：永豐 App → 期貨 → 帳戶 → 「**可用保證金**」 ≥ 你要的金額 → ✅

---

## 7:50 — SSH 上 VPS、改 .env

```bash
# 已 ssh 進去後跑：
cd ~/ultra-trader-src

# 備份 paper 版（之後若要切回 paper 用）
cp .env .env.backup-paper

# 改 TRADING_MODE
sed -i 's/^TRADING_MODE=paper/TRADING_MODE=live/' .env

# 改 INITIAL_BALANCE（用你實際可用保證金）
# 用 nano 編輯、找到 INITIAL_BALANCE=222890.0 改成你的數字
nano .env
# 例如 60000：INITIAL_BALANCE=60000.0
# Ctrl+O 存、Enter、Ctrl+X 離開
```

**驗證**：
```bash
grep -E "TRADING_MODE|INITIAL_BALANCE" .env
```
應該看到：
```
TRADING_MODE=live
INITIAL_BALANCE=60000.0   ← 你的實際金額
```
→ ✅

---

## 8:00 — 早盤前最後檢查

```bash
# 1. quota 是否 reset
/home/xx/ultra-trader-src/.venv/bin/python3 /home/xx/ultra-trader-src/scripts/log_quota.py
# 應該看到 bytes ≈ 0 MB / remaining ≈ 500 MB ← 必須是

# 2. watchdog cron 是否啟用
crontab -l | grep watchdog
# 應該看到「* * * * * /home/xx/ultra-trader-src/scripts/vps_watchdog.sh」（不該有 # 開頭）

# 3. broker.py 修法 D + A + C 載入
md5sum /home/xx/ultra-trader-src/core/broker.py
# 應該是 4ca8cd23f86a71bcbcddcd4cea1ff864 (或更新)

# 4. circuit_breaker 修法 1+2+3 載入
md5sum /home/xx/ultra-trader-src/risk/circuit_breaker.py
# 應該是 69b15ed3d0e5e2035bd6a54ef6501987 (或更新)
```

任一驗證失敗 → 暫停操作、確認問題再繼續。

---

## 8:25 — 等 cron 自動觸發（或手動）

**選 A：等 cron**（08:30 自動觸發、推薦）
```bash
# 不做任何事、等手機 TG
```

**選 B：手動立刻起**（如不想等 5 分鐘）
```bash
bash ~/ultra-trader-src/scripts/restart_day.sh
```

---

## 8:30-8:35 — 開盤前驗證

### TG 應該收到
- 🔄 [Cron] 日盤 start.py 排程重啟 PID=XXXXXX  ✅

### SSH 看 log
```bash
LOG=/home/xx/ultra-trader-src/data/logs/ultratrader_$(date +%Y%m%d).log

# 驗證 live 模式載入
grep -E "Mode.*live|Mode.*paper" $LOG | tail -3
# 應該看到「[Mode] live (real orders enabled)」← 必須是 live、不是 paper
```

如果 log 顯示 `[Mode] paper` → 緊急停止：
```bash
pkill -f start.py
# 確認 .env 真的改了 TRADING_MODE=live、然後重啟
```

### API 即時驗證
```bash
curl -s http://localhost:8888/api/state | python3 -m json.tool | grep -E "engine_state|trading_mode"
# 應該看到：
#   "engine_state": "running"
#   "trading_mode": "live"   ← 必須
```

→ ✅ 通過 → live 模式 已上線 🚀

---

## 8:45 開盤 → 13:45 收盤 — 守候第一筆

### 看 TG 的訊息（按重要度）

| 訊息類型 | 怎麼做 |
|---|---|
| **[Scan] 持續推**（沒設、log 才有）| 正常 |
| **「📥 [LIVE] 進場」** | **馬上開永豐 App 三方對齊**（見下）|
| **「✅ [LIVE] 出場」** | **馬上開永豐 App 確認平倉**（見下）|
| **「⚠️ 價格劇烈異常」** | 60 秒後自動恢復、不用動 |
| **「🚨 [Sinopac-Live] 券商連線中斷」** | broker 真斷、SSH 看 log、可能要 restart |
| **「🔧 [VPS Watchdog] start.py 重啟成功」** | watchdog 自癒、看 quota 是否 OK |

### 第一筆 [LIVE] 進場時、必檢三方對齊

**TG 訊息**：
```
[LIVE] 進場
TMF 做多 ▲ x1
進場價: 41000
停損: 40800（200pt）
原因: A-Squeeze LONG atr_ratio=1.05 adx=28 +DI=32
時間: 10:25:00
```

**永豐 App**（同時看）：
- 期貨 → 成交回報 → 應有 1 筆新成交、價格 41000 (容差 1-2 tick)
- 期貨 → 持倉 → TMF 多單 1 口、進場價 41000

**SSH 看 log**：
```bash
grep notify_entry $LOG | tail -1
```
應該看到 `notify_entry("live", "TMF", "BUY", 41000.0, 1, 40800.0, "...")`

**三方一致 → 繼續持有**
**任一不一致 → 緊急退場（見下）**

---

## 緊急退場 SOP（任何時刻可用）

### A. 切回 paper（最溫和）
```bash
ssh ultratrader-night "sed -i 's/TRADING_MODE=live/TRADING_MODE=paper/' ~/ultra-trader-src/.env && bash ~/ultra-trader-src/scripts/restart_day.sh"
```

### B. 全部平倉（緊急、強平）
```bash
ssh -L 8889:localhost:8889 -i ~/.ssh/google_compute_engine xx@35.221.239.245 &
curl -X POST http://localhost:8889/api/close_all
```

### C. 完全停機（最徹底）
```bash
ssh ultratrader-night "pkill -f 'start.py'; pkill -f 'paper_night_orb.py'"
# 也禁用 cron 避免自動重啟：
ssh ultratrader-night "crontab -l | sed 's/^\(30 0 .* restart_day\)/# \1/; s/^\(55 6 .* restart_night\)/# \1/' | crontab -"
```

### D. 永豐 App 手動平倉（最終保險、24h 都可用）
打開永豐 App → 期貨 → 持倉 → 選你的持倉 → 平倉
**不依賴 VPS / 程式、直接斷退場路徑**

---

## 第一筆完成後 — 強制 5 分鐘觀察期

第一筆 entry + exit 完成後：

```bash
ssh ultratrader-night "pkill -f 'start.py'"
```

**手動停 5 分鐘**、確認：
- [ ] 永豐 App 持倉 = 0
- [ ] 永豐 App 已實現損益 = TG 訊息內的 PnL（容差幾元手續費）
- [ ] log / TG / App 三方一致
- [ ] 心情上 OK

5 分鐘後沒問題 → 重啟：
```bash
ssh ultratrader-night "bash ~/ultra-trader-src/scripts/restart_day.sh"
```

---

## 全日監看時點（自動推 TG、你只要看手機）

| 時間 | 應該看到 |
|---|---|
| 09:05 | log_quota cron 量測（若 > 100 MB 警示）|
| 13:45 | 日盤收盤、修法 D 應靜默 |
| **13:50** | TG「📊 [日盤日報]」、看 trades 數 |
| 14:55 | TG「🌙 [Cron] 夜盤 paper_night_orb 排程重啟」|
| 21:30 | ORB session 啟動 |
| 22:00 / 04:30 | log_quota cron |
| 05:10 | cron pkill ORB |
| **05:15** | TG「🌙 [夜盤日報]」|

---

## 5/20 收盤前的 review

- [ ] 永豐 App 對帳：本日成交 + PnL 跟 daily JSON 一致
- [ ] `data/performance/daily/2026-05-20.json` 的 trades 陣列、`trading_mode=live`
- [ ] 全日 quota < 200 MB（修法 D 持續有效）
- [ ] 無「真斷線」TG

---

## 5/21 起每日例行（最簡）

每天早上開機後看手機 TG：
- 08:30 「🔄 [Cron] 日盤」 → ✅ 自動跑著
- 13:50 「📊 [日盤日報]」 → 看當日 trades 數 + PnL
- 14:55 「🌙 [Cron] 夜盤」 → ORB 起
- 05:15 隔日「🌙 [夜盤日報]」

**沒事不要 SSH**——一切靠 cron 自動跑。

---

## 心理校準

- **5/20 一整天可能 0 trade、完全正常**（backtest 月均 2-3 筆）
- **第一週 0-1 筆 = 正常範圍**
- **第一個月 1-3 筆 = 正常範圍**
- **連續 2 個月 0 筆 = 異常、再 review**

---

## 重要資料（萬一忘）

```
VPS:           xx@35.221.239.245
SSH key:       ~/.ssh/google_compute_engine
期貨帳號:       2066213 (broker F002000)
戶名:           徐安利
API key 名:    SHIOAJI_API_KEY (in .env)
CA path:       /home/xx/ultra-trader-src/certs/cert.pfx
本機 repo:     C:\Users\xx\Desktop\vps永豐微台指
TG bot:        金秘書（你的私人 channel）
```

---

## 出問題的 P0 聯絡

1. 永豐 SJ 客服：sj.agent@sinopac.com
2. Shioaji Discord：[Shioaji Agent X API](https://discord.gg/5nzmWCTnG7)
3. 緊急時手機開永豐 App 手動平倉（最終保險）
