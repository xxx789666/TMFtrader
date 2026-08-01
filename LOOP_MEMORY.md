# LOOP_MEMORY — 跨會話迴圈記憶

> 協定：每輪迴圈**開始前讀本檔，結束前更新本檔**（規則見 `.claude/skills/loop-method/SKILL.md`）。
> 四個迴圈的操作卡（含貼給 agent 的簡報模板）：`LOOP_PLAYBOOK.md`。
> 條目只能從「已測試」升到「已驗證」（需指令輸出背書），或留在「未解決」。相對日期一律寫絕對日期。

## 已驗證（有數據背書的事實）

- [2026-07-07] 本 repo 有 22 支回歸測試在 `TMFtrader-src/tests/`，多數是歷史事故固化（trail_latch、sameday_dir_once、force_close、position_lock 等）。修 engine bug 的迴圈以「pytest 全綠 + 新增事故回歸測試」為完成條件。
- [2026-07-07] 策略研究迴圈的裁判 = `scripts/optimize_breakout_wfo.py::run_wfo`，門檻見 `strategies_research/LESSONS.md`（OOS 兩合約 PF>1.10、正窗≥7/10、跨合約同號≥7/10、5→60 trial 不崩、參數收斂）。
- [2026-07-07] maxpain tape 對帳判準先例：133/133 逐筆誤差 0（配方同源才可比；FinMind vs TAIFEX 同日 mp 可差 100-450 點，絕對值回測必同源）。
- [2026-07-07] 策略研究迴圈的機械裁判已建：`python scripts/judge_wfo.py <wfo_oos_json>` → `VERDICT: PASS/FAIL` + exit 0/1。負樣本驗收：三個已知不過關版本全判 FAIL。注意零虧損窗 PF 天文數字，已加單窗 PF_CAP=10 再平均。`--smoke`/`--bounds` 路徑亦已實測（kill_ashort 參數 range 接近整個搜索空間 = 教科書過擬合特徵，與 LESSONS 判決一致）。breakout 家族 bounds = `data/wfo_bounds_breakout.json`。
- [2026-07-07] `check_kbar_gaps.py`（#3b 裁判）已建並實測：TMFR1 2026-01~05 GAPS:0；**TXFR1 五月檔在 5/27 13:45 截斷，5/28-29 日盤真缺**（待 shioaji 補抓後重跑）。夜盤 0 bar 只 WARN 不硬判（假日前夕夜盤可能不開）。
- [2026-07-07] `scan_silent_failures.py`（#7 裁判）已建並實測：**基線 SILENT: 137**（AST 掃 bare-except + 純吞噬 except），命中歷史事故同型（notify.py swallow = TG 靜默、engine.py bare-except）。allowlist = `scripts/silent_allowlist.txt`（空，附格式說明）。

## 已測試（做過的實驗與結果，含失敗）

- [2026-07-10] **首個封閉 bug-fix 迴圈跑通（裁判=pytest）**：修 `risk/circuit_breaker.py` 颱風假熔斷洗版事故。事故=颱風臨時休市、live 引擎 24h 常駐仍連 Shioaji,休市無 session→券商每~2分斷閒置連線→`on_connection_lost` 不分時段一律 EMERGENCY_STOP+推 TG,多引擎各噴洗版（09:47 後安靜=各引擎 30 分 TG cooldown、非 Telegram 限流）。修法=新增 `_in_trading_session()`（日盤08:45-13:45/夜盤15:00-次日05:00 且交易日;跨午夜00:00-05:00 以前一交易日判休市）,`on_connection_lost` 非交易時段直接忽略、`on_connection_restored` 一律解除但 TG 只在交易時段推。**先紅**（`tests/test_circuit_breaker_session.py` 6 支,4 紅）→ 修 → **6 綠**。既有 `test_risk.py::test_connection_lost` 過期（假設一律停機）已改 patch `_in_trading_session=True`。**全套 271 綠**（265+6）。⚠️ 過程抓到一致性 bug:canonical `TMFtrader-src/scripts/market_holidays.txt` 原缺 2026-07-10（先前颱風假只加了 lab repo + VPS 兩處）→ **rsync 會把 VPS 的 7/10 洗掉**,已補進 canonical。
- [2026-07-07] pytest 基線體檢：263 綠 / 2 紅。兩紅皆 `tests/test_strategy.py` 陳舊測試（非 live 鏈路）：v2 調權後 ADX 強趨勢門檻改 `>38`、做多改左側風格（RSI 超賣+K棒確認，追突破刻意 0 分），舊測試情境過期。已修測試（adx 38→40；buy 情境改左側），**265 全綠**。裁判基線可用。
- （策略研究的歷史實驗記錄在 `TMFtrader-src/strategies_research/LESSONS.md`，不搬過來、去那裡讀）

## 未解決（待試方向 / 卡住 / 待人決策）

