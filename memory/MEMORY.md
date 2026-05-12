# Memory Index

## 重大 Bug 記錄（優先閱讀）
- [paper_trading_peak_equity_bug.md](paper_trading_peak_equity_bug.md) — **[嚴重]** Paper Trading peak_equity 跨重啟殘留 → 虛假回撤 → 策略空轉 3 週。診斷「風控擋單」時必須優先排查 risk_state.json。已修復：①core/engine.py paper模式重置peak_equity ②risk/persistence.py 日夜盤改用獨立state file（risk_state.json / risk_state_night.json）③backtest worker 透過 `RiskManager(persist=False)` 不再寫 prod state（2026-05-06）

## 重大 Bug 記錄（續）
- [engine_time_shadowing_bug.md](engine_time_shadowing_bug.md) — **[已修復]** `from datetime import time` 遮蓋 `import time` → `time.monotonic()` 變成 `datetime.time.monotonic()` → AttributeError → engine thread 死亡 → heartbeat 停止 → watchdog 12分鐘重啟循環。修復：改用 `datetime.now()` 計時，移除死掉的 `import time`。
- [2026_05_04_bug_fixes.md](2026_05_04_bug_fixes.md) — **[已修復 2026-05-04]** 三個 bug：①[Scan] 冷卻期誤觸 watchdog（breakout.py）②KbarPoller stale price，Solace 斷線後 TP/SL 評估凍結（broker.py，REST snapshots+5s timeout）③**TP 從未被引擎檢查**（engine.py，新增 hard TP check 同 hard SL）。今日影響：TP 40,551 市場觸及未出場，延遲 1h46m 後於 40,648 出場。
- [2026_05_05_fixes.md](2026_05_05_fixes.md) — **[已修復 2026-05-05]** ①Paper 模式無 heartbeat monitor → Solace 斷線不自動重連（engine.py 改 live+paper 都啟用）②策略狀態 `_entry_atr`/`_trail_best` 重啟歸零 → trail 計算偏差（存入 position JSON，30s 節流存盤+重啟恢復）③Watchdog 有持倉跳過重啟（移除，hard TP/SL 已安全）
- [2026_05_06_fixes.md](2026_05_06_fixes.md) — **[已修復 2026-05-06]** ①backtest worker 污染 risk_state.json（`RiskManager(persist=False)`）②**daily journal 日夜盤碰撞**（performance.py 13 處檔案路徑用 `_suffix` 切分）③5/4-5/6 歷史 backfill + 5/5 trade 修正：日盤 +14,310（原 bug 紀錄 +12,180）、夜盤 -330，INITIAL_BALANCE 同步到 .env=214310、start_night.py=199670 ④5/5 09:01 trade 真實利潤 +4,050（不是 +1,920），entry_atr 沒持久化 → trail 過早出場 → 應該 hit hard TP @ 40,993（市場 09:00 K 棒衝到 41,020 確認衝破）
- [2026_05_07_to_11_fixes.md](2026_05_07_to_11_fixes.md) — **[已修復 2026-05-07~11]** ①watchdog 持倉時 TG spam workaround：`scripts/watchdog.py` 偵測 `state["position"].quantity>0` → 靜音不重啟（**根本修還沒做**，engine.py:1024 持倉分支沒呼叫 on_kbar 才是真凶）②dashboard 重整後 markers 消失：`dashboard/static/index.html:fetchTrades` 補上 markers 重建邏輯（從 entry_time/exit_time 生成 BUY/SELL/EXIT marker）③**INITIAL_BALANCE 漏算後續交易**（**會反覆發生**）：paper 模式 pm.balance 不持久化，每次重啟讀回 .env，累積獲利會丟。5/11 17:30 同步：日盤 .env=222890、夜盤 start_night.py=203930。要根治需修 core/position.py 讓 balance 持久化。④Windows 排程任務改 `powershell -WindowStyle Hidden` 包裝 bat → 完全背景執行不跳視窗（需 admin 提權才能 Set-ScheduledTask）

