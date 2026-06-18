# wave_exec — chips_combo × 波浪 fade 濾網 / 引擎真 tick paper / MXF 小台(2026-06-18 上線)

**定位**:lab `wave_fade_chips_filter_validation_2026_06_18.md` 的 forward 候選(**非確認 edge**:無 2015-19 真 OOS、切點B/15m/1.0% 是搜出來的)。**paper-only、不放大、≥1 年(~50-80 筆)才終審**(handoff §8)。占用第 5 條(最後一條)永豐連線。

## 架構(signal-file 橋接,比照 chips_exec/maxpain_exec)
```
chips_combo_daily.py (cron 18:30, HTTP籌碼)  →  data/chips_combo/next_signal.json (combo, trade_date)
                                                          │
wave_fade_daily.py (cron ~07:00)  ── 讀 combo ────────────┘
   └─ shioaji 唯讀數據金鑰抓 MXFR1 1min 近25日 → 連續15m → dir_full 波浪(切點B、1.0%、780根)
   └─ 三政策(A不濾/B同向跳/C只逆向)→ 政策B → data/wave_fade/next_signal.json + decisions.csv
                                                          │
start_wave_exec_paper.sh (cron pre-open ~08:22)  ── 引擎讀 side ──┘
   └─ WaveExecStrategy(MXF 1口): 08:45 真tick進場 / −2% 引擎硬停 / 13:44 收盤強平 / 不過夜
   └─ 真實成交價+滑價序列 → data/paper/wave_exec/(handoff §7.3 = paper 核心)
```

## 政策(producer 三欄全記;執行 = `WAVE_POLICY` 預設 B)
| 政策 | 規則 | IS 績效(微台 pv10;**MXF ×5**) |
|---|---|---|
| A 不濾 | 全做(純 chips 對照) | 年化 +19,185 / PF1.29 / Sharpe1.40 |
| **B 同向跳** | 波浪同向→跳、逆向+不表態→做 | 年化 **+20,766** / PF1.53 / **Calmar1.31**(建議) |
| C 只逆向 | 只做逆向 | 年化 +16,064 / PF1.79 / Sharpe3.11(頻率最低) |

⚠️ 全 in-sample,前推折半看;MXF pv50 → 上表 ×5(但年化%與口數無關)。

## 部署 runbook(VPS,2026-06-18)
1. **rsync 程式**(只碼、**勿同步 data/**):
   `strategy/wave_filter.py`、`strategy/wave_exec.py`、`scripts/wave_fade_daily.py`、
   `scripts/start_wave_exec_paper.sh`、`core/engine.py`、`scripts/morning_review.py`
   rsync 後 **`chmod +x scripts/start_wave_exec_paper.sh`**(Windows→Linux 不帶 exec bit、git 內亦 644)。
2. **.env 數據金鑰**(producer 抓 kbar 用、唯讀):VPS `.env` 需有
   `WAVE_DATA_API_KEY` / `WAVE_DATA_SECRET_KEY`(或既有 `SHIOAJI_DATA_*`;最後 fallback 引擎主金鑰)。
   來源 = 本機 `vps api.txt` 的「數據」段。**無金鑰 → 波浪 fail-open 退化成純 chips(政策A)、印警告**。
3. **cron**(VPS crontab 用 **UTC**、TST=UTC+8;務必 `crontab -l > /tmp/ct.bak; 編輯; crontab /tmp/ct.bak`,**絕不**用 `crontab -l|grep -v|crontab -` 自刪 pattern,2026-05-28 landmine):
   ```
   5  0  * * 1-5  cd /home/xx/TMFtrader-src && TZ=Asia/Taipei .venv/bin/python3 scripts/wave_fade_daily.py >> data/logs/wave_fade_daily.log 2>&1   # 00:05 UTC = 08:05 TST
   23 0  * * 1-5  /home/xx/TMFtrader-src/scripts/start_wave_exec_paper.sh                                                                          # 00:23 UTC = 08:23 TST
   ```
   (producer 08:05 TST 在 chips 18:30、夜盤 05:00 收之後、launcher 08:23 之前;launcher 在 producer 之後、開盤 08:45 之前。producer 用 .env 隔離數據金鑰登入,不撞 live 主金鑰連線。)
4. **不手動重啟既有進程**(memory `vps_deploy_without_restart`);wave_exec 是新進程,首次靠 08:22 cron 起。
5. 連線預算:現 4 條(day_v7/night_v7/chips_exec/maxpain_exec)+ wave_exec = **5 條 = 上限**。
   producer 的 kbar 抓取是短暫登入(抓完 logout)、非常駐連線。

## 通過標準(handoff §8;何時轉實質)
累積 ≥1 年(~50-80 筆)後:① 政策B forward Sharpe 守得住 >1;② 波浪「逆向 vs 同向」仍有 fade 分離;
③ 實際滑價未吃光 edge;④ 無系統性執行問題。全過→考慮放大/進組合;任一不過→回拋 lab。

## 已知保留
- forward 候選非 edge;頻率低(B~83筆/年×?…MXF 同筆數)、樣本累積慢。
- chips 本身薄 edge(RR~1.17)、MXF 高頻 → **滑價實測是核心**。
- 抽查:資料完整時濾網正確避開 6/9 那筆大虧(同向→跳),但 6/16 逆向照做仍虧 → 濾網是統計聚合、逐筆不保證。
- C1 觀察欄只記不交易。
- 連結:lab `docs/wave_fade_chips_filter_validation_2026_06_18.md`、handoff `wave_fade_handoff/handoff_wave_fade_chips_paper_2026_06_18.md`。
