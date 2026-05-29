# 🔧 [CANCELLED] 5/20 21:00 SDK upgrade playbook — 整套作廢

## ⛔ 為什麼整套 playbook 不執行了

**1. rshioaji 1.5.13 不存在**
- 5/20 下午查 sinotrade.github.io/release/ 確認 shioaji 1.3.3 是最新版（2026-04-08）
- 1.4.x / 1.5.x 從未發佈
- Discord expert 第 1 次回覆「升 1.5.13」是 hallucination、第 2 次自己推翻

**2. SDK 1.3.3 沒事、真兇是 list_positions 401**
- min-verify (noop callback) 跑 5 分鐘穩 ✅
- 12-round setter bisect 全 SURVIVED ✅
- 真兇定位：`api.list_positions()` 撞 401（API key 沒簽帳務查詢權限）
  → SDK 內部 401 handler 起 thread disconnect session
  → main thread 用舊 session pointer = use-after-free GPF at ip:0x580ec2
- 5/20 18:26 patch（disable engine.py 內 reconcile + init real_positions）後 live process 10+ 分鐘全程穩定

**3. 真正的修法（已 deploy）**
- `core/engine.py:463` + `core/engine.py:1445` 兩處 `if False and ...` 暫時 disable `get_real_positions()` 呼叫
- 等永豐 API 管理頁面簽好帳務查詢權限後、再 enable list_positions（且要改成單 thread 呼叫）

**4. 完整 incident timeline 見**
- `5_20_GO_LIVE_PLAYBOOK.md` 結尾的「事後紀錄」section
- memory: [[shioaji-1-3-3-live-callback-race]]（檔名雖叫 race、內容已修正：真因是 401）

---

> 以下保留原 playbook 內容作歷史紀錄、**勿執行**。

---

# 原 playbook（已作廢）

> 5/20 早上切 live 撞 callback table corruption（_engine_loop / dict / tuple 在同 slot）。
> Discord expert 第二次回覆：paper 4 天 OK = SDK 沒事、可能我們 callback body 有 bug。
> 建議 **先做 min-verify（noop callback）排除**、再決定要不要升 SDK。

## ⚡ 21:00 Phase 1: Min-verify（10 分鐘）

跑 noop callback 版 live mode、看是否還 crash：

```bash
# kill 跑中 process
ssh ultratrader-night "pkill -f 'scripts/start.py' 2>/dev/null; pkill -f 'paper_night_orb' 2>/dev/null; sleep 3"

# 上傳 min-verify script
wsl rsync -avz -e "ssh -i ~/.ssh/google_compute_engine -o IdentitiesOnly=yes" \
  /mnt/c/Users/xx/Desktop/vps永豐微台指/scripts/_min_verify_live.py \
  xx@35.221.239.245:/tmp/

# 執行（會跑 5 分鐘）
ssh ultratrader-night "cd ~/TMFtrader-src && source .venv/bin/activate && python3 /tmp/_min_verify_live.py 2>&1 | tail -20"
```

**判讀**：
- ✅ 跑完 5 分鐘無 crash、結束印「NOOP CRASH」=> **我們 callback body 有 bug、不用升 SDK、修 broker.py**
- ❌ 5 分鐘內 process crash + `terminate called`=> SDK 真有問題、進 Phase 2 升 SDK

---

## 📦 21:10 Phase 2: SDK 升級（min-verify crash 才走這條）

---

## 21:00 前確認

- [ ] 永豐 App 期貨可用保證金 ≥ 90K
- [ ] SSH 連 VPS 可用：`wsl ssh -i ~/.ssh/google_compute_engine xx@35.221.239.245`
- [ ] 今夜 5/20 ORB session 21:30 即將開始、SDK 升級要在 21:30 前完成、不然 ORB 起來會撞舊 SDK

---

## 21:00 — kill all process + backup

```bash
ssh ultratrader-night
cd ~/TMFtrader-src

# Kill 跑中 process（paper start.py + paper_night_orb）
pkill -f 'scripts/start.py' 2>/dev/null
pkill -f 'paper_night_orb.py' 2>/dev/null
sleep 5
ps -ef | grep -E "start.py|paper_night_orb" | grep -v grep || echo "✅ 無 process"

# 確認 watchdog cron 仍 disabled（避免升級期間 watchdog 拉舊版）
crontab -l | grep watchdog
# 應該看到 # EMERGENCY...

# Backup current shioaji wheel（萬一 rollback）
.venv/bin/pip freeze | grep -iE "shioaji|pysolace" > /tmp/sdk_versions_before.txt
cat /tmp/sdk_versions_before.txt
# 應該看到 shioaji==1.3.3
```

---

## 21:05 — pip install rshioaji

```bash
cd ~/TMFtrader-src
source .venv/bin/activate

# 卸舊版（rshioaji 會占 shioaji namespace、避免衝突）
pip uninstall -y shioaji

# 裝新版
pip install rshioaji==1.5.13

# 驗證
python3 -c "import shioaji; print(shioaji.__version__)"
# 預期：1.5.13 或類似
```

---

## 21:10 — 部署預改的 broker.py（decorator → setter）

```bash
# 本機已 commit 預改版本、rsync 上 VPS
# （從你電腦執行）
wsl rsync -avz -e "ssh -i ~/.ssh/google_compute_engine -o IdentitiesOnly=yes" \
  /mnt/c/Users/xx/Desktop/vps永豐微台指/TMFtrader-src/core/broker.py \
  xx@35.221.239.245:/home/xx/TMFtrader-src/core/broker.py

# VPS 上驗證 md5（本機/VPS 應一致）
md5sum /home/xx/TMFtrader-src/core/broker.py
```

