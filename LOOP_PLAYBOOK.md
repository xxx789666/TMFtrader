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

## 迴圈 #3b — 資料完整性（裁判：check_kbar_gaps.py，已就緒）

**目標**：所有交易日的 1min/5m parquet 無缺 bar。已知地形（別讓 agent 重新發現）：
2026-05/29 前 = 月檔 parquet、06/01 後 = tick CSV 經 `_resample_ticks_to_1min.py` 重採樣
（label=右邊界、volume>0、試撮窗剔除）；假日表 = `scripts/market_holidays.txt`；
TMFR1 歷史只到 2024-07、TXFR1 到 2020-03。

```
先讀 .claude/skills/loop-method/SKILL.md 和 LOOP_MEMORY.md。

任務：跑 python scripts/check_kbar_gaps.py <SYM> --from <日期> --to <日期>（已建，
2026-07-07 Fable 試跑驗證：TMFR1 1-5月 GAPS:0、TXFR1 抓到真缺口 5/28-29）。
有 GAP 就補洞（tick CSV 經 _resample_ticks_to_1min.py 重採樣或 shioaji 補抓）→
重跑直到 GAPS: 0。
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

## 迴圈 #5 — lab 策略前推 paper（三段式：閉環實作 → 人閘 → 驗收監控）

**目標**：tmf-strategy-lab 的新策略（handoff 資料夾交接）接進本 repo 引擎框架跑 paper。
歷史上這條路徑是事故重災區（試撮 tick 消耗訊號、領養預設停利誤觸、stale 訊號漏單、
金鑰靜默掛…），鐵律「上線前掃事故記憶當 checklist」— 本迴圈把它機械化。

### 5a 交接實作迴圈（封閉，可全自動）

**裁判（兩道，第一輪先建第二道）**：
1. `python -m pytest tests/ -q` 全綠（含為新策略加的引擎測試）
2. `python scripts/preflight_paper.py <策略>` → `PREFLIGHT: PASS` + exit 0 — **待建**。
   把事故記憶固化成機械檢查項（每項對應一次真實學費）：
   - 進場窗 ≥08:45（試撮假 tick）＋ simtrade tick 濾除
   - `_is_trading_day()` guard ＋ market_holidays 比對（非交易日 synth tick）
   - 領養/預設 SL/TP 逐欄審 = 無隱藏硬停（7/3 試撮觸發平倉事故）
   - 訊號檔過期規則（假日 gap >2 天跳過；stale 訊號告警雙端）
   - 金鑰缺失行為明確（fail-closed 或文件化 fail-open）＋ .env fallback 非 vps api.txt
   - 成交模擬 = 真實 tick 穿價（非 mid）；進場窗無量 = 放棄
   - Discord/TG：專屬 WEBHOOK ＋ 日報排程 ＋ 假日不出報
   - Shioaji 連線預算（5 條/身分證）不超；position_lock 單池互斥相容
3. （加分）replay parity：引擎跑歷史 N 日，訊號與 lab tape 一致（容差先例見 #3a）

```
先讀 .claude/skills/loop-method/SKILL.md、LOOP_MEMORY.md，和 <策略>_handoff/ 交接文件。