## Project Memories
- [shioaji_timestamp_fix.md](shioaji_timestamp_fix.md) — Shioaji kbars.ts epoch format and correct conversion method
- [gpu_optimizer_completed.md](gpu_optimizer_completed.md) — GPU optimizer + backtest drawdown fix (2026-04-11)
- [optimizer_findings.md](optimizer_findings.md) — Auto-optimize loop結果：策略PF<1，無法達到6%月報酬，需重寫信號產生器
- [ml_pipeline_qqqm_ivv.md](ml_pipeline_qqqm_ivv.md) — ML pipeline Stage 1-7 最終架構：QQQM+IVV from MT5，sample weights，gate criteria，Stage 7 結果
- [tmf_orb_night_b2.md](tmf_orb_night_b2.md) — TMF 夜盤 ORB B2 ML Filter：vol_ratio 過濾、模型結果、部署狀態、排程改為每天14:55啟動
- [watchdog_weekend_suppression.md](watchdog_weekend_suppression.md) — Watchdog 設計：補班日重啟、token 過期自癒、雙伺服器 --night、TG MODE_LABEL、夜盤 Heartbeat TMFN（timer-based 60s）、每小時策略正常推播、休盤靜默（GAP_WINDOWS + trading session 保護）
- [engine_time_shadowing_bug.md](engine_time_shadowing_bug.md) — engine.py `time` shadowing bug 根因與修復
- [broker_fallback_fix.md](broker_fallback_fix.md) — KbarPoller _fallback_since bug修復：REST失敗時fallback_active=False導致watchdog無法偵測K線凍結

## 診斷順序（策略沒進場時）
1. `cat data/risk_state.json`（日盤）或 `cat data/risk_state_night.json`（夜盤）— peak_equity 是否遠高於目前 balance？（最常見根因）
   - 注意：夜盤 server（port 8889）使用獨立的 `risk_state_night.json`
   - 2026-05-06 起 backtest worker 已隔離（`persist=False`），不再會污染這兩個檔
2. log grep "風控拒絕" — 查具體拒絕原因
3. log grep "Scan" — K線是否正常更新（KbarPoller 是否中斷）
4. ADX/squeeze 條件 — 市場結構問題

## 看實際成交了什麼（2026-05-06 起）
- **不要信** `data/performance/daily/{date}_live.json` 的 5/6 之前的歷史（5/4-5/6 已 backfill 修正，更早的可能仍有 day/night 覆寫遺漏）
- 真實成交：log grep `_execute_entry` 和 `_execute_exit_inner`，每筆都有 instrument / 進出價 / PnL points / reason
- 日盤檔：`{date}_live.json`、夜盤檔：`{date}_live_night.json`、累計：`cumulative.json` / `cumulative_night.json`

## 日盤 Watchdog 啟動時間點問題（已知行為）
- 自癒 watchdog 搜尋 `[Scan]` 訊號（來自 breakout.py on_kbar，每 5 分鐘一次）
- 若 server 剛重啟，第一個 `[Scan]` 要等到下一個 5 分鐘邊界才出現
- watchdog 看不到 `[Scan]` 就開始 12 分鐘倒數，但 engine 其實是 running 且健康
- 正常結果：12 分鐘內 `[Scan]` 出現 → 倒數重置 → 不再重啟
- 若 server 需要手動干預：kill 舊 PID → watchdog 偵測無回應 → 自動重啟 → 下一個 5min bar 後正常
- **2026-05-04 修復**：進場冷卻期（5 根 K 棒）也會觸發 watchdog 誤報（25 分鐘無 Scan），已將 [Scan] log 移到冷卻 early return 之前，修復後冷卻期不再誤報。

## 日盤 engine=error 根因排查
- engine=error 通常是 Shioaji 登入失敗（舊 API Key 啟動的 server）
- `.env` 換 Key 後需重啟 server 才能生效；只更新檔案不夠
- 快速驗證：`curl http://localhost:8888/api/state | grep engine_state`
- 修復：kill 對應 PID → watchdog 自動重啟（需先確認 watchdog 有在跑）

## 系統狀態確認指令（快速健診）
- 心跳：`tail -c 2000 data/logs/watchdog_night.log` — 最後一行應是 `[OK] 最後心跳 Xs 前`
- 引擎：`tail -c 3000 data/logs/ultratrader_YYYYMMDD.log` — 最後應有 `[Heartbeat] TMFN:` 每 60s 一筆
- ORB：grep `\[ORB\]` ultratrader_YYYYMMDD.log — 22:15 應有區間建立記錄
- 風控：`cat data/risk_state_night.json` — circuit_state 應為 active，peak 應≈200000

## 交易復盤
- [weekly_trades_2026_w19.md](weekly_trades_2026_w19.md) — 2026 W19（05/04-05/09）交易紀錄與統計

## Feedback Memories
- [server_restart_method.md](server_restart_method.md) — How to properly kill/restart the trading server on Windows
- [shioaji_login_ban_diagnosis.md](shioaji_login_ban_diagnosis.md) — **[教訓]** Shioaji `{IP} not allow` 誤診為 IP 封鎖。正確流程：先用備用 Key 測試同一 IP，成功則是 Key 問題不需換 IP。
