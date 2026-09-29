# 交接(lab → VPS 永豐微台指) — A_struct 夜盤閘門法 現況 / 2026-06-23

> **狀態:PAPER、人工為主(每日 Discord 審核)、無 live、VPS 端目前無新動作要做。**
> 這是回應你 Day-1 feedback(`feedback_to_lab_day1_2026_06_23.md`:A_struct「下跌日逆勢做多」)的 **lab 端解法**,已建成完整每日管線、跑在 dev 機(非 VPS)。

---

## 1. 解法定調
- **問題**(你回報):A_struct 偵測在下跌日逆勢做多(6/23 09:35 LONG 48301 → 停損 −79pt)。根因=偵測無盤中趨勢/當日錨意識。
- **lab 解法**:**夜盤波浪結構閘門 + 當日 08:45 低錨**。先用夜盤定方向與「破壞位」,隔日開盤沒站對邊就不做多 → 直接擋掉逆勢單。
- **6/23 實測**:夜盤=空頭/破壞位 48926;6/23 開 48800 ≤ 48926 → 空頭延續 → **不做單**(正確避開那筆虧損)。

## 2. 偵測規格(已鎖定;資料 MXF202607 永豐近月、1分K)
**① 夜盤**(前一交易日 22:00 → 進場日 04:55):
- 方向:絕對最高**先**出現→空頭(浪0=高);最低先→多頭(浪0=低)。**不用 %。**
- 破壞位:市場結構追蹤的「最後一個逆勢轉折」(空=最後反彈高 w2/w4、多=最後回檔低)。
- **艾略特上限:推動浪最多 w0~w5**(依波浪理論教學)。固定%級數太細會亂數→用**自適應級數**(門檻由細往粗,直到 ≤5 波)。
- abstain(交人工):①碎盤(破壞位跨門檻不穩)②無回檔(只 w0)。

**② 閘門**(進場日 08:45 開盤 vs 破壞位,只做多):
- 空頭:開 > 破壞 → 翻多做;開 ≤ → 不做。
- 多頭:開 ≥ 破壞 → 做;開 < → 不做。

**③ 日內**(過閘門才做,1分K):浪0=08:45 低;掃 08:45–09:45 首筆 0-1-2(浪0<浪2<浪1、L1≥0.15%、ret2∈20~80%);浪2 確認市價進、TP=浪2+1.618×L1、SL=浪2 下;一天一筆。

## 3. 每日運作(全自動 in dev 機,VPS 不用碰)
```
05:20  偵測夜盤(w0~w5)+自畫K線圖+破壞位+閘門 → 推 Discord #a-struct
  ↓   (排程 Astruct_morning;腳本 Astruct_daily.py)
人工   8:30 前在 Discord 私訊(DM) A_struct bot 審核:ok / 改空 break X / 跳過
  ↓   (openab-astruct bot 自然語言 → Astruct_decisions.csv)
13:50  讀決定→08:45閘門→1分K掃0-1-2→紙上進出場(小台pv50)→推結果+寫tape
  ↓   (排程 Astruct_close;腳本 Astruct_exec.py + Astruct_report.py)
日報   D:\vps自動化交易每日籌碼分析報告\每日籌碼分析報告\Astruct\{日}.md
       (判讀+圖+我的審核+進單盈虧) + _績效報告.md(累積回測)
```

## 4. ⚠️ 誠實限制(很重要)
- **Elliott 固定%自動化撞牆**:6 年掃描,程式「乾淨夜」自動可做僅 ~23%(0.3%)~41%(0.15%峰),**59%+ 夜需人工判讀**(碎盤/無回檔 abstain)。→ 這是**人工為主、程式輔助**的方法,不是全自動 alpha。
- 同 lab 其他線:**無 2015-19 OOS、forward paper 才終審**。Sharpe/績效都是 in-sample 觀察,別當實盤預期。
- 人工數浪靠判斷,程式只逼近;碎盤夜程式會 abstain 舉手交人。

## 5. VPS 端要做什麼?
- **現在:什麼都不用做。** lab 端自包含跑 paper(偵測/推送/審核/紙上執行/報告全在 dev 機 + Discord)。
- 你原本的 A_struct paper(cron 08:25、MXF/TF5/fixed1、EMA250)=**有逆勢 bug 那版**:可繼續跑當對照,或停掉(由你決定;新夜盤閘門法不在 VPS、不衝突)。
- **live 仍暫緩**。等夜盤閘門法 forward paper 累積足夠、且通過你我檢視,再談 VPS live launcher 整合。屆時交接「閘門+日內」執行模組給 VPS。

## 6. 檔案位置(dev 機 lab)
- 偵測/推送:`scripts/Astruct_daily.py`(+`run_astruct_morning.bat`)
- 日內執行:`scripts/Astruct_exec.py`
- 報告:`scripts/Astruct_report.py`(+`run_astruct_close.bat`)
- 狀態檔:`data/forward/Astruct_queue.csv`(自動讀)、`Astruct_decisions.csv`(人工審核)、`Astruct_tape.csv`(紙上單)
- Discord stack:`D:\discord 個人助理\openab\`(config-astruct.toml、openab-astruct 容器)、vault `D:\discord 個人助理\AStructSpace\`
- 記憶帳本:lab memory `astruct-nightgate-discord.md`

---
**一句話**:lab 已把你回報的逆勢問題用「夜盤閘門+當日錨」解掉並建成每日 paper 管線(自動偵測+人工 Discord 審核+紙上結算+回測報告),**VPS 端維持現狀、live 暫緩,等 forward paper 證明再議**。
