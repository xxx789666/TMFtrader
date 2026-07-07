# LOOP_MEMORY — 跨會話迴圈記憶

> 協定：每輪迴圈**開始前讀本檔，結束前更新本檔**（規則見 `.claude/skills/loop-method/SKILL.md`）。
> 條目只能從「已測試」升到「已驗證」（需指令輸出背書），或留在「未解決」。相對日期一律寫絕對日期。

## 已驗證（有數據背書的事實）

- [2026-07-07] 本 repo 有 22 支回歸測試在 `TMFtrader-src/tests/`，多數是歷史事故固化（trail_latch、sameday_dir_once、force_close、position_lock 等）。修 engine bug 的迴圈以「pytest 全綠 + 新增事故回歸測試」為完成條件。
- [2026-07-07] 策略研究迴圈的裁判 = `scripts/optimize_breakout_wfo.py::run_wfo`，門檻見 `strategies_research/LESSONS.md`（OOS 兩合約 PF>1.10、正窗≥7/10、跨合約同號≥7/10、5→60 trial 不崩、參數收斂）。
- [2026-07-07] maxpain tape 對帳判準先例：133/133 逐筆誤差 0（配方同源才可比；FinMind vs TAIFEX 同日 mp 可差 100-450 點，絕對值回測必同源）。

## 已測試（做過的實驗與結果，含失敗）

- （尚無迴圈輪次；策略研究的歷史實驗記錄在 `TMFtrader-src/strategies_research/LESSONS.md`，不搬過來、去那裡讀）

## 未解決（待試方向 / 卡住 / 待人決策）

- [2026-07-07] 尚未實跑第一個封閉迴圈。建議起手：bug 修復迴圈（裁判 = pytest，風險零），跑順後再上策略研究迴圈。
- [2026-07-07] 策略研究迴圈若要全自動，需把「8 維體檢 PASS/FAIL」包成單一指令（目前 `digest_research.py` 輸出需人讀）。