任務：把 <策略> 接進本 repo paper 引擎框架（訊號橋接/引擎配置/日報/watchdog 掛載），
產出可部署包但不部署。若 scripts/preflight_paper.py 不存在，第一個子任務是建它
（檢查項清單見 LOOP_PLAYBOOK 迴圈 #5a）。
完成條件：pytest 全綠 + python scripts/preflight_paper.py <策略> 判 PREFLIGHT: PASS
⚠️ preflight 每一項對應一次真實虧損事故，不准為了過檢而弱化檢查器 — 檢查器改動
單獨列出給我審。交接文件的成本/滑價預期 = 硬約束，實作偏離要標紅回報。
失敗處理：同型失敗 3 次或發現需改共用引擎核心 → 停下來問我
邊界：不碰 crontab / 不 rsync / 不 push / 不動既有策略行為
收工：更新 LOOP_MEMORY.md，commit
```

### 人閘（你親手，不進迴圈）

部署包 rsync 上 VPS 或本機排程掛載、crontab/排程器登記（照 CLAUDE.md 鐵律 cat -n 人審）、
確認 Shioaji 第幾條連線。**這步永遠是你。**

### 5b 上線驗收監控（開放式，跑 N 個交易日）

**裁判（待建，可與 preflight 共用骨架）**：`python scripts/paper_acceptance.py <策略>` →
逐項印 PASS/FAIL：heartbeat 新鮮、訊號檔按時更新、日報已產出且推送、log 無 ERROR/
裸單/幽靈鎖 pattern、成交紀錄全部真 tick。連續 N 日（建議 ≥5 個交易日）全 PASS → 收案。

操作：這段不是 goal 迴圈，是排程監控 — 每交易日收盤後跑一次驗收腳本（可掛 /loop、
排程器、或你手動）。任何一項 FAIL = 開一個迴圈 #1（bug 修復）處理，修完繼續數天數
（天數重算與否你判：資料側小修不重算、引擎行為修 = 重算）。
已知良性誤報先查 MEMORY：WallClock synth、DayORB 靜默掃描、休市日。

**你的驗收**：N 日滿 → 看累計 paper 統計（成本 gap、滑價 vs 交接文件預期）決定
續跑/轉 live 評估/砍。轉 live 永遠是獨立決策，不在本迴圈範圍。

---

## 迴圈 #6 — 資料源 fetcher 合約測試（hardening，一次性閉環）

**目標**：repo 內所有外部資料 fetcher（FinMind / TAIFEX 官網 HTML / TAIFEX OpenAPI /
shioaji 歷史）每支都有 contract test 防禦。動機 = 這是最高頻 bug 家族（≥7 次事故，
含 TAIFEX HTML 改版 → 幽靈單真錢 -96k）：資料源會變，fetcher 至今裸奔。

**每支 fetcher 的合約（每項對應真實事故）**：
- 具名表頭定位欄位，**禁止位置索引**（c[13] 吃到最佳賣價事故）
- 語義檢查：抓到的值要像那個欄位（OI 非負整數、價格在合理區間、日期跟得上今天）
- 空回應 / 非 200 / 表頭改名 → **fail-closed**（回 None+告警），不准回髒資料繼續跑
- 金鑰缺失必須炸或告警，不准靜默用不到的路徑（13 支腳本 vps api.txt 事故）
- 週末/假日回 0 筆不准覆寫既有歷史（FinMind 週日洗空 chips history 事故）

**裁判**：`python -m pytest tests/test_fetcher_contracts*.py -q` 全綠 + 全套 `tests/` 不退步。
**測試不打真網路** — 用存檔 fixture（正常樣本 + 人造壞樣本：改表頭/挪欄位/空表/亂碼），
測的是 fetcher 面對變化的行為，不是資料源今天心情。

```
先讀 .claude/skills/loop-method/SKILL.md 和 LOOP_MEMORY.md。

任務：(1) 盤點 repo 所有外部資料 fetcher，列清單（檔案:函式:資料源:用途）給我看;
(2) 每支補 contract test（合約五條見 LOOP_PLAYBOOK #6），fixture 進 tests/fixtures/;
(3) fetcher 不滿足合約的 → 修 fetcher（具名表頭/語義檢查/fail-closed），不是弱化測試。
完成條件：python -m pytest tests/ -q 全綠，且盤點清單上每支 fetcher 都有對應測試
⚠️ 修 fetcher 行為時：VPS 上在跑的同名腳本以 repo 版為準 rsync 是之後的事，本迴圈
只改 repo。已知刻意設計（如 fail-open 純 chips）先查 MEMORY 再動。
失敗處理：同型失敗 3 次停;發現某 fetcher 修復需要改資料源帳號/金鑰 → 列出問我
邊界：不打真網路壓測、不碰 crontab / VPS / live。收工：更新 LOOP_MEMORY.md，commit
```

**你的驗收**：跑 pytest 看綠；掃一眼盤點清單有沒有漏（比對 MEMORY 裡出過事的源頭
是否都在列）。之後零散的每日檢查（stale 告警、mp 不一致、13:50 錄製自檢）可收攏成
一支資料品質守門日報 — 那是獨立小任務，不在本閉環。

---

## 迴圈 #7 — 靜默失敗撲殺（掃描型閉環）

**目標**：消滅「該炸的地方選擇沉默」。動機 = ≥6 次事故全是事後才發現：引擎多條
靜默 return 漏單、TG .env 掉兩行告警全靜默、金鑰 cp950 壞位元靜默讀不到、chips
stale 靜默漏單。規格書 = 你的既有偏好「TG 全事件覆蓋」。

**裁判（已就緒）**：`python scripts/scan_silent_failures.py` → `SILENT: 0` + exit 0。
AST 掃 bare-except 與純吞噬 except 區塊；allowlist = `scripts/silent_allowlist.txt`
（每筆附理由）收留正當沉默 — 已知良性案例（WallClock synth 補空檔、DayORB 靜默掃描）
進 allowlist，**不是「修」它們**。
基線（2026-07-07 Fable 試跑）：**SILENT: 137**，含 notify.py swallow（= TG 靜默事故
同型）、engine.py bare-except。迴圈的工作 = 把 137 分類歸零。
掃描器 v1 只抓兩型；「except 只 log.debug」「回空值不檢查」等進階 pattern 之後加。

```
先讀 .claude/skills/loop-method/SKILL.md 和 LOOP_MEMORY.md。

