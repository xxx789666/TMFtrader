# LOOP_PLAYBOOK — 四個迴圈的操作手冊

> 給人看的操作卡。每個迴圈 = 目標 + 裁判（一行指令）+ 貼給 agent 的簡報模板 + 你的驗收動作。
> 紀律規則在 `.claude/skills/loop-method/SKILL.md`，跨會話記憶在 `LOOP_MEMORY.md` — 每個模板開頭都叫 agent 先讀這兩個，不用你複述。
>
> **每次開工**：`/model` 選模型、effort 設 high。貼模板 → 走開 → 回來做三件事：
> 自己跑一次裁判指令親眼看 exit 0、`git log` 審最終 diff、翻 LOOP_MEMORY 未解決區清決策。

---

## 迴圈 #1 — Bug 修復 / 重構（裁判：pytest，已就緒）

裁判：`cd TMFtrader-src && python -m pytest tests/ -q` → 全綠（基線 2026-07-07 = 265 passed）

```
先讀 .claude/skills/loop-method/SKILL.md 和 LOOP_MEMORY.md，照上面的紀律工作。

任務：<修復 XXX 的 YYY 問題>
完成條件：cd TMFtrader-src && python -m pytest tests/ -q 全綠（基線 265 passed），
且新增一支覆蓋本次案例的回歸測試
失敗處理：讀錯誤原文再修，同型失敗 3 次停下來問我
邊界：不碰 crontab、不碰 live 引擎行為、不 rsync 上 VPS、不 push
收工：更新 LOOP_MEMORY.md，commit
```

---

## 迴圈 #2 — 策略 edge 研究（裁判：judge_wfo.py，已就緒）

裁判：`python scripts/judge_wfo.py data/wfo_oos_<label>.json` → 最後一行 `VERDICT: PASS` + exit 0

```
用 strategy-edge-research skill 做下一版策略。

完成條件：python scripts/judge_wfo.py data/wfo_oos_vN_<name>.json 判 VERDICT: PASS。
判 FAIL 就照實寫進 LESSONS，不准翻案。
輪數上限：最多 3 個假設全 FAIL 就停，把診斷寫進 LESSONS 後回報我
邊界：同迴圈 #1。收工：更新 LESSONS.md 和 LOOP_MEMORY.md，commit
```

---

## 迴圈 #3a — 回測 tape 對帳（裁判：對帳器，**第一輪先建**）

**目標**：策略的回測 tape（chips / maxpain）跟 live 實際成交逐筆一致。先例 = 2026-07 maxpain
tape 配方檢查 133/133 誤差 0 — 那次是人工跑的，這個迴圈把它固化成可重複指令。

**裁判（目標型態）**：`python scripts/reconcile_tape.py <策略> --from <日期>` → 印
`RECONCILE: 133/133 diff=0` + exit 0。**這支腳本還不存在** — 照 loop-method 規則二：
寫不出裁判指令時，第一個子任務就是建裁判。

現成積木：`scripts/resettle_5m.py`（日K tape → 5m 重結算）、live 成交在引擎 log /
whatif 落帳、`_maxpain_precursor_check.py` 有配方比對邏輯可參考。

```
先讀 .claude/skills/loop-method/SKILL.md 和 LOOP_MEMORY.md。

任務：建 scripts/reconcile_tape.py — 重生成 <chips|maxpain> tape 後與 live 成交逐筆對帳，
印 RECONCILE: n/n diff=0 + exit 0/1。然後跑它，若有差異，逐筆找根因（口徑?配方?資料源?）
修 tape 生成端直到 diff=0。
完成條件：python scripts/reconcile_tape.py <策略> 印 diff=0 且 exit 0
⚠️ 鐵則：對不上時預設是 tape/配方錯，不是 live 錯 — live 成交是 ground truth，
禁止改對帳器容差來「湊過」。容差放寬需要我點頭。
失敗處理：同型失敗 3 次或發現需要改 live 端才能對上 → 停下來問我
邊界：不碰 crontab / live 引擎 / VPS。收工：更新 LOOP_MEMORY.md，commit
```

**你的驗收**：自己跑對帳指令看 n/n 和 exit 0；抽 1-2 筆人眼比 log。

---

## 迴圈 #3b — 資料完整性（裁判：缺bar檢查器，**第一輪先建**）

