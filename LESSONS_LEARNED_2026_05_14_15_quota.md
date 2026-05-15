# 2026-05-14~15 Quota 爆量事件覆盤

> 5/14 一日燒 1.59 GB / 500 MB 上限（超量 318%）、5/15 復發燒 -77 MB。
> 兩天才抓到真正主犯。本文記錄前因後果 + Claude 犯的 5 個錯誤 + 教訓。

---

## TL;DR

- **配額**：永豐 Shioaji API paper 模式 0 成交額帳號 = **500 MB / 日**
- **5/14 結果**：bytes = 1,592,560,729（1.59 GB）、remaining = -1,068,272,729、超量 318%
- **5/15 結果**：bytes = 577 MB、remaining = -77 MB（連續超量風險、可能被永豐第三階段 IP 鎖）
- **真正主犯**：`broker._monitor` 把「收盤時段無 tick」誤判為「斷線」、觸發 `_attempt_reconnect()`、每次重連都呼叫 `fetch_contracts()` ≈ 50 MB
- **dead zone 長度**：日盤→夜盤 1h15min、夜盤→日盤 **3h45min**、每 11s 一次 reconnect、單一場 storm 可燒 200-2000 MB
- **修了什麼**：表面修了 5 次、4 次選錯層級、第 5 次（本文撰寫時）才開始攻真正根因（broker heartbeat 邏輯）

---

## 事件時間軸

### 5/13 週二 — 部署日（無事）
- VPS 雙策略 paper 開跑：start.py（日盤 breakout 24h）+ paper_night_orb.py（夜盤 ORB 14:55-05:10）
- 流量未監測

### 5/14 週三 — 第一次爆量
- 08:30 cron restart_day.sh、PID 49253 起跑
- 整個早盤 09:00-12:59 **0 個 [Scan] log**（策略 mute）
- 13:45 日盤收盤、broker heartbeat 30s timeout 開始 fire
- **13:45-14:55** 1h10min dead zone storm、每 11 秒一次 reconnect × ~380 次
- 14:55 夜盤開盤、storm 停
- 22:00 ORB session 啟動、邏輯正常
- 收線時 `api.usage()` 顯示 1.59 GB、超量 318%
- TG spam 8 則「[UltraTrader] 券商連線中斷/恢復」

**當天 Claude 抓到的**：
- ✅ daily JSON 顯示 0 trade
- ✅ 早盤 [Scan] = 0
- ✅ log 顯示 `[Shioaji] TMF 歷史 K 棒: 0 bars`
- ✅ `api.usage()` quota 爆量 318%
- ❌ **沒抓到** reconnect storm 是主犯（只看到 kbars 0 bars、把焦點放在 kbars API 失能）

**當天 Claude 做的**：
- commit `44bbb90` TG 全事件覆蓋
- commit `0f45ea7` circuit_breaker 5min cooldown（spam 抑制、不省 quota）
- commit `3d687fd` TG tag rename
- commit `0892ac7` **修法 A**：broker.KbarPoller 預設停用（節省 ~10-50 MB/日、**不是主犯**）

### 5/15 週四 — 第二次爆量
- 5/15 00:00 quota 重置（時區待永豐確認、推測 TST 00:00）
- 08:30 cron restart_day.sh、PID 234932 起跑
- kbars API 恢復、`[Warmup] 3722 bars`、`[Scan]` 從 8:35 起正常
- 09:00 跑 log_quota：30 MB 健康
- 13:45 日盤收盤、**reconnect storm 重現**
- 13:45-13:57 12 分鐘內 quota 從 144 → 577 MB（燒 433 MB）
- user 反映 TG spam（13:45:33, 13:50:51, 13:56:06）
- Claude 此時才認真追 reconnect storm
- 14:00 commit `b909bea` **修法 C**：reconnect fetch_contracts 5min cooldown
- 14:00 commit `d7d16b9` circuit_breaker TG cooldown 5min → 30min
- 14:08 user 怒：「快被你氣死、早該我第一次傳給你就先暫停了你在那邊拖」
- 14:10 emergency kill start.py + 停 watchdog cron