- [2026-07-10] **circuit_breaker 修復已部署(user 授權人閘)**:①scp 新 `risk/circuit_breaker.py` 上 VPS(備份 `.bak_20260710`,驗 `_in_trading_session` ×3)②kill 5 支引擎(2 live breakout_v7/maxpain_exec + 3 paper chips/wave/astruct,全 flat、active_position 空)—— 用 `pkill -f start_paper.py` 會誤殺自己 SSH session(255),但引擎確實死(pgrep 精確 pattern=0、heartbeat 凍結 10:30:37)。今天洗版停。**未 scp market_holidays.txt**(VPS 已有 7/10、canonical 也補了、只 scp 碼避免洗掉 VPS 其他假日)。**週一行為驗收待辦**:7/13 crontab 08:15-08:25 自動拉起 5 支(start_*.sh 清 .pyc→載新碼),盯 log 確認休市/盤間不再噴緊急停機 TG=修法生效。教訓:①pkill -f <字串> 經 SSH 執行會自我匹配 remote shell、殺自己;驗 live 引擎死看 log heartbeat 凍結(行為)不看 pgrep(會誤匹配)。②**故意 kill live 引擎要順手清掉 PID 檔 `/tmp/TMFtrader_<owner>_live.pid`**,否則 `watchdog_alert.sh`(每5分、只告警不重啟)讀到 stale PID 檔+process 已歿 → 每5分噴「live <owner> 不在了(pid X 已歿)」誤報(7/11 週末 breakout_v7 噴了;規則:PID 檔不存在=尚未啟動=不報,故 rm PID 檔即止;週一 crontab 重啟寫新 PID 檔恢復)。watchdog 只監控 breakout_v7+night_v7(paused),maxpain/chips/wave/astruct 不在監控內。
- [2026-07-13] **連線上限搶槽（已證實=常態飽和,非瞬間 race;正解=第二身分證,卡在權限）**：person **J122786231** 被 **11 個進程**同搶（本機 6:equalratio_paper/weekly、record_depth/txo/stock、kuli_tzi + VPS 5 引擎），上限 ~5-8 → 晚到者撞 451。**7/13 08:46 sweep 收集器 login 重試 3 次（18秒）全撞 Too Many Connections** → 更正 7/9「開盤瞬間 race」誤判:**是連線池常態不夠、重試治標無效**（重試修碼本身正確運作、log 看得到 try1/2/3,但救不回飽和）。正解=拆第二身分證。**已解決(2026-07-13):** user 給第二把 key(person **C221430754**,≠ J122786231 ✓、本機 IP 過 ✓)雖只有模擬權限(正式 login 回 400 "no production permission"),但**永豐模擬模式只擋下單、不擋行情**——`sj.Shioaji(simulation=True)` 登入這把 key,`snapshots([TXFG6])` 回**真盤價**(close=46033、真買賣量,實測)。故 sweep 改吃這把:`connect()` 加 `SWEEP_API_KEY/SWEEP_SECRET_KEY`(fallback 原 J122786231)+ `SWEEP_SIMULATION=1` → `sj.Shioaji(simulation=sim)`;三值寫入 lab `.env`。落在 C221430754 獨立池、0 搶槽。**已驗證**:`--once` smoke `sweep ok: 560 contracts, 499 with volume` exit0、持久迴圈 PID 起、明天 08:46 排程讀 `.env` 自動沿用。教訓:**data-only 用途(sweep/行情錄製)不需正式權限,sim key 就能吃真行情且是獨立連線池 → 天然的拆槽手段**(下單才需正式)。
- [2026-07-07] ~~尚未實跑第一個封閉迴圈~~ → 已跑（見上 2026-07-10 circuit_breaker）。建議起手：bug 修復迴圈（裁判 = pytest，風險零，基線已 265 全綠），跑順後再上策略研究迴圈。
- [2026-07-07] ~~策略研究迴圈需單一 PASS/FAIL 指令~~ → 已解決（`judge_wfo.py`）。~~bounds 未建~~ → breakout 家族已建，其他策略家族首跑 #4 時自建。
- [2026-07-07] TXFR1 1min 五月檔 5/28-29 日盤缺（check_kbar_gaps 抓到）→ 首跑迴圈 #3b 時 shioaji 補抓；6 月起 tick CSV 尚未落成月 parquet，同輪處理。
- [2026-07-07] 靜默失敗基線 137 → 首跑迴圈 #7 時分類歸零（改 live 引擎路徑的點要集中問 user）。
- [2026-07-07] 迴圈 #8（資料更新到最新）零件全現成：更新器 `fetch_history_kbars.py`（quota 雙閘門/續抓/交易時段拒跑/`--force` 重抓當月）+ 裁判 `check_kbar_gaps.py`。`fetch_and_update_all.py` 是舊一次性版（寫死 2026-04-27）勿用。待辦：(a) 首跑補 TXFR1 5/28-29 與 6 月起缺段、(b) 想全自動就掛本機排程（人閘登記）。
- [2026-07-07] 迴圈 #5（lab 策略前推 paper）的兩支裁判待建：`preflight_paper.py`（事故記憶機械化 checklist，項目清單在 LOOP_PLAYBOOK #5a）、`paper_acceptance.py`（上線後逐日驗收）。第一次跑 #5a 時順手建。
- [2026-07-07] 迴圈 #6/#7 待首跑：#6 fetcher 合約測試（fixture 型，不打真網路；先盤點 fetcher 清單）、#7 靜默失敗撲殺（先建 `scan_silent_failures.py` + allowlist；良性沉默 WallClock synth / DayORB 掃描進 allowlist 非修掉）。高頻 bug 家族統計見 2026-07-07 對話：資料源 ≥7 事故（含真錢 -96k）、靜默失敗 ≥6 事故。