---

## 21:15 — test paper 模式（先驗 SDK 跟既有邏輯不撞）

```bash
ssh ultratrader-night
cd ~/TMFtrader-src

# 確認 .env 是 paper
grep TRADING_MODE .env
# 應該是 TRADING_MODE=paper

# 起 process
bash scripts/restart_day.sh
sleep 30

# 看是否能成功 init
ps -ef | grep start.py | grep -v grep
grep -E "Mode|Contract|Subscribe|KbarPoller|Heartbeat" data/logs/TMFtrader_$(date +%Y%m%d).log | tail -10

# 觀察 5 分鐘看是否 die
sleep 300
ps -ef | grep start.py | grep -v grep
# 還在跑 = paper 模式 OK ✅
# 死了 = 升級失敗、立刻 rollback（見最後）
```

**驗證點**：5 分鐘內 process 不 die、log 沒「pybind11 error_already_set」。

---

## 21:20 — 試切 live

```bash
ssh ultratrader-night
cd ~/TMFtrader-src

# kill paper
pkill -f scripts/start.py
sleep 5

# 切 live
sed -i 's/^TRADING_MODE=paper/TRADING_MODE=live/' .env
sed -i 's/^INITIAL_BALANCE=.*/INITIAL_BALANCE=125000.0/' .env
grep -E "TRADING_MODE|INITIAL_BALANCE" .env

# 起 process
bash scripts/restart_day.sh
sleep 30

# 看 [Mode] LIVE 載入
grep "\[Mode\]" data/logs/TMFtrader_$(date +%Y%m%d).log | tail -3
# 應該看到 [Mode] LIVE trading

# 觀察 5 分鐘
sleep 300
ps -ef | grep start.py | grep -v grep
# 還在跑 = live 模式 OK ✅
# 死了 = SDK 升級沒解、繼續 rollback
```

---

## 21:25 — 解除 watchdog disable + 確認新 SDK 通過

```bash
ssh ultratrader-night

# 解除 EMERGENCY DISABLED
crontab -l | sed 's|^# EMERGENCY[^*]*\(\* \* \* \* \* /home/xx/.*vps_watchdog.sh.*\)$|\1|' | crontab -

# 驗證
crontab -l | grep watchdog
# 應該看到 * * * * * /home/xx/... 不再有 # 開頭

# 即時 quota 量測
.venv/bin/python3 scripts/log_quota.py
# 預估 ~150-200 MB (今早 spam 燒過、剩餘 dale night session)
```

---

## 21:30 — 夜盤 ORB cron auto-start

cron `55 6 * * 1-5` 已過、今晚 ORB 不會 cron 自動起。要手動起：

```bash
ssh ultratrader-night
bash ~/TMFtrader-src/scripts/restart_night.sh
sleep 10
ps -ef | grep paper_night_orb | grep -v grep
```

或：今晚就讓 ORB 跳過、明早 5/21 14:55 cron 再正常起。

---

## 22:00 — 持續監控

- [ ] start.py 還活著（live 模式不死）
- [ ] paper_night_orb 跑著（如果手動起）
- [ ] 沒「pybind11 error_already_set」
- [ ] 沒「[Cron] 日盤」spam（watchdog 不該拉起新 PID）
- [ ] TG 安靜

---

## 5/21 早上 08:35 起床

- [ ] TG 看到「🔄 [Cron] 日盤 start.py 排程重啟」（cron 自然觸發）
- [ ] log [Mode] LIVE trading 確認
- [ ] 日盤開始 evaluate live 訊號

---

## 🚨 Rollback Plan（升級失敗）

若 SDK 1.5.13 也撞 crash、或其他不可預期問題：

```bash
ssh ultratrader-night
cd ~/TMFtrader-src
source .venv/bin/activate

# 卸 rshioaji
pip uninstall -y rshioaji

# 裝回 shioaji 1.3.3
pip install shioaji==1.3.3

# 回 paper 模式（不要繼續嘗試 live）
sed -i 's/^TRADING_MODE=live/TRADING_MODE=paper/' .env

# rsync 回原版 broker.py（含 decorator）
# 從本機：
wsl ssh -i ~/.ssh/google_compute_engine xx@35.221.239.245 'cd ~/TMFtrader-src && git checkout HEAD~1 -- core/broker.py'

# 重啟
bash scripts/restart_day.sh
sleep 30
grep "\[Mode\]" data/logs/TMFtrader_$(date +%Y%m%d).log | tail -3
# 應該回到 [Mode] paper

# 重新 disable watchdog（不該繼續嘗試 live）
crontab -l | sed 's|^\(\* \* \* \* \* /home/xx/.*vps_watchdog.sh.*\)$|# EMERGENCY DISABLED \1|' | crontab -
```

---

## 緊急聯絡

- Shioaji Discord：[Shioaji Agent X API](https://discord.gg/5nzmWCTnG7) - 找 victoryang
- 永豐 SJ 客服：sj.agent@sinopac.com

---

## 預期時程

```
21:00  kill process + backup
21:05  pip install rshioaji 1.5.13
21:10  rsync 新 broker.py
21:15  paper test 5 分鐘
21:20  switch live test 5 分鐘
21:25  解除 watchdog
21:30  夜盤 ORB（手動起 or 跳過）
21:35  全部就緒、5/21 08:30 cron 自然延續 live
```

整套 30-35 分鐘、如 21:00 開始、21:35 前完成。

夜盤 21:30 ORB session 不太可能撞 callback race（ORB 用獨立 paper_night_orb.py、不像 start.py 跑 engine_loop）、所以 ORB 跑舊 SDK 不會 crash。但 ORB 也升 SDK 比較乾淨。