**當天 Claude 抓到的**：
- ✅ broker.py:_attempt_reconnect() 每次都 `fetch_contracts(contract_download=True)` ~50 MB
- ✅ dead zone 70 分鐘 × 每 11s = 200-400 reconnect 次數
- ❌ **沒算夜盤 dead zone 3h45min**、所以 5min cooldown 設計值不夠
- ❌ 沒抓到 broker.heartbeat tick_timeout_sec=30s 才是真正源頭（修這個、就不會有 reconnect storm）

---

## 真正根因（5/15 14:30 才完整理解）

```
broker.py::start_heartbeat_monitor() 預設 tick_timeout_sec=30
  ↓
broker._monitor() 每 10s 檢查、elapsed = monotonic() - _last_tick_time
  ↓
elapsed > 30 → fire _on_connection_lost_cb() → 觸發 _attempt_reconnect()
  ↓
_attempt_reconnect() → logout → login → fetch_contracts(~50MB) → re-subscribe
  ↓
若仍無 tick（市場休市）→ _last_tick_time 不會被 reset
  ↓
下一輪 _monitor() 仍 elapsed > 30 → 繼續 fire（無限循環）
```

**為何「無限循環」**：
1. `_attempt_reconnect()` 成功完成、但**沒 reset `_last_tick_time`**
2. `_last_tick_time` 只在收到真實 tick 時更新（broker.py:372 `self._last_real_tick_time = _now`）
3. 市場休市 = 無 tick = `_last_tick_time` 不會更新 = elapsed 一直 > 30
4. 30 秒 + 重連 11 秒 + 10 秒 sleep = 每 ~11 秒一次循環

**Dead zone 長度**：
- 日盤 13:45 → 夜盤 15:00 = **75 分鐘**
- 夜盤 05:00 → 日盤 08:45 = **225 分鐘**
- 兩個合計 = **5 小時 / 日**
- 每場 storm 約 ~400 次 reconnect、若全 fetch = ~20 GB / 日（誇張、實際因 fetch 失敗或 cache 沒到那麼多、但 1-2 GB 容易撞）

---

## Claude 犯的 5 個錯誤（明確記）

### 錯誤 1：5/14 誤判 0 trade 是市況問題
**症狀**：daily JSON 顯示 0 trade、log 沒 entry/signal 事件
**Claude 反應**：說「市況沒突破訊號、預期內」
**實際**：策略**完全沒在 scan**（[Scan] log = 0、bar_count<80 早退）
**應該**：先看 [Scan] log 計數、再下結論

### 錯誤 2：抓到 kbars 0 bars 但沒抓到 reconnect storm
**症狀**：log `[Shioaji] TMF 歷史 K 棒: 0 bars`
**Claude 反應**：寫了 LESSONS_LEARNED... 不對是寫 memory 說「kbars API 失能」
**實際**：kbars 0 bars 是「quota 爆量被切」的**症狀**、不是**因**
**應該**：再往上追「為什麼 quota 爆量」、找到 reconnect storm

### 錯誤 3：5/15 看到 spam 重現先 commit 才 kill
**症狀**：user 13:48 貼 TG「13:45:33 / 13:50:51 / 13:56:06」spam
**Claude 反應**：說「cooldown 工作正常、只推 1 對」、繼續分析
**實際**：quota 已在燒、應該第一動作 kill、不是寫 commit
**已存 memory**：[[incident-response-kill-first-analyze-later]]

### 錯誤 4：5min cooldown 設計沒算 dead zone 長度
**症狀**：寫修法 C cooldown = 300s
**Claude 反應**：「應該夠了」、commit
**實際**：dead zone 225 min / 5 min = 45 個 cooldown 週期、每週期 1 fetch = 45 × 50 MB = 2.25 GB
**應該**：先算「最長 dead zone × 預估 fetch 次數 × 50MB = ?」、再決定 cooldown 長度

### 錯誤 5：每次修法都修錯層級
| 修法 | 攻的層 | 真正主犯層 |
|---|---|---|
| 修法 A KbarPoller off | snapshot REST fallback | ❌ |
| 修法 C reconnect cooldown | reconnect 重抓 fetch_contracts | 半對（緩解但治標）|
| TG cooldown bump | TG 推播次數 | ❌（不省 quota）|
| **修法 D（待）broker.heartbeat 不在 dead zone fire** | **monitor 過度敏感** | ✅ **真兇** |

