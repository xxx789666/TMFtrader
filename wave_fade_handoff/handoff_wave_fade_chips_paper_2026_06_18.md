# 交接(lab → live)— chips_combo × 波浪 fade 濾網 / forward paper / 2026-06-18

> **收件人**:`C:\Users\xx\Desktop\vps永豐微台指`(已在跑 settlement_v2_daily、maxpain paper)。
> **任務**:新增一條 **forward paper tape**——chips_combo 訊號 **+ 波浪 fade 濾網**,**微台(TMF)1口**執行,**先 paper 累積真樣本**;不動任何現有凍結策略。
> **依據**:`tmf-strategy-lab-main/docs/wave_fade_chips_filter_validation_2026_06_18.md`(完整驗證:過 kill-test+逐年6/7+MCPT校正p=0.0037+多空+窗口穩健高原+衰竭mechanism)。
> **定位**:**forward 候選、非確認 edge**。in-sample 漂亮(Sharpe 含停損 B2.30/C3.11),但**無 2015-19 真 OOS、設定是搜出來的** → **forward paper 才終審**,過關前微台 1 口、不放大、不進實質資金。

---

## 0. 一句話策略
chips 籌碼訊號照舊算;**進場前(05:00 夜盤收)用「日盤+夜盤連續 15 分 K 波浪方向」當濾網**:波浪與 chips **同向 = 該方向已走完一段完整推動結構(衰竭)→ 隔日大概率反轉 → fade(不做/減);逆向才做**。

## 1. 訊號規格(零新參數;濾網不改 chips 進出/止損)

| 項目 | 規格 |
|---|---|
| 訊號(不變) | chips combo=(z_flow 外資TX net OI Δ1d + z_lt 大戶淨/OI)/2,60日因果z;**\|combo\|>0.5 下單**,方向=sign(combo) |
| **濾網(新增)** | 進場前(T+1 開盤前最後一根≈**05:00 夜盤收**)算波浪方向:**日盤+夜盤連續 15m**、完整 impulse 引擎(ZigZag 1.0%→錨顯著起漲低→W0–W5→三法則)、**lookback 780根≈10交易日**→ +1多/−1淘汰/0不表態 |
| 進場 | **T+1 日盤 08:45 開盤**(波浪+籌碼在開盤前已知) |
| 出場 | **T+1 日盤 13:45 收盤平倉(不過夜)** + **−2% 盤中災難停損**(多看 low/空看 high,逐5m;原 chips 規則,全期觸發 7/761=尾部保險) |
| 口數 | **微台 TMF 1口**(paper 階段) |

**三政策(paper 階段三欄全記;上線選一)**:
| 政策 | 規則 | 定位 |
|---|---|---|
| A 不濾 | 全做(= 純 chips,對照組) | 基準 |
| **B 同向跳** | 波浪同向→跳、逆向+不表態→做 | **衝總報酬/最佳 Calmar(建議)** |
| C 只逆向 | 只做逆向(同向+不表態都跳) | 衝 Sharpe、頻率最低(~49筆/年) |

## 2. 歷年回測績效(微台 TMF pv10、含 −2% 停損、成本1pt、2020-06~2026-05 ≈5.94年)— 對帳基準

| 政策 | 筆/年 | 年化獲利 | PF | 勝率 | Sharpe | MaxDD | Calmar |
|---|--:|--:|--:|--:|--:|--:|--:|
| A 全做 | ~128 | **+19,185** | 1.29 | 53.2% | 1.40 | 31,861 | 0.60 |
| **B 同向跳** | ~83 | **+20,766** | 1.53 | 54.9% | 2.30 | 15,860 | **1.31** |
| C 只逆向 | ~49 | **+16,064** | 1.79 | 56.1% | 3.11 | 13,370 | 1.20 |

- 微台 = 大台 ÷20(大台1口:A年化+383,698 / B+415,326 / C+321,278)。年化報酬%**與口數無關**:**本金~5万/口時 B≈41.5% / C≈32%(IS);本金~10万/口時減半**。
- **−2%停損很少觸發(7/761)、價值在尾部保險**(防崩盤日單筆爆),對 B 的 MaxDD 不變、對 A/C 略增——別期待它縮常態回撤。
- ⚠️**全 in-sample;前推務必折半看**(無 OOS)。

## 3. 本金/保證金(微台)
- 微台 TMF 1口保證金 2026 高檔 ≈ **大台÷20 ≈ 1.5万上下**(以永豐實際公告為準,**請回報**)。
- 建議本金 = 保證金 + 前推回撤緩衝(IS MaxDD×1.5):B ≈ 1.5万 + 15,860×1.5 ≈ **~4-5万/口**。
- paper 階段不佔資金;上線微台 1口風險極小(最壞單日 −2% ≈ −0.4千/微台口)。

