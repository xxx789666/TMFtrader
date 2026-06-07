# 忠實回測方法論 — Decision Recorder(2026-06-07)

> 一句話:**回測判「edge」有效、判「單筆」無效;decision recorder 把 live 真值錄下來,讓之後的回測逐筆對齊 live、不再白算。**

---

## 1. 問題:重寫一套回測 → 必然跟 live 跑掉

回測會偏離 live,不是 bug,是「用 pandas/precompute **重新實作**了一套 bar/指標」的必然結果。已實證的 5 個誤差源:

| 誤差 | replay(pandas 重寫)| live(真引擎)| 後果 |
|---|---|---|---|
| bar 建法 | pandas resample tick(label 對齊整點)| TickAggregator 逐 tick 建、bar 收盤才觸發 | OHLC/視窗差一格、OR 邊界與突破點不同 |
| 進場時間 | 用 bar 標籤時間 | bar **收盤**才動作(00:00 那根其實 01:00 才收)| 差一根 |
| ATR/ADX 暖身 | 連續跑 / precompute 壞掉(ADX=0)| 每日 14:50 重啟、~2000 根 kbars 重暖 | OR 寬度/ADX 閘**翻盤** |
| 成交價 | bar 收盤價 | tick 市價 | 崩盤單筆差上百點 |
| 每日重啟 | 無(連續)→ OR 錨點異常、盤末強平拖到 08:46 | 每日重啟、從 15:00 乾淨建 OR | session/OR 錯位 |

**2026-06 實例:同一筆 06-05 崩盤,我的 pandas replay 給出 +26,273 / 0 / 0 三個答案,live log 實際是 -6,500。三個全錯。**

---

## 2. 關鍵分界:回測能答什麼、不能答什麼

| 問題 | 回測 | 為什麼 |
|---|---|---|
| **有沒有 edge?該不該上?**(6 年、上百筆)| ✅ **可靠** | 樣本大 → 單筆 ±100 點誤差**互相抵銷**,不影響「PF 1.1 / 474 筆」結論 |
| **下週二晚會不會進、賺多少?**(單筆)| ❌ **不可信** | 單筆誤差**不抵銷**、直接翻盤 |

→ **go/no-go 用 lab 全期回測(有效);單筆/上線實況用 live log(唯一真相)。別拿回測答單筆。**

---

## 3. 解法:不要「重寫」,要「重放 live 那套 code」+「錄 live 決策」

### 3a. 引擎忠實回放(較近,仍不完美)
`scripts/replay_live_engine.py`:驅動**真 TradingEngine + 真 IndicatorEngine + 真 TickAggregator + 真 RiskManager**,餵歷史/錄製 tick。
```
python scripts/replay_live_engine.py --start 2026-05-10 --end 2026-06-05 \
       --strategy night_v7 --tf 30 --ticks-glob "data/ticks/TMF_2026060[1-6].csv"
```
關掉了表格的前 4 格;**殘留「無每日重啟」假象**(08:46 誤進/盤末強平拖到 08:46)未解 → 仍非逐筆對齊。

### 3b. ✅ Decision Recorder(這才是「有用的回測」基礎)
`core/decision_recorder.py` + engine hook(on_kbar 後)。`env RECORD_DECISIONS=1` 開(預設關、零影響)。

**每根 bar 把策略「當下看到的真值」整包寫檔** → `data/decisions/<owner>_<YYYYMMDD>.csv`:

| 欄位 | 內容 |
|---|---|
| bar_time, instrument, owner, strategy | 識別 |
| price, atr, adx, ema60, ema200 | **live IndicatorEngine 當下算出的真值** |
| or_hi, or_lo, or_ready, traded, trail_armed | 策略內部 OR/狀態 |
| signal, reason | 當下有沒有出訊號、原因 |

已在 day_v7 / night_v7 的 MXF launcher 開啟 → **週一上線即開始錄**。

---

## 4. 為什麼這才有用:live 真相 → 可驗證的 what-if

decision recorder **不是取代 live,是把 live 跟回測接起來**:

1. **消除最大誤差源**:回測不再「重算」ATR/ADX/OR → 直接讀 live 當時的真值 → 表格第 3 格(暖身)歸零。
2. **逐筆驗證**:重放結果 vs 決策帶逐筆對齊,差在哪根一眼看出 → 回測有了「對答案」的基準。
3. **可信的 what-if**:想試「min_adx 30→25 會多賺嗎」「OR 窗拉長會抓到崩盤嗎」,**拿 live 真值(snapshot)重跑策略邏輯**,只改參數、不動指標 → 結果可信、不翻盤。

→ **回測從「猜」變成「拿 live 真相做的可驗證 what-if」。這才是有用的回測。**

---

## 5. 保真度階梯(由差到好)

```
pandas resample + precompute        ← 我這幾天用的,5 格全踩,翻盤(白做)
  ↓
引擎忠實回放(replay_live_engine)   ← 真引擎/指標,關掉 4 格;殘留每日重啟假象
  ↓
decision tape 回放(讀 live 真值)   ← 消除指標/暖身誤差,可逐筆對齊;改參數的 what-if 可信
  ↓
paper 前推                          ← 真引擎、真行情、真路徑,只差沒下真錢
  ↓
live log(實單)                     ← 唯一 100% 真相
```

**規則:判 edge 用全期回測;判單筆/實況用 live log;做 what-if 用 decision tape;絕不用 pandas 重寫那套答單筆。**

---

## 6. 待辦
- [ ] 寫 decision-tape 回放器:讀 `data/decisions/*.csv`,只跑策略 `on_kbar` 邏輯(餵 live 真值 snapshot)→ 逐筆對齊 live、改參數做 what-if。
- [ ] replay_live_engine 補「每日 14:50 重啟」→ 清掉 08:46 假象。
- 相關:`core/tick_recorder.py`(錄原料 tick)、`core/decision_recorder.py`(錄決策)、memory `backtest_vs_live_fidelity_2026_05_30`。