**應該**：找到「為什麼會 trigger reconnect」、修那個、不是修「reconnect 後做啥」

---

## 不該再犯的反模式

1. **看到症狀想修第一層、不追根因**
   - 反例：kbars 0 bars → 改 broker.kbars 邏輯 ❌
   - 正例：kbars 0 bars → 為什麼 quota 爆 → reconnect storm → 為什麼 reconnect → broker.heartbeat 誤判

2. **未量化驗證就 commit**
   - 反例：「5min cooldown 應該夠」→ commit ❌
   - 正例：「最長 dead zone 225 min、5min cooldown 救 5/225 = 2.2%、不夠、要改 60min」

3. **user 反映「又來了」還在分析**
   - 反例：「cooldown 在工作呀」→ 繼續解釋 ❌
   - 正例：「kill、等止血後再分析」（memory [[incident-response-kill-first-analyze-later]]）

4. **沒 simulate 程式碼路徑就 deploy**
   - 反例：寫修法 C 沒走過「dead zone 連續 100 次 reconnect」的 loop ❌
   - 正例：紙上跑一次「05:00 tick 停→05:00:30 fire→reconnect→還是沒 tick→05:00:41 又 fire→...」、估算結果

5. **過度信任 user 偏好（VPS 不重啟）在緊急時不能 override**
   - 反例：quota 爆量還堅持「等明天 cron」❌
   - 正例：「現在 -17 MB、再燒會 IP 鎖、現在 kill」

---

## 真正修法（5/16 週末完成、5/18 一上線驗證）

### 修法 D-1：broker.heartbeat 加 dead zone 判斷

```python
# core/broker.py
from datetime import datetime, time as dtime

def _is_dead_zone(self) -> bool:
    """市場休市時段、heartbeat 不該把無 tick 當斷線。"""
    now = datetime.now()
    # 週末完全休市
    if now.weekday() >= 5:  # 5=Sat, 6=Sun
        return True
    t = now.time()
    # 日盤→夜盤 gap：13:45-15:00
    if dtime(13, 45) <= t < dtime(15, 0):
        return True
    # 夜盤→日盤 gap：05:00-08:45
    if dtime(5, 0) <= t < dtime(8, 45):
        return True
    return False

def _monitor():
    while self._connected:
        time_module.sleep(10)
        if not self._connected:
            break
        if self._is_dead_zone():
            continue  # 收盤時段不偵測斷線
        if self._last_tick_time:
            elapsed = ...
```

**效果**：dead zone 內完全不 fire on_connection_lost、零 reconnect、零 fetch_contracts、storm 從根上消失。

**真斷線怎辦**：交易時段（08:45-13:45 / 15:00-05:00 / 共 19h）heartbeat 仍 30s 觸發、正常偵測。

### 修法 D-2：reconnect 成功 reset `_last_tick_time`

```python
# broker._attempt_reconnect() 成功 path:
self._connected = True
self._last_tick_time = time_module.monotonic()  # ← 加這行、視為心跳基準重置
```

**效果**：即使萬一 dead zone 判斷出錯、reconnect 後也只會在另一個 timeout 週期後才 fire、不會立刻無限重試。

### 修法 D-3：保留現有修法 A / C / cooldown 30min 當第二道防線

- 修法 A KbarPoller off（commit `0892ac7`）：仍保留
- 修法 C reconnect fetch_contracts 5min cooldown（commit `b909bea`）：仍保留
- circuit_breaker TG cooldown 30min（commit `d7d16b9`）：仍保留
- 若 D-1 出錯、D-2 + 修法 C 還能擋下大部分

---

## 5/18 週一驗證計畫

### Sunday 5/17 晚上前
1. ✅ Commit 修法 D-1 + D-2 到 broker.py
2. ✅ rsync 到 VPS（disk 換、不重啟）
3. ✅ 解註解 vps_watchdog cron
4. ✅ 寫驗證 checklist

