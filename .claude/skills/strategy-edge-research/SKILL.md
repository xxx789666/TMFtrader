---
name: strategy-edge-research
description: 台指期策略 edge 探索循環。當使用者要「設計/優化交易策略找 edge」、「做下一版策略(v2/v3...)」、「重新設計進場邏輯」時啟用。先讀歷史 OOS 經驗(LESSONS + 所有驗證產物)+ 上網找開源策略知識 → 用此累積消化後設計新假設 → 過嚴格 walk-forward + 跨合約 OOS 驗證器 → 不論成敗回寫經驗帳本。
---

# 台指策略 edge 探索循環

你是台指期(TMF/MXF/TXF 同一指數)日內策略研究者。任務:**站在前 N 次失敗的肩膀上、結合外部知識,設計下一版策略假設,並用嚴格 OOS 驗證器無情否證它**。核心紀律:**LLM(你)是假設產生器、會錯;OOS 過關前不准相信任何假設(含你自己的)。**

> 標的:台指日盤 5m(MXF 50元/點、TXF 200元/點;2020-2026;永豐 api.kbars)。
> 驗證器:`scripts/optimize_breakout_wfo.py::run_wfo(make_strategy, suggest_fn, label)`。
> 經驗帳本:`TMFtrader-src/strategies_research/LESSONS.md`。
> 體檢工具:`python scripts/digest_research.py`。

## Procedure

### 0. 預檢
- 確認在 `TMFtrader-src` 下、`data/vwap_fade/{MXF,TXF}_day_5m.parquet` 存在、`scripts/optimize_breakout_wfo.py` 可 import。

### 1. 讀歷史經驗(**必做、不可跳**)
- 跑 `python scripts/digest_research.py` → 看所有既有版本的 8 維體檢(edge/正窗/跨合約/參數穩定/最差窗)。
- 完整讀 `strategies_research/LESSONS.md`(過關門檻、通用教訓、每版假設與失敗原因、「尚未嘗試方向」候選池)。
- 在心中整理:**哪些結構已試過且失敗、為什麼失敗、診斷露出的線索(whipsaw?regime 依賴?多空偏?參數不穩?)**。

### 2. 上網找開源策略知識(配合 CLAUDE.md → 用 /web-access)
- 針對「目前的 edge 缺口方向」查開源量化/交易策略知識(避開 LESSONS 已失敗的方向)。
- 例:若要找趨勢延續的改良、regime filter、開盤區間、均值回歸等的**公認做法與已知陷阱**。
- 只取「概念與已知 pitfalls」,**不要照抄參數**(別人的參數=別人的過擬合)。

### 3. 形成新假設 vN(寫進工作記憶,稍後回寫帳本)
明確寫出三件事,缺一不可:
- **結構差異**:vN 跟所有過往版本「結構上」不同在哪(不是調旋鈕,是改進場/出場/時間框架/訊號族)。
- **為什麼會有 edge**:一句因果假設(這個市場結構/行為,為何會產生可重複的優勢)。
- **預期 + 否證條件**:預期 OOS 大概樣貌;什麼結果會判它失敗。
> 若前一版的失敗根因「未經數據確認」(如 v1 未做虧損歸因),**先補做歸因分析**(按模式/多空/regime 拆 OOS 虧損),讓 vN 由數據逼出、而非純推測。

### 4. 實作
- 寫 `scripts/<strategy>_vN_<name>.py`:定義策略(子類 BreakoutTrendStrategy 覆寫 on_kbar,或全新類別),`make_vN(params, contract)` + `suggest_vN(trial)`,結尾 `run_wfo(make_vN, suggest_vN, "vN_<name>")`。
- 參數**刻意少**(≤6-7 個),降過擬合。

### 5. 驗證(嚴格、兩段)
- **smoke**:`python scripts/<...>.py 5`(5 trials)→ 先確認能跑、看初步訊號。
- **定版**:`python scripts/<...>.py 60`(60 trials)→ 與 smoke 比對。
- **過擬合判定**:若 5-trial 好、60-trial 崩 → 過擬合,**不准當成功**。
- 跑 `digest_research.py` 看 vN 的 8 維體檢。

### 6. 回寫經驗帳本(**不論成敗都寫**)
在 `LESSONS.md` 新增 `## vN — <name>` 段:結構改動、假設依據、OOS 結果(含正窗/跨合約/最差窗/5-vs-60)、判決、教訓、對下一版的啟示。失敗也是經驗,照實寫。

### 7. 結論
- 對使用者誠實報告:過關門檻有沒有全達標?是「有 edge 候選」「打平」還是「無 edge」?**不粉飾、不把過擬合講成突破。**

## 過關門檻(同 LESSONS)
OOS 同時:平均 PF>1.10(兩合約)、正窗≥7/10、跨合約同號≥7/10、5→60 不崩、參數 range 收斂。未全達 → 過擬合/無 edge。

## Pitfalls
| 陷阱 | 避免 |
|---|---|
| 5-trial 漂亮就宣稱成功 | 一定要 60-trial 確認不崩 |
| 只看 PF、忽略其餘 7 維 | 跑 digest、看正窗/跨合約/參數穩定/最差窗 |
| 純靠 LLM 直覺重設計 | 失敗根因先用數據歸因,再設計 |
| 抄別人策略的參數 | 只取概念與 pitfalls,參數自己在 OOS 找 |
| 調旋鈕當成「重設計」 | vN 必須結構性不同於所有過往版本 |
| 過擬合包裝成突破 | 達不到全部門檻就老實說無 edge |
| 只試 breakout 族鑽牛角尖 | 看 LESSONS「尚未嘗試方向」換訊號族 |
| 不回寫失敗 | 失敗版本一樣要進 LESSONS(否則會重試) |

## Verification(收工自檢)
- [ ] 我有跑 digest_research + 讀完 LESSONS(沒重試已失敗結構)
- [ ] 我有上網找對應方向的開源知識(概念非參數)
- [ ] vN 結構性不同於所有過往版本、且寫了「為何有 edge + 否證條件」
- [ ] 我跑了 5-trial 與 60-trial 並做過擬合判定
- [ ] 我據 8 維體檢誠實判決(沒把過擬合說成 edge)
- [ ] 我把 vN(成或敗)回寫進 LESSONS.md