## 4. Paper tape 欄位(已有產生器,見 §6)
`tmf-strategy-lab-main/data/forward/wave_fade_log.csv`:
`signal_date, entry_date, combo, combo_dir, wave_dir, c1_dir, decA, decB, decC, asof, entry_open, exit_close, ret_bp, status`
- **每個交易日都寫一列**(含空手:combo_dir=0、decX=0)。
- `wave_dir`:+1/−1/0;`c1_dir`:C1 定律觀察欄(方向對但 in-sample t−0.54 不顯著,**只觀察、不下單**)。
- eval 回填 `entry_open/exit_close/ret_bp`(T+1 開→收;停損版另算)、`status=done`。

## 5. 每日執行流程(時序)
| 時點 | 事 |
|---|---|
| 訊號日 T ~15:00後 | chips OI 公布 → combo 算出 → 報告寫 `D:\...\chips_combo\T.md`(既有流程) |
| T 15:00→T+1 05:00 | 夜盤(含美股段),波浪引擎用連續15m看 |
| **T+1 ~05:00** | 夜盤收 → **波浪方向定格**(濾網偵測點) |
| **T+1 08:45 前** | 跑 `run_wave_fade.bat` → 三政策決定 + 寫 log |
| T+1 08:45 | 微台 1口 進場(或空手) |
| T+1 13:45 | 收盤平倉(+ 盤中 −2% 停損) |

排程:**早上 ~07:00 跑 `scripts\run_wave_fade.bat`**(內含 `--update` 抓最新夜盤 + 決策 + 回填昨日)。掛 Windows 工作排程器,交易日執行。

## 6. 交付清單
lab 已備(在 `tmf-strategy-lab-main`):
- [x] 決策+log:`scripts\wave_fade_forward.py`(讀最新chips報告→算波浪dir_full切點B→三政策決定→寫log;`--update`用唯讀數據金鑰抓MXFR1最新1min)
- [x] 回填+滾動績效:`scripts\wave_fade_forward_eval.py`
- [x] 早晨排程:`scripts\run_wave_fade.bat`
- [x] 含停損回測對帳:`scripts\wave_fade_stop_pnl.py`、完整驗證 `wave_fade_validation.py`
- [x] 研究報告 `docs\wave_fade_chips_filter_validation_2026_06_18.md`

vps 端待做:
- [ ] 把 `run_wave_fade.bat` 掛上交易日 07:00 排程(或併進現有晨間流程)
- [ ] **微台執行模組**:讀 log 當日列 → 依選定政策(建議 B)→ 08:45 微台1口進場、13:45平倉、−2%盤中停損;記實際成交價/滑價
- [ ] 回報:永豐微台保證金實額、微台日盤滑價(高頻、成本敏感)
- [ ] 累積 forward 樣本

## 7. 風險與已知保留(務必讀)
1. **forward 候選、非 edge**:in-sample 過 MCPT,但**無 2015-19 真 OOS、切點B/15m/1.0% 是搜出來的** → 真實會打折,**微台 1口、不放大**。
2. **頻率低、樣本累積慢**:B~83筆/年、C~49筆/年 → **至少跑滿一整年**才有統計意義,別早下結論。
3. **成本敏感**:chips 本身薄 edge(RR~1.17)、微台高頻 → **滑價實測是 paper 核心**;微台日盤滑價若大,edge 會被吃。
4. **兩條實作鐵則(lab 踩過的坑,務必遵守)**:
   - **波浪一律用 `dir_full`**(輪流試多錨點);用簡化單錨版會把該 +1 的誤判成不表態。
   - **日內資料不可有缺口**:缺口會讓視窗回補到更早、引擎誤判 → `run_wave_fade.bat` 的 `--update` 每次抓近 20 天涵蓋,避免缺口。
5. **C1 觀察欄只記不交易**(in-sample 方向對但不顯著)。
6. **2-trade 抽查**(6/9多/6/16空):資料完整時濾網**正確避開 6/9 那筆 −20,850**(同向→跳),但 6/16 逆向照做仍虧(逆向桶 WR 57%、43% 照虧)→ **濾網是統計聚合 edge、逐筆不保證**。

## 8. 通過標準(何時從 paper 轉實質)
累積 **≥ 一年(~50-80筆)** forward 後檢查:
1. 選定政策(B/C)forward Sharpe 方向與回測一致(別期待 IS 的 2.3/3.1,守得住 >1 即合格);
2. 「波浪逆向 vs 同向」forward 仍有 fade 分離(同向桶明顯較差);
3. 實際滑價未吃光 edge;
4. 無系統性執行問題。
→ 全過:考慮放大口數/進組合;任一不過:回拋 lab。

連結:`docs/wave_fade_chips_filter_validation_2026_06_18.md` · `docs/strategy_chips_combo_v1.md` · memory `wave-gann-filter-tests`
