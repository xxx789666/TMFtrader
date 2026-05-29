# deployed_strategies 變更紀錄（Changelog）

> 專門記錄 `deployed_strategies/` 資料夾內所有檔案的變動。
> 每次改動請新增條目，**最新在最上**。
> 包含：策略參數、腳本修改、自癒邏輯、部署版本、重要觀察、問題診斷。

---

## 📝 記錄格式範本

```markdown
### YYYY-MM-DD HH:MM — 簡短標題
- **動機**：為什麼要改？（市場事件、回測發現、bug、合規需求…）
- **改動**：具體改了什麼（參數 A=X→Y、加了哪段 code、刪了什麼）
- **檔案**：影響的檔案路徑（deployed + source of truth 兩邊）
- **驗證**：怎麼確認改完是對的（log 訊息、TG 通知、實盤觀察）
- **狀態**：✅ 已驗證 / 🟡 觀察中 / 🔴 已回退
```

---

## 🔵 TMF Breakout 策略（當前運行）

### 當前部署參數（2026-04-18 v6b）

**策略參數**（breakout.py Mode A：ATR 壓縮突破）：
| 參數 | 值 | 說明 |
|------|----|------|
| squeeze_ratio | 0.9 | ATR 壓縮門檻（ratio < 0.9 視為壓縮）|
| expand_ratio | 1.18 | ATR 擴張門檻（突破後需 ratio ≥ 1.18）|
| squeeze_grace_bars | 1 | 壓縮結束後的寬限根數（1=等同原始行為）|
| min_adx | 23.0 | 基本 ADX 門檻（v6b: 20→23，穩健性測試確認）|
| afternoon_min_adx | 30.0 | 日盤 11:00 後 ADX 門檻（v6b: 32→30，避免尖峰過擬合）|
| sl_atr | 2.5 | 停損 = 2.5 × ATR |
| tp_atr | 10.0 | 停利 = 10.0 × ATR |
| trail_trigger_atr | 1.2 | 追蹤停利啟動門檻 |
| trail_dist_atr | 1.25 | 追蹤停利距離 |
| max_bars | 80 | 最大持倉 K 棒數 |
| early_cut_bars | 40 | 提早砍停 K 棒數（v6: 30→40，提高 WR）|
| early_cut_loss_atr | 1.5 | 提早砍停虧損門檻 |

**v6b 回測績效**（2024-01~2026-04，5min日盤，200k初始）：
PF=2.176 · WR=61.5% · MaxDD=7.3% · 52筆 · net=+113,190
Walk-Forward: Train PF=1.983 / Test PF=2.080（泛化良好）

**交易/風控**：
| 項目 | 值 |
|------|----|
| 商品 | TMF（TMFR1 近月）|
| 交易模式 | **paper**（模擬單，真實行情）|
| 初始資金 | NT$ 200,000 |
| 風險 Profile | tmf_3x（每筆 4%、最多 3 口）|
| 日盤 | 08:45–13:45 |
| 夜盤 | 15:00–05:00（跨午夜）|

---

## 🗂️ 版本與變更歷史
### 2026-04-29 日盤 — 自癒流程診斷 + ADX 不足無進場
- **動機**：日盤 engine=error，watchdog「治癒系統失效」疑問
- **根因**：Day server (PID 170812) 是 4/28 14:31 以舊 API Key 啟動，Shioaji 登入失敗，engine 永遠卡 error，不自癒
- **改動**：
  - 手動 kill PID 170812 → watchdog 09:05:15 偵測無回應 → 重啟 PID=121512 → engine=running
  - 09:10 第一根 [Scan] 出現，watchdog [OK]
- **今日無進場原因：ADX 全程 < 23**
  - 全日 ADX 最高 22.1（09:10），此後持續下滑至 13.0（10:15）
  - min_adx=23 從未達到 → 策略全程跳過
  - atrR 從未低於 0.9（無 squeeze 壓縮），也未出現 squeeze→expand 序列
- **狀態**：✅ 自癒確認正常，無交易為正確行為

### 2026-04-28 夜盤 — Heartbeat 修復後首次夜盤驗證
- **動機**：確認 engine heartbeat bug 修復後，夜盤 ORB 策略能否正常全程運行
- **觀察**：
  - 18:27 重啟後心跳穩定每 60s 觸發，watchdog 不再重啟
  - 22:15 ORB 區間正確建立：High=39526 Low=39215 Width=311（4.18×ATR）✅
  - `risk_state_night.json` 不存在 → paper 模式自動 reset peak_equity=200,000（正常行為）
  - 日誌至 22:30 持續正常，之前疑似「22:25 停止」為觀察時間點問題（非真實中斷）
  - 今晚未出現符合條件的突破信號（市場持倉觀望）