任務：跑 python scripts/scan_silent_failures.py（已建，基線 137），逐點分類：
真問題 → 改 fail-loud（推 TG/Discord 告警或 exit 非 0）+ 回歸測試;
正當沉默 → 進 scripts/silent_allowlist.txt 附一行理由。迭代到 SILENT: 0。
完成條件：python scripts/scan_silent_failures.py 印 SILENT: 0 且 exit 0，
且 python -m pytest tests/ -q 全綠
⚠️ 分類拿不準的（改了可能影響 live 引擎行為）→ 集中列一批問我，不要自行判斷後直接改。
allowlist 不是垃圾桶 — 每筆理由要能說服人，濫塞 = 弱化檢查器。
失敗處理：同型失敗 3 次停。邊界：不碰 crontab / VPS / live 引擎的下單路徑邏輯
（告警可以加，決策邏輯不動）。收工：更新 LOOP_MEMORY.md，commit
```

**你的驗收**：跑掃描器看 SILENT: 0；**重點審 allowlist** — 那是 agent 替自己開的門，
逐筆看理由；抽 2-3 個改 fail-loud 的點確認告警真的會推（測 raw URL / 假造一次失敗）。

---

## 迴圈 #8 — 歷史資料更新到最新交易日（積木全現成，回測前必跑）

**目標**：回測前把 1min 月檔更新到最後一個交易日（規則「回測一律跑到最新交易日」的
機械化）。**更新器與裁判都已存在**，這個迴圈只是把它們接成「更新 → 驗證 → 才准回測」。

**零件**：
- 更新器：`scripts/fetch_history_kbars.py` — quota-safe（雙閘門預設 420MB/保留 70MB）、
  分月斷點續抓、交易時段拒跑保護 live、0-bars 急停+TG。
  ⚠️ 預設跳過已存在月檔 → **當月檔會 stale，必須對當月用 `--force` 重抓**。
- 裁判：`scripts/check_kbar_gaps.py <SYM> --from <起> --to <最後交易日>` → `GAPS: 0`
- 下游（要回測才做）：5m 衍生檔重生成、tape 重結算（`resettle_5m.py`）

```
先讀 .claude/skills/loop-method/SKILL.md 和 LOOP_MEMORY.md。

任務：把 <TMFR1,TXFR1,...> 的 1min 月檔更新到最後交易日。
流程：(1) 先跑 check_kbar_gaps 看缺哪段;(2) fetch_history_kbars 補 — 歷史月正常抓、
當月加 --force;(3) 重跑 check_kbar_gaps。
完成條件：每個 SYM 都 python scripts/check_kbar_gaps.py <SYM> --from <90天前>
  --to <最後交易日> 印 GAPS: 0
⚠️ 鐵則：交易時段不跑抓取（腳本預設會擋，禁止用 --allow-trading-hours 繞過）;
撞 0-bars 急停 = 疑似配額切斷，停下告警不硬重試;補不回來的段（源頭無資料,
如 TMFR1 上市前）記進 LOOP_MEMORY 已驗證區,不算 GAP 失敗。
若之後要回測：資料綠了才准動 — 再重生成 5m 衍生檔與 tape(resettle_5m),
且 tape 要跟 live 對齊(參 #3a)。
失敗處理：同型失敗 3 次或配額告警 → 停下來問我
邊界：不碰 crontab / VPS / live;抓取只在非交易時段。收工：更新 LOOP_MEMORY.md，commit
```

**兩種跑法**：(a) 回測前 on-demand — 把上面模板當回測任務的前置段;(b) 每日排程 —
可掛本機工作排程器在收盤後自動跑（排程登記 = 人閘，比照 #5;掛好後這迴圈就從
「每次手動」變「永遠是新的」）。

**你的驗收**：跑 check_kbar_gaps 親眼看 GAPS: 0 且 --to 是最後交易日;瞄一眼
fetch log 的配額用量（每月成本印在輸出裡）。

**已知陷阱**（都吃過虧）：quota 超量永豐只切歷史查詢會回 0 bars;Shioaji 連線
5 條/身分證,更新器占 1 條;TMFR1 歷史起點 2024-07;6 月起 tick CSV 與月檔雙軌,
resample 落地規則見 _resample_ticks_to_1min.py docstring(右邊界/volume>0/試撮剔除)。

---

## 共通備忘

- breakout 家族的搜索空間 bounds 已建：`data/wfo_bounds_breakout.json`（judge_wfo `--bounds` 用）。新策略家族照樣從自己的 suggest_fn 抽一份。
- 各卡裁判狀態（2026-07-07 Fable 全套試跑）：#1 pytest 265 綠 ✓、#2/#4 judge_wfo 含 --smoke/--bounds ✓、#3b check_kbar_gaps ✓（並抓到 TXFR1 5/28-29 真缺口）、#7 scan_silent_failures ✓（基線 137）。**#3a reconcile_tape、#5 preflight/acceptance、#6 fixture 測試 = 仍待首輪建**。
- 所有迴圈產物只 commit 不 push；push 由你決定。
- 「同型失敗 3 次停下來問我」是每個模板的保險絲 — agent 停了就去看它卡在哪，別直接叫它再試。
