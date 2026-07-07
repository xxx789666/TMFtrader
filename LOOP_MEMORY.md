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

- [2026-07-07] pytest 基線體檢：263 綠 / 2 紅。兩紅皆 `tests/test_strategy.py` 陳舊測試（非 live 鏈路）：v2 調權後 ADX 強趨勢門檻改 `>38`、做多改左側風格（RSI 超賣+K棒確認，追突破刻意 0 分），舊測試情境過期。已修測試（adx 38→40；buy 情境改左側），**265 全綠**。裁判基線可用。
- （策略研究的歷史實驗記錄在 `TMFtrader-src/strategies_research/LESSONS.md`，不搬過來、去那裡讀）

## 未解決（待試方向 / 卡住 / 待人決策）

- [2026-07-07] 尚未實跑第一個封閉迴圈。建議起手：bug 修復迴圈（裁判 = pytest，風險零，基線已 265 全綠），跑順後再上策略研究迴圈。
- [2026-07-07] ~~策略研究迴圈需單一 PASS/FAIL 指令~~ → 已解決（`judge_wfo.py`）。~~bounds 未建~~ → breakout 家族已建，其他策略家族首跑 #4 時自建。
- [2026-07-07] TXFR1 1min 五月檔 5/28-29 日盤缺（check_kbar_gaps 抓到）→ 首跑迴圈 #3b 時 shioaji 補抓；6 月起 tick CSV 尚未落成月 parquet，同輪處理。
- [2026-07-07] 靜默失敗基線 137 → 首跑迴圈 #7 時分類歸零（改 live 引擎路徑的點要集中問 user）。
- [2026-07-07] 迴圈 #5（lab 策略前推 paper）的兩支裁判待建：`preflight_paper.py`（事故記憶機械化 checklist，項目清單在 LOOP_PLAYBOOK #5a）、`paper_acceptance.py`（上線後逐日驗收）。第一次跑 #5a 時順手建。
- [2026-07-07] 迴圈 #6/#7 待首跑：#6 fetcher 合約測試（fixture 型，不打真網路；先盤點 fetcher 清單）、#7 靜默失敗撲殺（先建 `scan_silent_failures.py` + allowlist；良性沉默 WallClock synth / DayORB 掃描進 allowlist 非修掉）。高頻 bug 家族統計見 2026-07-07 對話：資料源 ≥7 事故（含真錢 -96k）、靜默失敗 ≥6 事故。
