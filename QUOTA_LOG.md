# 永豐 Shioaji API Quota 用量日誌

> 用途：追蹤每日實際 quota 用量、建立 baseline、提前發現異常、為 5/19 切 live 做準備。

---

## 1. 配額等級表（官方）

來源：[使用限制（中文版）](https://sinotrade.github.io/zh/tutor/limit/)

| 等級 | 條件（近 30 日 API 成交額） | 每日流量上限 |
|---|---|---|
| 0 | 0 口（paper / 不交易）| **500 MB** |
| 1 | 1 ~ 大台 1000 口 / 小台 4000 口 | 2 GB |
| 2 | > 大台 1000 口 / 小台 4000 口 | 10 GB |

> ⚠️ 官方文檔**沒提 TMF 微型台指口數如何換算**、待永豐客服回覆（email 已寄 2026-05-14）。

---

## 2. Quota 重置機制

| 規則 | 來源 | 確認狀態 |
|---|---|---|
| 流量超量 → 行情查詢類（`ticks`/`snapshots`/`kbars`）回空值 | 官方明寫 | ✅ 確認（5/14 親歷）|
| 連續超量 → 服務暫停 1 分鐘 | 官方明寫 | ⚠️ 未驗證 |
| 當日多次連續超量 → IP + ID 整日鎖 | 官方明寫 | ⚠️ 未驗證 |
| **daily reset 時區（TST / UTC / 滾動 24h）** | 官方**未寫** | 🚨 待永豐回覆 |

**5/14-5/15 親身觀察**：5/14 14:30 用量 1.59 GB → 5/15 08:57 重置成 30 MB。重置點介於兩者之間、可能在 5/15 00:00 TST 或 08:00 TST（UTC=00:00）。

---

## 3. 用量分解（按來源）

| 來源 | 估算 | 是否必要 | 修法狀態 |
|---|---|---|---|
| TMF tick subscribe 日盤 5h（08:45-13:45）| 50-80 MB | ✅ 必要 | - |
| TMF tick subscribe 夜盤 14h（15:00-05:00）| 60-100 MB | ✅ 必要 | - |
| MXF tick subscribe ORB 期間 7h（21:30-04:30）| 60-100 MB | ✅ 必要 | - |
| Solace heartbeat × 2 process × 24h | 20-30 MB | ✅ 必要 | - |
| **修法 A**：KbarPoller REST fallback（30s 一輪）| ~10-50 MB | ❌ 可砍 | ✅ commit `0892ac7` |
| **修法 B**：每日 08:30 cron restart 重抓 contracts | ~150-200 MB/次 | ❌ 可砍 | 🟡 評估中 |
| **修法 C**：reconnect 重抓 fetch_contracts | ~100 MB/事件 | ❌ 可砍 | 🟡 評估中 |
| **異常**：broker reconnect 風暴 | 50-150 MB | ❌ 應避免 | 🟡 watchdog 改善 |

副作用清單詳見 memory `vps_traffic_optimization_side_effects.md`。

---

## 4. 監測方法

### 方法 A：手動跑診斷腳本（任何時候）
```bash
ssh ultratrader-night
cd ~/ultra-trader-src && source .venv/bin/activate
python3 /tmp/_test_kbars.py 2>&1 | tail -10
```
看最後 `api.usage()` 輸出：
```
UsageStatus(connections=N, bytes=X, limit_bytes=Y, remaining_bytes=Z)
```

### 方法 B：未來可實現（待開發）
- `scripts/_log_quota.py`：純 login + api.usage() + logout（不 fetch_contracts、最省）
- cron 一日 4 次自動記錄到此檔
- 警戒推 TG（remaining < 100MB 時告警）

---

## 5. 警戒線

| 用量門檻 | 動作 |
|---|---|
| `bytes < 200 MB` | 🟢 健康範圍 |
| `bytes 200-300 MB` | 🟡 中度負載、留意是否有 reconnect 風暴 |
| `bytes 300-400 MB` | 🟠 高負載、應主動 SSH 看 log |
| `bytes 400-500 MB` | 🔴 即將爆量、考慮人工關閉某條策略 |
| `remaining < 0` | 🚨 已爆量、第一階段切 kbars/ticks/snapshots、立即排查 |
| 連續 2 日爆量 | 🚨🚨 可能被切第二/三階段、立即打永豐客服 |

---

## 6. 用量歷史日誌

### 表格欄位說明
- **bytes**：當下 `api.usage().bytes`、單位 MB（四捨五入）
- **remaining**：當下剩餘流量（負值 = 超量）
- **conn**：connection 數量（同 person_id 上限 5）
- **跑了什麼**：當下哪些 trading process 在運行
- **備註**：異常事件

| 日期 / 時間 | bytes | remaining | conn | 在跑什麼 | 備註 |
|---|---|---|---|---|---|
| 2026-05-13 (歷史) | ? | ? | ? | start.py 24h + 22:00 起 paper_night_orb | 未監測、5/14 才察覺問題 |
| **2026-05-14 14:30** | **1,519 MB** | **-1,019 MB** | 2 | 雙策略 + 重連風暴 + 手動 restart | 🚨 **爆量 318%、kbars 全 0** |
| 2026-05-14 14:30+ | (持續) | (繼續累積) | 1 → 0 | 手動 kill start.py、剩 ORB | 止血、避免進第二階段 |
| **2026-05-15 08:57** | **29 MB** | **471 MB** | 2 | 08:30 start.py 重啟 + `_test_kbars.py` 測 | ✅ **quota 已重置、kbars 恢復** |

---

## 7. Baseline 預期（修法 A 後、5/15 起）

| 階段 | 預期 bytes/日 |
|---|---|
| 修法 A 生效（KbarPoller 停） | ~250-300 MB |
| + 修法 C（reconnect 不重抓）| ~200-250 MB |
| + 修法 B（取消 daily restart）| ~150-200 MB |
| 切 live 後等級升 2GB | 完全無壓（用 < 25%）|

---

## 8. 對應的 commit 與 memory

**已落地（修法 A）**
- `0892ac7` Disable broker.KbarPoller by default (save ~10-50 MB/day quota)

**待落地（修法 B / C）**
- 修法 B：取消 daily 08:30 cron restart（前置：驗 risk_state 新交易日 reset）
- 修法 C：reconnect 不重抓 fetch_contracts

**相關 memory**
- [[shioaji-kbars-api-zero-bars]] — 5/14 quota 爆量根因
- [[vps-traffic-optimization-side-effects]] — 3 個修法的副作用清單
- [[open-items-snapshot-2026-05-14]] — 全部 P0-P4 待辦
- [[vps-clock-utc-vs-log-tst]] — VPS 時鐘 UTC、log 用 TST、grep 前換算

---

## 9. 客服待回信問題（quota 相關）

1. quota daily reset 時區（TST / UTC / 滾動）
2. TMF 微台口數如何計算（大台/小台/微台比例）
3. 升等是即時 / 隔日 / 月結？
4. Paper mode 是否計入「近 30 日成交」？
5. 帳號 / IP 是否已被當日暫停？是否需主動 unblock 申請？

收到回信後請更新「2. Quota 重置機制」段落並填回確認狀態。

---

> 維護備註：每次 `api.usage()` 後請更新「6. 用量歷史日誌」表格、保留至少 14 天記錄。