- **狀態**：✅ 系統驗證通過

### 2026-04-28 — Engine Heartbeat 修復 + API Key 換新

#### engine.py `time` 遮蓋 Bug（根本原因）
- **動機**：夜盤伺服器從 17:45 起持續每 12 分鐘被 watchdog 重啟，K棒不更新，ORB 策略無法進場
- **根因**：`core/engine.py` 頂部同時有：
  ```python
  import time                                    # 立即被覆寫（死碼）
  from datetime import datetime, time, timedelta # 把 time 遮蓋成 datetime.time
  ```
  導致 `_engine_loop` 裡 heartbeat 計時用 `time.monotonic()` → `datetime.time.monotonic()` → `AttributeError` → exception 每秒發生 → heartbeat 從未成功觸發 → watchdog 12分鐘重啟 → 無限循環
- **改動**：
  - `core/engine.py`：heartbeat 計時改用 `datetime.now()` + `timedelta.total_seconds()`
  - `core/engine.py`：移除頂層死碼 `import time`
  - `core/engine.py`：移除 `_engine_loop` 內無用的 `import time as _time_mod`
  - `core/engine.py`：`_engine_loop` 所有 heartbeat / WallClock 區塊包 try/except，防止 thread 無聲死亡
- **檔案**：`TMFtrader-src/core/engine.py`
- **驗證**：
  - 18:27:11 重啟後：Heartbeat 每 60 秒穩定觸發（18:27:12 → 18:28:12 → 18:29:12）
  - Watchdog 顯示 `[OK] 最後心跳 23s 前`，不再重啟
  - WallClock K棒合成正常：`[WallClock] TMFN synth tick @ 18:27:12`
- **狀態**：✅ 已驗證

#### ⚠️ engine.py 未來使用 time 模組注意
```python
# engine.py 裡 time = datetime.time，不是 time 模組
# 需要 time.sleep / time.monotonic 時，必須在函數內 local import：
import time as _time_mod
_time_mod.sleep(1)
```

#### Shioaji API Key 換新
- **動機**：登入失敗 `111.243.139.191 not allow`（表面看起來像 IP 封鎖）
- **根因**：API Key 失效，不是 IP 問題
- **改動**：`.env` 換用備用 Key（從本機 `410.txt` 取）—— key 值已 redact，避免進入 git history
- **診斷教訓**：`{IP} not allow` 不代表 IP 被封，先換備用 Key 測試同一 IP，成功才是 Key 問題，不需換 IP
- **狀態**：✅ 已驗證

---



### 2026-04-18 — v6b 穩健性修正（afternoon_adx 32→30）
- **動機**：穩健性測試發現 afternoon_min_adx=32 是尖峰型（上下 ±3 就掉 0.9 PF），改為 30 更穩健；min_adx=23 優於 22（掃描網格遺漏）
- **改動**：engine.py: min_adx 22→23, afternoon_min_adx 32→30
- **v6b 全量**：52筆 WR=61.5% PF=2.176 MaxDD=7.3% net=+113,190
- **Walk-Forward**：Train PF=1.983 vs Test PF=2.080（train≈test，泛化良好）
- **狀態**：🟡 待重啟 server 生效

### 2026-04-18 — v6 參數優化 + squeeze 邏輯修正
- **動機**：Agent 引入兩個回歸：(1) squeeze flag 改為需 3 連根才設（原始是第 1 根即設）；(2) 新增 squeeze_grace_bars 預設為 0（應為 1）。同時發現 engine.py 的 memory.md 文件錯誤（afternoon_min_adx 記為 22，實際是 32）。
- **改動**：
  - `breakout.py`：修正 squeeze 偵測邏輯 + 新增 `_in_grace` state + 修正 reset()
  - `engine.py`：min_adx 20→22，early_cut_bars 30→40（v6 sweep 結果）
  - squeeze_grace_bars 預設改為 1（等同原始行為）
- **sweep 結果**：432 組進場 × 81 組出場組合，最佳為 er=1.18, min_adx=22, afternoon_adx=32, early_cut=40
- **v6 vs v5（current code）**：PF 2.203→2.723，WR 57.8%→63.5%，MaxDD 10.9%→8.7%
- **檔案**：
  - source: `TMFtrader-src/strategy/breakout.py`, `TMFtrader-src/core/engine.py`
  - 副本：`deployed_strategies/tmf_breakout/breakout.py`（已同步）
- **驗證**：全量回測 52筆 WR=63.5% PF=2.723 MaxDD=8.7% net=+132,990 ✅
- **狀態**：🟡 待重啟 server 生效（server 自 4/17 15:57 持續運行，使用舊參數）