**目標**：所有交易日的 1min/5m parquet 無缺 bar。已知地形（別讓 agent 重新發現）：
2026-05/29 前 = 月檔 parquet、06/01 後 = tick CSV 經 `_resample_ticks_to_1min.py` 重採樣
（label=右邊界、volume>0、試撮窗剔除）；假日表 = `scripts/market_holidays.txt`；
TMFR1 歷史只到 2024-07、TXFR1 到 2020-03。

```
先讀 .claude/skills/loop-method/SKILL.md 和 LOOP_MEMORY.md。

任務：建 scripts/check_kbar_gaps.py — 對照 scripts/market_holidays.txt 與交易時段
（日盤 08:46-13:45、夜盤 15:01-05:00），掃 <SYM> 的 1min parquet 列出缺 bar 日，
印 GAPS: 0 + exit 0/1。然後補洞（tick CSV 重採樣或 shioaji 補抓）→ 重跑直到 GAPS: 0。
完成條件：python scripts/check_kbar_gaps.py <SYM> --from <日期> 印 GAPS: 0
⚠️ 已知良性缺口先讀 LOOP_MEMORY / MEMORY（如 WallClock synth、休市日、量0分鐘），
別把良性缺口「補」成假資料 — 檢查器要能區分「該有而缺」vs「本來就沒有」。
失敗處理：補不回來的歷史段（源頭沒資料）列清單問我，不准造資料
邊界：同上。API 補抓注意 quota（超量切歷史查詢）。收工：更新 LOOP_MEMORY.md，commit
```

**你的驗收**：跑檢查器看 GAPS: 0；抽一天缺補的資料人眼看 K 線合理（無平線/跳價）。

---

## 迴圈 #4 — WFO 參數優化（裁判：judge_wfo.py — ⚠️ 最容易變過擬合機的一個）

**目標**：對某支既有策略做參數空間 / 搜索方式的優化實驗（參數高爾夫）。
引擎：`scripts/optimize_breakout_wfo.py::run_wfo`。裁判：同迴圈 #2 的 `judge_wfo.py`。

**先讀這三條紅字再開迴圈**：
1. **終止條件只能是 judge_wfo（OOS walk-forward + 跨合約），永遠不准用 in-sample PF**。
   用同段回測當裁判的迴圈跑越久越毒 — 它會精準地過擬合到裁判身上。
2. **每輪 = 一個明確假設**（改搜索範圍/改參數化方式/砍參數），不是「trial 數加倍再抽一次」。
   加 trial 不是進步，是給過擬合更多彩券。
3. **參數維持 ≤6-7 個**。新增參數要在簡報裡寫「為什麼這個自由度該存在」。

```
先讀 .claude/skills/loop-method/SKILL.md、LOOP_MEMORY.md、strategies_research/LESSONS.md。

任務：對 <策略label> 做參數優化實驗。本輪假設：<例：trail 參數改相對 ATR 參數化，
理由 X>。先建 bounds JSON（從 suggest_fn 的 trial 範圍抽出搜索空間）。
流程：5-trial smoke → 60-trial 定版 → 裁判。
完成條件：python scripts/judge_wfo.py data/wfo_oos_<label>.json
  --smoke <smoke產物> --bounds <bounds.json> 判 VERDICT: PASS
輪數上限：最多 3 個假設。全 FAIL = 結論就是「參數層面無改善空間」，照實寫進
LESSONS（這是有效結論，不是失敗）。
⚠️ 禁止：用 in-sample 指標選版本、smoke 好就宣稱成功、放寬 judge 門檻、加 trial 當一輪。
邊界：同上，且優化結果不自動部署 — PASS 了也只出報告，上不上 live 我決定。
收工：更新 LESSONS.md 和 LOOP_MEMORY.md，commit
```

**你的驗收**：自己跑 judge_wfo（帶 --smoke --bounds）看 PASS；看 LESSONS 新段落的
「假設→結果→教訓」是否誠實；**PASS ≠ 上線** — 上 live 前照舊走 paper 驗證流程。

---

## 共通備忘

- 裁判缺 `--bounds` 時參數收斂只 WARN 不硬判（記在 LOOP_MEMORY 未解決區）— 跑 #4 第一輪順手建。
- 所有迴圈產物只 commit 不 push；push 由你決定。
- 「同型失敗 3 次停下來問我」是每個模板的保險絲 — agent 停了就去看它卡在哪，別直接叫它再試。