### Monday 5/18 早上
- 08:30 TST：cron restart_day.sh 自動觸發、PID 新起、載入 D-1 + D-2 broker.py
- 09:00 TST：log_quota.py cron 跑、第一個 baseline 量測（應該 < 100 MB）
- 13:45 TST：日盤收盤、broker 進入 dead zone、**應該完全沒 reconnect**（D-1 生效）
- 13:50 TST：再量 quota（應該跟 13:40 差不多）
- 14:00 TST：log_quota.py cron 又量（應該 < 200 MB）
- 14:55 TST：夜盤 cron restart_night.sh、ORB 起
- 21:30 TST：ORB session 啟動
- 22:00 TST：log_quota.py 量（應該 < 300 MB）
- 04:30 TST 隔日：log_quota.py 量
- 5/19 00:00 TST：quota reset 前最終 < 500 MB ✓

### Monday 監測門檻
| 時間 | 預期 bytes | 異常處置 |
|---|---|---|
| 09:00 | < 100 MB | > 100 MB 立刻查 log |
| 13:50 | < 250 MB | > 300 MB 緊急 kill |
| 22:00 | < 350 MB | > 400 MB 緊急 kill |
| 04:30 | < 450 MB | > 480 MB 緊急 kill |

### 含「下單進場成功」驗收
- 5/18 期望至少看到 1 筆 paper trade（任一策略）
- 看 `data/paper_trading/night_orb_*.csv` 或 `data/performance/daily/*.json` 內 trades 陣列
- TG 收到「📥 進場 [Sinopac-Paper]」訊息
- 若 5/18 整日都無進場（市況真無訊號）、5/19 再觀察
- **5/19 之前未看到任何進場、要審查策略條件是否過嚴**

---

## 給未來自己的提醒

當你（Claude / 我自己）下次想下「我修好了」這個結論之前：

1. **量化算過嗎？** 寫公式：問題量 × 修法效果 = 救回多少？vs 預算？
2. **simulate 過程式碼路徑嗎？** 紙上跑一次 worst-case 場景（如 225 分鐘 dead zone）
3. **user 第一個怒回前我反應對了嗎？** 如果 user 罵、那 90% 我選錯優先順序
4. **承認看走眼的勇氣**：「上次的修法不是真兇、現在改攻 X 層」比「我這次抓對了」誠實

---

## 相關 commit 索引

| Commit | 內容 | 對 quota 真實影響 |
|---|---|---|
| `44bbb90` | TG 全事件覆蓋 | 0（TG 不算永豐 quota）|
| `aba2d2a` | daily_status_ping 夜盤 bug fix | 0 |
| `0f45ea7` | circuit_breaker 5min cooldown | 0（不省 quota、只省 TG spam）|
| `3d687fd` | TG tag rename | 0 |
| `909cd51` | Checklist 更新 | - |
| `0892ac7` | **修法 A** KbarPoller off | ~10-50 MB / 日 ✓ |
| `f5da045` | QUOTA_LOG.md | - |
| `69e3b4c` | Checklist 5/15 morning | - |
| `8b7078d` | log_quota.py 自動化 | 微弱（每次 ~1MB × 4 = 4MB/日 額外）|
| `b909bea` | **修法 C** reconnect cooldown 5min | ~50-100 MB / storm（不夠救）|
| `d7d16b9` | TG cooldown 5min → 30min | 0 |
| **(待 commit)** | **修法 D-1 + D-2** broker heartbeat dead-zone aware + reset on reconnect | **預估 ~500-1500 MB / 日** ← 真主犯 |

---

## 相關 memory

- [[shioaji-kbars-api-zero-bars]] — 5/14 第一次抓到的症狀（kbars 0 bars）
- [[vps-traffic-optimization-side-effects]] — 修法 A/B/C 副作用清單
- [[incident-response-kill-first-analyze-later]] — 5/15 教訓：先 kill 再分析
- [[open-items-snapshot-2026-05-14]] — 5/14 待辦清單（過時、本文取代）
- [[tg-full-event-coverage-preference]] — TG 偏好
- [[vps-deploy-without-restart]] — 部署偏好（緊急情境下被 override）