### 2026-04-18 — 同步 breakout.py 備份
- **動機**：4/17 新增 ema20/ema200/rsi 到 [Scan] log 後，deployed_strategies 備份未同步
- **改動**：`cp TMFtrader-src/strategy/breakout.py deployed_strategies/tmf_breakout/breakout.py`
- **驗證**：diff 無差異
- **狀態**：✅ 已同步

### 2026-04-16 16:23 — 建立 deployed_strategies 資料夾
- **動機**：集中管理此策略實際運行中的所有腳本（策略/自動化/自癒/開機自啟），避免散落在專案各處。仿 `Funding Pips-EA自動化交易/deployed_strategies` 架構。
- **改動**：
  - 新建資料夾結構（README.md, memory.md + 3 個子資料夾）
  - 複製（非移動）以下檔案作為 backup 副本：
    - `tmf_breakout/`: breakout.py, base.py, filters.py, 策略說明.md
    - `automation/`: start.py, watchdog.py, restart_trader.bat, start_watchdog.bat
    - `startup/`: TMFtrader-Server.bat, TMFtrader-Watchdog.bat
- **檔案**：
  - 新建：`deployed_strategies/README.md`, `deployed_strategies/memory.md`
  - 副本位置：詳見 README.md
  - Source of truth 仍在 `TMFtrader-src/` 和 Windows Startup folder
- **驗證**：`ls deployed_strategies/*/` 檔案都在、server/watchdog 未受影響（PID 15480, 26696 持續運行）
- **狀態**：✅ 已驗證

### 2026-04-16 15:?? — watchdog 改用 UTC+8 時間戳
- **動機**：TG 通知時間與系統時區綁定，跨時區部署會混亂；統一用台灣時間
- **改動**：
  - 新增 `TW_TZ = timezone(timedelta(hours=8))` 和 `tw_now()` helper
  - 所有 `tg(...)` 呼叫內的 `datetime.now().strftime(...)` 改為 `tw_now().strftime(...)`（replace_all 一次完成）
- **檔案**：
  - source: `TMFtrader-src/scripts/watchdog.py`
  - 副本（此資料夾）：`automation/watchdog.py` — 已同步
- **驗證**：重啟 watchdog 後 log 正常，下次 TG 通知會是 UTC+8
- **狀態**：🟡 觀察中（等下次 Solace 斷線/恢復事件驗證）

### 2026-04-16 15:?? — watchdog 新增 Solace 恢復通知
- **動機**：原本只發「Solace 斷線」TG，恢復時只 log 不發通知；使用者不確定何時恢復
- **改動**：
  - Watchdog class 新增 `_solace_was_down: bool` 狀態追蹤
  - fallback_active=True 時設 `_solace_was_down = True`
  - fallback_active=False 時若 `_solace_was_down`，發 TG「Solace Tick 已恢復正常」並重置 flag
- **檔案**：
  - source: `TMFtrader-src/scripts/watchdog.py`（line ~398-404）
  - 副本：`automation/watchdog.py` — 已同步
- **驗證**：🟡 待下次 Solace 斷線恢復事件驗證
- **狀態**：🟡 觀察中

### 2026-04-16 ~08:40 — 開機自啟設定（新增 Server bat）
- **動機**：重開機後發現 server 沒有自動啟動，只有 watchdog.bat 在 Startup，而 watchdog 啟動失敗的話就沒人拉起 server
- **改動**：
  - 在 `C:\Users\xx\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\` 新增 `TMFtrader-Server.bat`，直接啟動 `pythonw scripts/start.py --no-browser`
  - 原有的 `TMFtrader-Watchdog.bat` 保留，雙保險
- **檔案**：
  - source: `C:\Users\xx\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\TMFtrader-Server.bat`
  - 副本：`startup/TMFtrader-Server.bat` — 已同步
- **驗證**：下次重開機驗證（目前手動啟動 PID 15480 正常）
- **狀態**：🟡 觀察中（待下次重開機驗證）

---

## 📌 當日觀察（2026-04-16 日盤）

- 日盤 08:45–13:45 **無進場**（daily_trades=0, paper_signals=0）
- 原因：全日 ATR 擴張 ratio 最高 1.16，**從未突破 1.18 的 expand 門檻**
- 最接近進場條件的時刻是 13:30–13:40，ADX 衝到 27.8、+DI=49，但 atrR 僅 1.06，仍不符合 Mode A
- 12:15 volR=4.14（量爆）但 ADX 當時只有 15.4
- **觀察結論**：今日為區間震盪，策略不進場是正確行為，無需調整

---
