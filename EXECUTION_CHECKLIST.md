# 永豐微台指 — 自動化交易 + Hermes 週度覆盤 執行清單

> **這份文件**：伴隨「第一天用 VPS → 實際用真錢下單成功」全旅程的執行紀錄與下一步指南。
>
> 建立 2026-05-12 ｜ Hermes 覆盤已上線 ｜ **Phase 8 後期、Phase 9 待 5/20 啟動** 🚀
>
> **最新進度（2026-05-15 收線）**：
> - 交易（VPS）：start.py 已 emergency kill、watchdog disabled、夜盤 ORB 24:00 前還會跑、之後週末完全靜默
> - 修法 D（broker.heartbeat dead-zone aware）已 commit `1839db4`、disk 上 VPS、5/18 08:30 cron 自然套用
> - LESSONS_LEARNED 完整事件覆盤已寫（[[shioaji-kbars-api-zero-bars]] / quota 三波處理）
> - **切 live 日延後**：5/18-5/19 雙日 paper 驗證 → **5/20 (Wed) 切實單**（給修法 D 兩次 dead zone 實戰驗證）

---

## 📖 旅程地圖

| 階段 | 日期 | 狀態 | 里程碑 |
|---|---|---|---|
| Phase 0-7 | 5/12 | ✅ 完成 | WSL2 + Hermes Agent + NIM + Telegram + Watchdog + Secret 善後 |
| Phase 8 Day 0-1 | 5/12-5/13 | ✅ 完成 | GCP VPS 開機、Shioaji 登入成功、雙策略 paper 上線 |
| Phase 8 Day 2-4 | 5/13-5/15 | ⏳ 完成 + 補救 | Paper 觀察、**quota 兩次爆量事件、修法 A/C/D 完整覆盤**（[LESSONS_LEARNED_2026_05_14_15_quota.md](LESSONS_LEARNED_2026_05_14_15_quota.md)）|
| Phase 8 Day 5 | 5/18 (Mon) | ⏰ 待跑 | Paper 驗證日 1：修法 D 第一次實戰、quota < 500 MB + ≥1 paper 進場 |
| Phase 8 Day 6 | 5/19 (Tue) | ⏰ 待跑 | Paper 驗證日 2：修法 D 穩定性、Go/No-Go 評估、永豐客服回覆 |
| **Phase 9** | **5/20 (Wed)** | ⏰ **GO 切實單** | `TRADING_MODE=live`、第一筆真錢下單 🚀 |
| Phase 10 | 5/20+ | - | 首單成交回報驗證、24h 嚴密監看 |
| Phase 11 | 5/21-5/26 | - | Live 首週、確認 PnL 正確、覆盤對齊 |
| Phase 12 | 5/27+ | - | 持續運維、每週六 Hermes 覆盤 |

---

## ⏰ 今日 (5/13 三) 進度

- [x] **API 開通測試**：本機 IP 撞 `Sign data is timeout` → 改 VPS 跑、5 分鐘審核通過、`futopt_account.signed=True` ✅
- [x] **看 VPS 夜盤過夜結果**：22:05 session 啟動、23:10 區間建立 [41010,41383] / 3.82×ATR、之後無突破訊號（CSV 空、ML filter 未放行）✅
- [x] **修 VPS 日盤 broker Options bug**：`fetch_contract=True` 強制下載 Options 撞權限 → patch 改 False + 手動 fetch_contracts、09:23 重啟成功、TMF tick 即時進來 ✅
- [x] **Hermes orchestrator 接 VPS**：`.env.sync` 改 VPS_HOST、TMF_DATA_ROOT 切到 sync 目錄、rsync 12 個 daily JSON、load_week dry-run 對 ✅
- [x] **commit + push fix**：`cfdd758` Fix Shioaji Options fetch ✅

## ⏰ 接下來時間軸

| 時 | 動作 |
|---|---|
| 09:30–13:45 | VPS 日盤 TMF tick / 訊號自動跑、看 TG |
| 14:55 | VPS cron 自動重啟夜盤 |
| 21:30–04:00 | MXF 夜盤 ORB session、有訊號就 paper 下單 |
| **5/14 早** | 看 `data/performance/daily/2026-05-13_*.json` 整日紀錄 |
| 5/14–15 | 持續 paper 觀察、TG 監看 |
| **5/16 Sat 09:00** | Hermes 自動跑首份來自 VPS 資料的覆盤 |
| **5/20 Wed 08:30** | 切 `TRADING_MODE=live` 🚀 |
>
> **未來**（VPS 租用後）：交易 EA 搬到 VPS、資料寫 VPS，本機 WSL2 透過 `sync_from_vps.sh` 拉資料；本架構同時支援這兩種模式（orchestrator 偵測有沒有 `.env.sync` 自動切換）。
>
> Stack：**Hermes Agent**（Nous Research、2026-02 發佈）+ NVIDIA NIM provider
> 覆盤頻率：**每週六 09:00 (Asia/Taipei)** 一次（Windows Task Scheduler 觸發），聚合上週 Mon-Fri 的日盤+夜盤
> 專案模組：`ultra-trader-src/review/`（資料層）+ `hermes_skills/`（skill）+ `scripts/`（orchestrator + 可選 sync）

## 部署架構速覽（**VPS 已上線、5/13 進入 paper 觀察期**）

```
☁️ GCP VPS: ultratrader-night (asia-east1-b, e2-small, 35.221.239.245 靜態 IP)
  /home/xx/ultra-trader-src/
    ├─ scripts/restart_{day,night}.sh    Linux 啟動腳本
    ├─ scripts/paper_night_orb.py        夜盤 ORB B2 ML strategy
    ├─ scripts/start.py                  日盤 FastAPI 8888
    ├─ scripts/watchdog.py               12-min 心跳監控
    ├─ deployed_strategies/tmf_orb_night/orb_filter_b2.pkl   B2 ML model
    ├─ certs/cert.pfx                    Shioaji 憑證
    ├─ .env                              TRADING_MODE=paper（即將 5/20 切 live）
    └─ .venv (Python 3.12.13 + Shioaji 1.3.3 + xgboost/lightgbm)
  crontab (UTC):
    30 0 * * 1-5    日盤 08:30 啟動
    55 6 * * 1-5    夜盤 14:55 啟動
    56 6 * * 1-5    Watchdog 啟動
    10 21 * * 0-4   夜盤 05:10 關（隔日）

🖥️ 本機 Windows
  └─ WSL2 Ubuntu (~/.local/bin/hermes)
      ├─ ~/vps_trader        → /mnt/c/.../vps永豐微台指    (本專案、git tracked)
      ├─ ~/vps_trader_paper  → /mnt/c/.../永豐-自動化交易  (本機 paper、雙重備援)
      ├─ ~/.ssh/google_compute_engine  ← SSH key 連 VPS
      └─ ~/.hermes/                                 Hermes runtime + NIM provider

每週六 09:00 (Asia/Taipei)：
  Windows Task Scheduler "TMF_Weekly_Review"  (wake-to-run, 30 min cap)
    └─ scheduled_trigger.ps1
          └─ wsl.exe -d Ubuntu -- bash -lc 'cd ~/vps_trader && bash scripts/run_weekly_review.sh'
                ├─ [0]  kill_switch 預檢
                ├─ [1]  sync from VPS（5/13 後：rsync xx@35.221.239.245:~/ultra-trader-src/data/）
                ├─ [2a] python load_week → JSON
                ├─ [2b] bash 格式化成 facts
                ├─ [2c] curl NIM hosted → 繁中報告 ★直接 API★
                ├─ [2d] curl Telegram → 你手機
                └─ [3]  runaway_guard (≤8 tool calls / 10 min / 50k tokens / 30 RPH)
```

## 進度總覽

- [x] **Phase 0** 架構釐清（含全面 pivot 到 Hermes Agent）
- [x] **Phase 1** 資料層 MVP（`loader.py` + `tools_for_hermes.py`，含 `load_daily` + `load_week`）
- [x] **Phase 2** 第一版 skill 檔（`hermes_skills/tmf-weekly-review/SKILL.md`，含 budget + kill-switch 自律規則）
- [x] **Phase 2.5** 失控防禦 —— skill 預檢 + 外部 watchdog（`review/runaway_guard.py`）
- [x] **Phase 3** 本機 WSL2 安裝 Hermes Agent + 接 NIM ✅
- [x] **Phase 4** 部署 skill + Windows Task Scheduler 排程 ✅
- [x] **Phase 4.5** orchestrator 內整合 runaway_guard ✅
- [x] **Phase 5** Telegram 整合（**改用 curl 直推、不繞 Hermes gateway**）✅
- [x] **Phase 7（中途插入）** Secret leak 善後 —— TG token + Shioaji key redact、刪 repo 重建乾淨版 ✅
- [/] **Phase 8（進行中）** Day 0-1 完成 ✅（VPS 上線）｜ 5/13 跑 API 開通測試 + Hermes 接 VPS ｜ 5/13–15 paper 驗證 ｜ 5/20 切實單 🚀
- [ ] **Phase 6** Hermes 覆盤上線驗證（paper 4 週 → 開放低風險自動套用）— 第 1 週已自動跑、繼續觀察 3 週

## 改週度的決策（2026-05-12）

| 項目 | 原本（每日） | 現在（每週） |
|---|---|---|
| 觸發頻率 | 14:15 日盤 + 05:30 夜盤 + 週日週度 = 3 條 cron | **週六 09:00 = 1 條 cron** |
| 平均樣本 | 單 session 1–2 筆（N=1 是噪音）| 全週 5–10 筆（有統計意義）|
| LLM 呼叫量 | 每月 ~44 次（22 日 × 2 session）+ 4 次週度 | 每月 ~4 次 |
| NIM 用量 | ~44 calls/月（會撞 rate limit 的可能性高）| **~4 calls/月**（rate limit 容量的 0.001%）|
| 動參數的觸發門檻 | n_trades ≤ 3 才 observe-only | n_trades < **5** 才 observe-only |
| 跨日模式偵測 | 弱（只看單日）| **強**（best/worst day、連虧日數、跨日訊號對比）|

---

## 上線過程關鍵發現（2026-05-12，照時序）

實際做下去發現很多原計畫沒考慮到的事，逐一記錄：

| # | 發現 | 影響 / 修法 |
|---|---|---|
| 1 | Grok 截圖描述的 Hermes Agent **是真的**（我知識截止 1 月、它 2 月發佈）| 全面 pivot 到 Hermes Agent，省 80% 自寫 |
| 2 | NVIDIA NIM 已**廢除 credits 制度**、改 forever-free + ~40 RPM rate limit | 附錄 A 改為「rate-limit 退路」、不是「credits 燒完」 |
| 3 | 手機驗證後 NVIDIA 給「**Unlimited API, no daily limits**」| 比 forum 提的 40 RPM 寬鬆，週度用量 ~4 calls/月實質無限 |
| 4 | VPS（GCP e2-small 2GB）跑 Hermes 會搶 EA 記憶體 | 改本機 WSL2 跑 |
| 5 | **VPS 根本還沒租** | orchestrator 加偵測：無 `.env.sync` → 跳過 sync、直接用本機資料 |
| 6 | `vps永豐微台指/` 是**打包快照**、實際 live EA 在 `永豐-自動化交易/` | 加 `TMF_DATA_ROOT` env var 支援、`~/vps_trader_paper` symlink 指 live |
| 7 | WSL2 已預先裝好（Ubuntu 24.04 + Python 3.12） | Phase 3 半數步驟跳過 |
| 8 | `hermes setup` wizard 重跑會把 key value **串接**（70 → 210 字元）| 直接覆寫 `~/.hermes/.env` 修 |
| 9 | Hermes **execute_code 沙箱 CWD 不固定** | skill 改用 `subprocess.run(... env={'PYTHONPATH': ...})`、不用 `cd` |
| 10 | `hermes chat -Q` 對純摘要 prompt 常回**空 response**（Nemotron 對大塊 JSON 不友善）| 跳過 Hermes、orchestrator 直接 curl NIM API |
| 11 | Hermes `send_message` 需 gateway daemon 持續跑 | TG 推送改 bash 直接 curl Telegram、不依賴 daemon |
| 12 | TG bot token **硬編在 4 個 .py/.md 內**、且**被 push 到 GitHub** | 用 `gh search code` 確認後選方案 A：刪 repo 重建乾淨版 |
| 13 | PowerShell 5.1 讀 UTF-8 無 BOM `.ps1` 含中文會 **parser error** | 移除 .ps1 內所有中文字串、用 `$PSScriptRoot` 動態取路徑 |
| 14 | `secret_scan.sh` 對 209 檔跑 13 patterns 太慢（2700 grep 呼叫）| 後續可優化、當前用直接 grep 替代 |
| 15 | VPS 日盤 broker 撞 `SecurityType.Option` 載入失敗 → engine.start bail | 改 `login(fetch_contract=False)` + 手動 `fetch_contracts()` try/except、見 [[shioaji-options-fetch-fix]] |
| 16 | Shioaji 開通測試 Windows 本機 IP 撞 `Sign data is timeout` | 改用 VPS IP（35.221.239.245）跑、5 分鐘審核通過、`futopt_account.signed=True` |

## Phase 0 — 架構釐清 ✅

- [x] 確認 self-host MoE 大模型不可行（GCP e2-small 無 GPU）
- [x] 發現 NousResearch **Hermes Agent**（2026-02-25 發佈、95.6K stars）
- [x] 確認 Hermes 官方支援 NVIDIA NIM、MiniMax、OpenAI-compatible endpoints
- [x] 確認 Hermes 內建 cron、FTS5 跨 session 記憶、Telegram gateway、skill auto-evolution
- [x] **全面 pivot**：放棄自寫 OpenAI agent loop，改用 Hermes Agent 完整路線
- [x] **NVIDIA 計費政策更新（2026-05 查證）**：build.nvidia.com 已廢除 credit-based 系統，改為 rate limit（~40 RPM）forever-free，個人開發用無時間限制；生產級才需 NVIDIA AI Enterprise 授權
- [x] 退路：極端情況撞 rate limit 或 NVIDIA 改政策，Hermes 一條指令切 OpenRouter / Groq / 任意 OpenAI-compatible

---

## Phase 1 — 資料層 MVP ✅

> 角色：Hermes Agent 透過 `execute_code` 呼叫此模組取得結構化交易資料 + Python 端統計（**LLM 不算數字**）。

- [x] `ultra-trader-src/review/__init__.py` — 模組定位說明
- [x] `ultra-trader-src/review/loader.py` — `load_daily` + `load_week`，算 WR/PF/MFE/MAE + 跨日聚合
- [x] `ultra-trader-src/review/tools_for_hermes.py` — CLI JSON 介面：`load_daily` + `load_week` subcommand
- [x] Inf / NaN sanitize（避免破壞 JSON）
- [x] `load_daily` 對 5/11 資料驗證輸出正確（PF=null、net=2340.0）
- [x] `load_week` 對 5/11–5/15 那週驗證正確（3 筆、+3510、WR 66.7%、PF 3.85、best=5/11、worst=5/12）

### 舊 MVP 已清理

- [x] `review/llm.py` 已刪（Hermes 取代）
- [x] `review/cli.py` 已刪（Hermes 取代）
- [x] `review/report.py` 已刪（Hermes gateway 取代）
- [x] `review/prompts.py` 已刪（轉成 skill 檔）

---

## Phase 2 — 第一版 Skill 檔 ✅

- [x] `hermes_skills/tmf-weekly-review/SKILL.md` 已建立
- [x] Frontmatter 含 `name / description / version / platforms / metadata.hermes.{tags,category,config}`
- [x] Body 含 `When to Use / Procedure / Pitfalls / Verification` 四段
- [x] 規則寫死：`n_trades < 5` 只能 observe-only、禁止 LLM 算數字、禁止編造、必對 best/worst day 縱深、必查近 4 週記憶、circuit_state 異常先排查

---

## Phase 2.5 — 失控防禦 ✅

> **威脅模型**：即使 NVIDIA NIM 是 forever-free，agent 卡迴圈（同樣的 tool 反覆呼叫、自我對話無限延長）仍可能在單次 cron 內打爆 rate limit、或燒掉幾百萬 token 而毫無進展。三層防禦如下：

### 三層防禦架構

| 層 | 在哪 | 怎麼擋 | 失效情境 |
|---|---|---|---|
| **L1 Skill 自律** | `hermes_skills/tmf-weekly-review/SKILL.md` | 寫死 `tool_calls ≤ 8`、`max_minutes=10`、開頭預檢 kill_switch | LLM 沒遵守 prompt |
| **L2 外部 Watchdog** | `review/runaway_guard.py` + 系統 cron（週六 09:20）| 解析 Hermes output，超門檻就寫 `~/.hermes/kill_switch` + 推 TG | guard 自己沒跑 |
| **L3 緊急斷路** | `~/.hermes/kill_switch` 檔 | Skill 第 0 步必檢，存在即停。`rm` 才會恢復 | kill_switch 被誤刪 |

### L1 — Skill 自律規則（已加進 SKILL.md）

- [x] Procedure 加 step 0「預檢 kill_switch」
- [x] Procedure 加 step 0.5「預算宣告：tool_calls ≤ 8、wall 10 分鐘、max_tokens=2048」
- [x] Pitfalls 加 5 條防迴圈規則（禁重複查同 (date,session)、超預算強制出報告、ignore halt 等）
- [x] Verification 加自我審計（我跑過預檢嗎？我 ≤ 8 次嗎？）

### L2 — 外部 Watchdog（已寫好）

- [x] `ultra-trader-src/review/runaway_guard.py` 已實作
- [x] 門檻可由 env var 覆寫（`GUARD_MAX_TOOL_CALLS / GUARD_MAX_MINUTES / GUARD_MAX_TOKENS / GUARD_BURST_LIMIT_1H`）
- [x] Hermes 未安裝時 graceful skip（不誤觸發）
- [x] dry-run 模式測試通過

**門檻設計（比 Skill 寬一點，留容錯）：**
| 指標 | Skill 預算 | Watchdog 門檻 | 餘裕 |
|---|---|---|---|
| tool_calls / 單次 | 8 | **12** | +50% |
| minutes / 單次 | 10 | **10** | 0 |
| total_tokens / 單次 | 暗示 ~16k | **50,000** | 寬 |
| 1 小時內任務數 | n/a | **30** | 防 cron 卡死後重複觸發 |

### L3 — Kill-switch 檔

- [x] 觸發後路徑：`~/.hermes/kill_switch` 內含 ISO 時間 + 違反原因
- [x] 解除方式：`rm ~/.hermes/kill_switch`
- [x] Skill 開頭強制檢查、存在即停且推 TG

---

## Phase 3 — 本機 WSL2 安裝 Hermes Agent + 接 NIM ✅

### 申請 + 取得 API key ✅

- [x] 到 https://build.nvidia.com 申請 NVIDIA Developer，拿 `nvapi-...`
- [x] 手機驗證完成（驗證後得到 **Unlimited API requests, no daily limits** —— 比 forum 提的 40 RPM 更寬鬆）
- [x] 我們週度只用 ~4 calls/月 → 永遠不會撞限制
- [x] **本機 .env 已注入** `NVIDIA_API_KEY` / `NVIDIA_BASE_URL` / `NIM_MODEL_DEFAULT` / `NIM_MODEL_DEEP`
- [x] **連線測試通過**（2026-05-12）：
  - List models 回 129 個 model
  - 目標 model 全部在線：`minimaxai/minimax-m2.7` / `deepseek-ai/deepseek-v4-pro` / `nvidia/llama-3.3-nemotron-super-49b-v1`
  - 額外發現便宜款：`deepseek-ai/deepseek-v4-flash`（中文好、cheap）
  - 真實 inference 測試成功（26 tokens, "pong"）
- [ ] ⏰ **2026-11-12 API key 到期前換新**（在 Hermes 加一條 cron 提醒、或行事曆設）

### 為何不裝 VPS

VPS 是 GCP e2-small **2GB RAM**，現有 UltraTrader（Shioaji + FastAPI + watchdog + pandas）已用掉 1.2–1.5GB，剩餘 RAM 不足以再塞 Hermes Agent（Python 3.11 + SQLite + Node + sandbox 工具，需 300–500MB）。記憶體競爭可能拖累交易延遲、甚至觸發 OOM。

**改本機 WSL2** 跑 Hermes，VPS 只負責交易與寫 JSON。

### WSL2 安裝（本機 Windows，PowerShell 系統管理員）

- [ ] `wsl --install -d Ubuntu`（若已裝過跳過）
- [ ] `wsl --set-default-version 2`
- [ ] 重開機 → 首次啟動 Ubuntu 設定 username / password
- [ ] 進 WSL2 後：`sudo apt update && sudo apt upgrade -y`
- [ ] `sudo apt install -y curl git rsync openssh-client python3 python3-pip jq`
- [ ] 確認 Python：`python3 --version`（需 ≥ 3.10）

### 建立 WSL2 內專案 symlink

> 直接讀 `/mnt/c/...` 含中文路徑，rsync / Hermes 容易出 encoding 問題。改用純 ASCII symlink。

- [ ] 在 WSL2 內執行：
  ```bash
  ln -s "/mnt/c/Users/xx/Desktop/vps永豐微台指" ~/vps_trader
  ls ~/vps_trader/   # 應該看到 ultra-trader-src/, hermes_skills/, scripts/, ...
  ```
- [ ] 給 sync 腳本執行權限：
  ```bash
  chmod +x ~/vps_trader/scripts/*.sh
  ```

### 安裝 Hermes Agent（WSL2 內）

- [ ] `curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash`
- [ ] `source ~/.bashrc`
- [ ] `hermes --version` 確認安裝成功
- [ ] `hermes setup`（首次設定，會問 default provider / model）

### 設定 NIM provider（WSL2 內）

- [ ] 從本機 `.env` 把 NVIDIA_API_KEY 複製進 Hermes config：
  ```bash
  source ~/vps_trader/ultra-trader-src/.env
  hermes config set NVIDIA_API_KEY "$NVIDIA_API_KEY"
  hermes config set NVIDIA_BASE_URL "https://integrate.api.nvidia.com/v1"
  ```
- [ ] `hermes model`（互動切換預設 model）
  - 推薦預設：`nvidia/llama-3.3-nemotron-super-49b-v1`
  - 高階備援：`minimaxai/minimax-m2.7`、`deepseek-ai/deepseek-v4-pro`、`deepseek-ai/deepseek-v4-flash`

### Smoke test

- [ ] `hermes` 進 TUI，問「ping」確認 NIM 回應
- [ ] 確認 Python 端工具能跑（Linux Python 版）：
  ```bash
  cd ~/vps_trader/ultra-trader-src
  pip install python-dotenv
  python3 -m review.tools_for_hermes load_week --week_ending=2026-05-15 --compact
  ```

### SSH key 設定（WSL2 → VPS）

- [ ] WSL2 內生 key（若沒有）：
  ```bash
  ssh-keygen -t ed25519 -C "wsl2-tmf-review" -f ~/.ssh/id_ed25519 -N ""
  ```
- [ ] 把 public key 傳到 VPS（**只允許 read-only 區域**）：
  ```bash
  cat ~/.ssh/id_ed25519.pub
  # 複製 → 貼到 GCP Console: Compute Engine → Metadata → SSH Keys，或：
  gcloud compute os-login ssh-keys add --key-file=~/.ssh/id_ed25519.pub
  ```
- [ ] 測試連線：`ssh root@<VPS_IP> "ls ~/ultra-trader-src/data/performance/daily | head"`

---

## Phase 4 — 部署 skill + Task Scheduler 排程 ✅

### 部署 skill（WSL2 內 symlink）✅

- [x] `mkdir -p ~/.hermes/skills`
- [x] symlink 建好：`~/.hermes/skills/tmf-weekly-review → ~/vps_trader/hermes_skills/tmf-weekly-review`
- [x] `hermes skills list` 確認偵測：tmf-weekly-review (local, enabled)
- [x] paper data symlink：`~/vps_trader_paper → /mnt/c/.../永豐-自動化交易`（指向 live EA）

### 路徑與環境變數 ✅

- [x] `~/vps_trader/scripts/.env.sync` 已建（含 TG_BOT_TOKEN / TG_CHAT_ID）
- [x] orchestrator 自動偵測：無 VPS_HOST → 跳過 sync、用 `~/vps_trader_paper` 內 live 資料
- [x] `TMF_DATA_ROOT` env var 由 orchestrator 自動 export

### 手動測完整 orchestrator ✅

- [x] WSL2 跑 `bash ~/vps_trader/scripts/run_weekly_review.sh`
- [x] 全 3 步通過：
  - [x] Step 1: 跳過 sync（無 VPS、正確行為）
  - [x] Step 2a-2d: load_week → facts → NIM API → TG curl
  - [x] Step 3: runaway_guard 通過
- [x] **TG 手機收到繁中報告**（+5,880 / 4 筆 / WR 75% / 最佳日 5/11）

### Windows Task Scheduler 排程 ✅

- [x] `scripts/install_task_scheduler.ps1`（已重寫為 ASCII、用 `$PSScriptRoot`）
- [x] `scripts/scheduled_trigger.ps1`（已重寫為 ASCII）
- [x] PowerShell 跑 `install_task_scheduler.ps1` → Task 已註冊
- [x] **`Start-ScheduledTask` 觸發測試 → LastTaskResult=0、TG 二次收到**
- [x] State: Ready / WakeToRun: True / ExecutionTimeLimit: 30 min
- [x] **NextRunTime: 2026-05-16 (Sat) 09:00** ← 第一次正式自動跑
- [ ] 確認電源計畫「允許喚醒計時器」= 啟用（如桌機平常睡眠、必做）

---

## Phase 5 — Telegram 整合（**改用 curl 直推、不繞 Hermes gateway**）✅

### 設 bot（在 WSL2 內）

- [ ] 從 `~/vps_trader/ultra-trader-src/.env` 抄 TG bot token + chat id
- [ ] `hermes gateway setup`（互動輸入）
- [ ] 把 gateway daemon 設成 WSL2 系統服務（避免 WSL2 重啟後不啟動）：
  ```bash
  # 方案 A：直接背景跑（適合手動）
  nohup hermes gateway start > ~/.hermes/gateway.log 2>&1 &

  # 方案 B：systemd（若 WSL2 已啟用 systemd，更穩）
  # /etc/systemd/system/hermes-gateway.service
  ```
- [ ] 用手機發任意訊息給 bot，確認 echo 回來
- [ ] **重要**：因為 Hermes 跑在本機，TG bot 只在 WSL2 開機 + gateway daemon 跑時才會回應。若你 desktop 關機/睡眠 → bot 不會回。覆盤推送則用我們 orchestrator 內的 curl 推（不依賴 daemon）。

### 整合 orchestrator 推送

- [ ] 確認 `scripts/sync_from_vps.sh` 失敗時會推 TG
- [ ] 確認 `scripts/run_weekly_review.sh` 各步完成 / 失敗時會推 TG
- [x] 確認 `runaway_guard.py` 觸發時會推 TG（已實作於 `runaway_guard.py` 內）
- [x] **改用 curl 推 TG**（不繞 Hermes gateway daemon）—— orchestrator step 2d 直接 `curl https://api.telegram.org/.../sendMessage`
- [x] 訊息格式人類友善（NIM 直接產繁中段落、不是 raw JSON）

### 為什麼不用 Hermes gateway

實測 `hermes chat -Q` 對「純摘要」prompt 常回空 response（empty model output）。而且 `send_message` tool 需要 `hermes gateway start` daemon 持續跑 —— WSL2 重開 daemon 沒起來就 silent fail。**改用 bash 直接 curl** 反而最可靠、最少依賴。Hermes 留著但只用於將來互動式覆盤（人工 `hermes chat` 進 TUI）。

---

## Phase 4.5 — runaway_guard 整合到 orchestrator（L2 外部監控）✅

> 原本規劃 runaway_guard 用獨立系統 cron 跑，但既然 orchestrator 是序貫流程，直接把 guard 串成 orchestrator 第 3 步更乾淨（已實作於 `scripts/run_weekly_review.sh`）。

### orchestrator 內整合（已完成）

- [x] `run_weekly_review.sh` 第 0 步預檢 `~/.hermes/kill_switch`
- [x] `run_weekly_review.sh` 第 3 步呼叫 `python3 -m review.runaway_guard`
- [x] 失敗時自動推 TG 警告

### 故意觸發測試（pre-deploy）

- [ ] 設低門檻跑一次：
  ```bash
  GUARD_MAX_TOOL_CALLS=0 bash ~/vps_trader/scripts/run_weekly_review.sh
  ```
- [ ] 確認：
  - [ ] `~/.hermes/kill_switch` 被建立
  - [ ] TG 收到警告
  - [ ] orchestrator log 顯示 guard 觸發

### 猴子測試 —— skill 第 0 步真的會停嗎？

- [ ] 手動建 kill_switch：`echo "manual test" > ~/.hermes/kill_switch`
- [ ] 跑 orchestrator：`bash ~/vps_trader/scripts/run_weekly_review.sh`
- [ ] 確認 orchestrator step 0 看到後**直接退出**，沒進 step 1 sync
- [ ] 清除：`rm ~/.hermes/kill_switch`

### 真實 output 格式校準（Hermes 安裝後做）

> `runaway_guard.py` 正則目前是寬鬆 fallback，跑過真實 cron 後可能要對齊格式：

- [ ] 跑 1 次真實覆盤、看 `~/.hermes/cron/output/<job_id>/<ts>.md` 長相
- [ ] 對應調整 `_TOOL_CALL_PATTERNS / _TOKEN_PATTERNS / _DURATION_PATTERNS`
- [ ] 確認真實 tool_calls 數能被解析（不會 false negative）

---

## Phase 6 — 上線驗證

### Paper 階段（4 週 — 週度覆盤 N=4 才有比較基礎）

- [x] **第 0 次（手動觸發 dry-run，2026-05-12）**：TG 收到報告
  - [x] 數字對：+5,880 / 4 筆 / WR 75% / best=5/11 / max_consec_loss=0 跟 `load_week` 一致
  - [x] 建議具體：「考慮調整短倉比例」「優化停損」
  - [x] 走 curl 直推（不繞 Hermes gateway）—— TG 推送成功
- [ ] **第 1 週（首次自動跑：2026-05-16 Sat 09:00）**：等實際跑
  - [ ] Task Scheduler 真的喚醒 / 觸發
  - [ ] 數字跟 `load_week --week_ending=2026-05-15` 一致
  - [ ] 有無幻覺（編造訊號類型 / 參數 / 不存在的訊號）
  - [ ] 若 `n_trades < 5`，建議是否都是 observe-only
- [ ] **第 2–3 週**：觀察跨週模式
  - [ ] 報告品質是否穩定（不時好時壞）
  - [ ] 是否抓得到本週與上週的對比
  - [ ] orchestrator 失敗時 TG 是否真的會推警告
- [ ] **第 4 週**：累積基礎後評估
  - [ ] 整體建議品質是否穩定提升
  - [ ] 信心校準：高信心建議是否真的更準

### 用量觀察（不是成本問題，是反常偵測）

> NVIDIA 已是 forever-free + rate limit，週度 ~4 calls/月幾乎不可能撞限。但仍要記錄用量當作「agent 行為異常」的訊號。

- [ ] 連續 4 週記錄每週的 token 用量 + 工具呼叫次數
- [ ] 若某週工具呼叫 > 30 次或 token > 預期 3×，**很可能 agent 卡在迴圈** → 看 `~/.hermes/cron/output/` 排查
- [ ] 觀察是否曾收到 NVIDIA 端的 `429 Too Many Requests` 或 rate limit 警告

### 進階（觸發條件達成才做）

- [ ] 寫第二支 skill `tmf-strategy-tune`：接受 `tmf-daily-review` 的建議、產出 unified diff、TG `/approve_<hash>` 才套用
- [ ] 串 pytest gate：套用前必過全綠
- [ ] 套用後 git auto-commit、3 天 PnL 惡化自動回滾

---

## Phase 7 — Secret leak 善後 ✅（2026-05-12 同日插入處理）

### 為何插入 Phase 7

第一次 push `vps永豐微台指` 到 GitHub `xxx789666/VPS--` 時，pre-flight scan **漏抓 TG bot token + chat_id**（硬編在 `core/notify.py` × 2 + `watchdog.py` × 2 + `README.md`）。即使 .gitignore 擋住了 `.env` / `*.pfx` 等檔，hardcoded 在 source 內的 secret 沒擋。

### 處理動作

- [x] 4 個檔 redact 為從 env var 讀（`vps永豐微台指/` repo 內）
- [x] 4 個檔 redact 為從 env var 讀（`永豐-自動化交易/` live EA 內，未在 git，但仍同步處理）
- [x] live EA `.env` 補上 `TG_BOT_TOKEN` + `TG_CHAT_ID`
- [x] 強化 `.gitignore`：補 `scripts/.env*`、`scripts/_*.sh`（測試用 helper）、`scripts/logs/`
- [x] 寫 `scripts/secret_scan.sh`：13 patterns × 整 staged tree、push 前必跑
- [x] **方案 A 處理 history**：`gh repo delete` + `rm -rf .git` + fresh `git init` + `gh repo create` + push 乾淨版
- [x] 驗證：`gh api search/code?q=<TG_TOKEN_PREFIX>+repo:xxx789666/VPS--` → **0 hits**
- [x] 新 repo 只有 1 commit：`ccfdc92 Initial commit (re-init after secret leak cleanup)`
- [x] 使用者選擇**不 rotate token**（評估 private repo + 沒 collaborator 風險可接受）

### 教訓（已寫進 memory）

memory: `secret_scan_must_cover_hardcoded.md` —— push 前不只看 .gitignore，要 grep 整個 staged tree 找 token/key 字串。

---

## Phase 8 — VPS 實戰部署（Day 0-1 完成 ✅、3 天 paper 驗證進行中）

> **目標**：5/12 啟動 → 5/15 前完成 VPS paper 初次驗證 → 5/18-5/19 雙日 paper 最終驗證 → **5/20（週三 08:30）切實單開盤**
>
> **為何走這條**：桌機 24/7 開機風險（藍屏 / 斷網 / Windows update）。VPS 在台灣彰化機房、< 5ms 到 Shioaji、月費 NT$490。

### Day 0 預檢狀態（2026-05-12 已查）

| 項 | 狀態 |
|---|---|
| Shioaji API Key + Secret | ✅ 都有（`.env` 內） |
| 憑證 `Sinopac.pfx` | ✅ `C:/Users/xx/Downloads/Sinopac.pfx` 存在（4/9 下載、約 2027/4 到期）|
| 憑證密碼 + 身分證號 | ✅ 都在 `.env` |
| GCP 帳戶 | ❌ **沒有、需註冊**（拿 $300 / 90 天免費試用）|
| 永豐期貨下單權限 | ⚠️ **未確認、user 需查**（電子戶 → 我的服務 → 開通服務）|
| TG bot token / chat id | ✅ live EA `.env` 已補 |

### Day 0（你做）✅ 完成（除 API 測試明日跑）

- [x] 永豐電子戶確認「**期貨/選擇權電子下單**」已開通
- [x] **簽署「API 電子交易風險預告書暨使用同意書」**
  - 詳細條款摘要 + 系統合規檢查見 memory: [[sinopac-api-consent]]
- [x] **跑 Shioaji API 開通測試** ✅ 已通過 (5/13 09:15)、`futopt_account.signed=True`
  - 時段：08:00–20:00（18:00–20:00 限台灣 IP）
  - 指令（在 Windows PowerShell 跑、用既有 .env）：
    ```
    cd "C:\Users\xx\Desktop\永豐-自動化交易\ultra-trader-src"
    python "C:\Users\xx\Desktop\vps永豐微台指\scripts\shioaji_api_test.py"
    ```
  - 腳本流程：login (sim=True) → activate_ca → 取 TXF 近月 → Buy 15000 / 1 口 / ROD → sleep 2s → cancel
  - 通過條件：`signed=True` + `ca_ok=True` + `status=Submitted`
  - **審核約 5 分鐘**（隨到隨審）→ 通過後 simulation=False 才能跑實單
  - 完整規格：https://sinotrade.github.io/zh/tutor/prepare/terms/
- [x] 註冊 Google Cloud
  - [x] 用 Gmail (`x011training@gmail.com`) 登入
  - [x] 啟用 $300 免費試用
  - [x] Project ID: `project-ae93c5d6-cf6e-402d-969`

### Day 0 同步同意書合規檢查（已查、全綠）

| 條款 | 對我們系統的要求 | 狀態 |
|---|---|---|
| 條 1（程式錯誤自負）| 3 天 paper 驗證 + watchdog 必要 | ✅ Phase 8 已安排 |
| 條 2（key / 不全權委託）| key 不入 git、不交第三方、Hermes 不能下單 | ✅ 已 redact + skill 限制 |
| 條 3（委託即正式、需主動查）| 每筆 push TG + JSON 紀錄 | ✅ notify.py |
| 條 6（**有線網路、不宜 Wi-Fi**）| 桌機 Wi-Fi 不合規 → 必須遷 VPS | ⭐ **這就是切 live 必走 VPS 的合規依據** |
| 條 7（永豐可逕行限流）| Shioaji 報錯先換備用 key + 換 IP、不狂重試 | ✅ [[shioaji-login-ban-diagnosis]] |
| 條 9（**嚴禁行情轉散布**）| TG 只推私人 chat、GitHub 不放 raw tick | ✅ historical/ 在 .gitignore |

### Day 0–1（Claude 做）✅ 完成

- [x] **gcloud CLI 裝 WSL2 內**（v568.0.0）
- [x] **開 VM** `ultratrader-night` 在 `asia-east1-b`（e2-small / Ubuntu 22.04 / 50GB SSD pd-balanced）
- [x] 設靜態 IP `ultratrader-ip` = **35.221.239.245**
- [x] 啟用 Compute Engine API + 確認 billing
- [x] 防火牆：建 `deny-trading-ports` 規則（block 8888/8889 對外、target trader-vm tag）
- [x] ufw：本機只允許 SSH 22
- [x] 裝 Python 3.12.13 + pip 26.1.1（deadsnakes PPA）
- [x] rsync 上傳 `ultra-trader-src/`（342 檔、52MB、排除 historical/data/logs）
- [x] rsync 上傳 `Sinopac.pfx` → `~/ultra-trader-src/certs/cert.pfx`
- [x] rsync 上傳 `deployed_strategies/` (B2 ML model)
- [x] 修 `.env`：`SHIOAJI_CA_PATH=/home/xx/ultra-trader-src/certs/cert.pfx`
- [x] 建 venv + `pip install -r requirements.txt`
- [x] 補裝 ML 依賴：xgboost / scikit-learn / lightgbm + sys libgomp1
- [x] **Shioaji simulation 登入測試成功**（Session up、期貨帳戶 `徐安利` 抓到）
- [x] 寫 Linux 啟動腳本：`restart_day.sh` / `restart_night.sh` / `start_watchdog.sh`
- [x] crontab：日盤 30 0 * * 1-5（UTC=TST−8）、夜盤 55 6 * * 1-5、watchdog 56 6 * * 1-5、收盤關 server
- [x] **22:03 啟動夜盤 ORB paper**：ML model 載入 OK、訂閱 MXF tick feed、PID 4619 running
- [x] paper log 寫入 `~/ultra-trader-src/data/paper_trading/night_orb_20260512.csv`

### Day 1（Claude 做、orchestrator 接 VPS）✅ 完成

- [x] `~/vps_trader/scripts/.env.sync` 改為 `VPS_HOST=xx@35.221.239.245` / `SSH_KEY=$HOME/.ssh/google_compute_engine` / `TMF_DATA_ROOT=$HOME/vps_trader/ultra-trader-src/data`
- [x] `bash sync_from_vps.sh` rsync 拉 12 個 daily JSON 到本機 sync 目錄
- [x] `load_week` dry-run 驗證：5/11–5/15 trades=4 net=+5,880 best=5/11 +4740 worst=5/12 +1140
- [x] 5/16 (Sat) 09:00 Task Scheduler 跑時、Step 1 sync 不再跳過、改 rsync VPS

### Day 2~4（你 + Claude，5/13 ~ 5/15）⏳ 進行中

**5/13（三）**
- [x] 早盤前看夜盤 5/12 結果：22:05 啟動、23:10 區間建立、無突破（CSV 空 = 預期）
- [x] 09:23 修好 broker Options bug、日盤重啟成功、TMF tick 即時進來
- [x] 13:45 收盤後檢查 `data/performance/daily/2026-05-13_live.json` 有沒有日盤交易 → paper 模式檔名為 `2026-05-13.json`、0 trade、`daily_pnl=0`（日盤無 breakout 訊號、正常市況）
- [x] 14:55 確認夜盤 cron 自動重啟 → syslog `06:55:01 CRON CMD restart_night.sh` + `06:56:01 start_watchdog.sh`（UTC=TST 14:55/14:56）、`paper_night_orb.py PID=10442` 14:55:04 起跑
- [x] **21:30 ORB session 啟動觀察** → 21:35:00 `[ORB] New session: 2026-05-13-N`、22:15:00 `[ORB] Range skipped: width=2.70×ATR (need 3.0-5.0)` 區間太窄、今夜停手（TG 應推「區間跳過」）。ORB process PID=15720 穩定跑、tick feed 即時、heartbeat=flat 全程乾淨、無 ERROR
- [x] **TG 全事件覆蓋（commit `44bbb90`）**：補三個缺口
  - (1) `risk/circuit_breaker.py` `on_connection_lost/restored` 加 `tg()` + `was_active/was_stopped` flag 防 spam
  - (2) `paper_night_orb.py` 加 monotonic tick heartbeat（120s timeout、21:00-04:30 主時段告警）
  - (3) `restart_day.sh` / `restart_night.sh` 結尾加 curl notify、cron 排程重啟也推 TG
  - (4) 新 `daily_status_ping.sh`（cron `50 5 * * 1-5 day` + `15 21 * * 0-4 night`）日報推 TG
  - (5) `restart_*.sh` 從 VPS-only 改為 git tracked
  - 部署完成、ORB session 未中斷、新 cron 已生效、TG 測試訊息已驗收

**5/14（四）**
- [x] **08:30 日盤 cron restart 驗證**：TG 收到「🔄 [Cron] 日盤 start.py 排程重啟 PID=49253」✅
- [x] **13:45 日盤收盤 broker spam 風波**：發現 circuit_breaker 在收盤 dead zone 震盪、8 則「[UltraTrader] 券商連線中斷/恢復」湧入 TG、commit `0f45ea7` 加 5 分鐘 cooldown、commit `3d687fd` rename [UltraTrader]→[Sinopac-Paper]/[Sinopac-Live] 動態 tag、PID 60541 重啟生效
- [x] **🚨 重大發現：日盤 0 trade 是 kbars API bug 不是市況**
  - log `[Shioaji] TMF 歷史 K 棒: 0 bars` (broker.py:727)、整個早盤 09:00-12:59 共 4.5 小時 [Scan] log=0、breakout.py:145 `if bar_count < 80: return None` 全程觸發
  - 進一步測試（`scripts/_test_kbars.py`）：TMFR1 / TXFR1 / MXFR1 / 股票 2330 / 古老日期、**全部 0 bars**
  - 結論：永豐 `api.kbars()` 帳號層級失能、不是合約 / 日期 / 程式碼問題
  - 影響：每日 08:30 cron restart 後策略要 6.5h warm-up 才能 scan、整個早盤幾乎 mute
  - memory: [[shioaji-kbars-api-zero-bars]]
- [x] **🚨 根因再深挖：`api.usage()` 流量配額爆量 318%**
  - bytes=1.59 GB / limit=500 MB (paper、0 成交額等級)、remaining=-1.07 GB
  - 完全符合官方「流量超量 → 行情查詢類 API 回空值」說法
  - 升級規則：近 30 日 API 成交額 ≥ 1 大台 (或 4 小台) 即升 2GB/日（官方文檔）
  - TMF 微台口數換算 / 自動 vs 申請 / paper 是否算成交 → 官方未寫、已 email 永豐
- [x] **14:30 現場處置：止血**
  - 手動 `kill` start.py PID 60541、註解 `vps_watchdog.sh` crontab → 完全停止 quota 累積
  - ORB PID 63301 仍在跑（14:55 cron 自動啟動、Shioaji 直連、不受 kbars 影響）
  - 預期：5/15 00:00 TST quota reset → 08:30 cron 自然恢復日盤
- [x] **修法 A 已 commit + rsync（commit `0892ac7`）**
  - `core/broker.py::_start_kbar_poller()` 加 env var `ENABLE_KBAR_POLLER` 開關、預設停用
  - VPS disk 已是新版（md5 對齊）、明早 08:30 cron 起的 start.py 才會載入
  - 預期節省：~10-50 MB/日（Solace 斷線時不再 REST fallback 浪費）
  - 副作用詳見 memory: [[vps-traffic-optimization-side-effects]]

**5/14（四）夜盤結果（事後補記）**
- ORB session 啟動 @ 21:35:00
- 22:15 區間建立成功：**[41735, 42086] width=4.42×ATR**（不像 5/13 第一次 skipped 太窄）
- 22:15 → 05:10 cron pkill、整夜**價格在區間內、零突破**、CSV 仍 header only
- 結論：5/14 夜盤真的無下單條件（無突破訊號、不是腳本問題）

**5/15（五）— 早上必做（按時間）**
- [x] ~~00:30 TST `_test_kbars.py` 驗時區~~（沒做、跳到 08:57 一起測、由 08:57 結果反推 quota 在 08:57 之前某點重置）
- [x] **08:57 TST 跑 `_test_kbars.py`** → **kbars 完全恢復**：TMFR1=1454 / 2330=798 / 昨日 TMFR1=1140 bars ✅
- [x] **api.usage() 確認 quota 重置** → bytes=30MB / limit=500MB / **remaining=493MB** ✅ 配額正常
- [x] **08:30 TST cron 自動 restart start.py**：PID=234932、TG 應收「🔄 [Cron] 日盤 start.py 排程重啟」
- [x] **🎉 修法 A 生效驗證**：log 有 `08:30:53 [KbarPoller] 已停用（節省流量、設 ENABLE_KBAR_POLLER=true 可復開）`、commit `0892ac7` 真的在跑
- [x] **🎉 Warmup 完全成功**：`[Shioaji] TMF 歷史 K 棒: 3722 bars`、合成 `404 bars for 5m / 138 bars for 15m`、`last_price=42360.0`、ema200=42077（不再是 0）
- [x] **[Scan] 從 8:35 就開始**：09:00 前已 10 個 [Scan] log、整個早盤策略 active、**不再 mute 4.5 小時** 🎉
- [x] **08:57 TST 解註解 watchdog cron**：`* * * * * /home/xx/ultra-trader-src/scripts/vps_watchdog.sh` 已恢復
- [ ] **13:50 TST**：TG 收「📊 [日盤日報]」、trades 應該 ≥ 1（如果 breakout 真有訊號 fire）
- [ ] **14:55 TST**：TG 收「🌙 [Cron] 夜盤」、ORB 上線
- [ ] **21:30 TST**：ORB session 啟動觀察
- [ ] 永豐客服回覆（已寄 email、9 個問題）

**5/15（五）— 隨時可做**
- [ ] **驗證 watchdog 自癒**：手動 `kill <paper_night_orb_PID>` → `vps_watchdog.sh` 預期 ≤ 2 分鐘內重啟 + TG 「🔧 paper_night_orb 重啟成功」
- [ ] **審查 5/13 restart 風暴根因**：16:02–17:25 vps_watchdog.log 顯示 start.py 8888 卡死。看：(1) 8888 為何 16:02 卡住 → broker login race? Shioaji reconnect? (2) commit `16123b9` mutex 真有效嗎 (3) 17:25 後靜默是 mutex 還是 lucky

**5/15（五）下午—緊急事件補記**
- [x] **13:45 第二次 quota 爆量 storm 重現**：bytes 從 144MB → 577MB（12 分鐘燒 433MB）
- [x] **發現真兇**：broker.py `_attempt_reconnect()` 每次都 `fetch_contracts(~50MB)`、broker.heartbeat 把「市場無 tick」當「斷線」、dead zone 內無限重試
- [x] **緊急止血**：kill start.py PID 246558、EMERGENCY DISABLE watchdog cron、quota 鎖在 577.9 MB
- [x] **修法 D commit `1839db4`**：broker.heartbeat 加 dead-zone aware（13:45-15:00 / 05:00-08:45 / 週末不偵測）+ reconnect 成功 reset `_last_tick_time`
- [x] **教訓 MD**：`LESSONS_LEARNED_2026_05_14_15_quota.md` 寫完、5 個錯誤 + 4 個反模式 + 真兇分析 + 週一驗證計畫
- [x] **VPS broker.py rsync 完**：disk 上是新版、明天 08:30 cron 自然套用、不手動重啟

**5/16（六）+ 5/17（日）— 週末完全休市、什麼都不用做**
- 5/16 早上 05:10 cron 會自動關掉昨夜 ORB（最後一次夜盤）
- 5/16 早上 05:15 daily_status_ping night 會推一則 TG「🌙 [夜盤日報]」
- 之後整週末 VPS 完全靜默（start.py 不在跑、watchdog 停、cron `1-5` 週末不 fire restart_day / restart_night）
- 5/16 09:00 TST Hermes 排程仍會跑「來自 VPS」覆盤、確認 TG 收到

**5/18（一）— Paper 驗證日 1 結算（事後紀錄）**

✅ **修法 D 真兇修對的鐵證**——五小時 quota 0 增加、修法 D-1 dead-zone aware 完美擋下 storm

| 時間 (TST) | 事件 | 結果 |
|---|---|---|
| 08:30:04 | cron `restart_day.sh` 自動跑、PID 268323 | ⚠️ `fetch_contracts partial`、TMF 找不到合約（永豐 reset 後 server 還沒穩） |
| 08:38:01 | watchdog 偵測 8888 not listening、10 min grace 開始 | 進入自癒流程 |
| 08:39:21 | 手動 restart_day.sh → PID 269001 | ✅ Contract TMFR1 / Subscribe / KbarPoller off / Warmup 1442 bars 全綠 |
| 09:05 / 14:00 | log_quota cron 自動量測 | bytes=28.6 MB（5 小時 0 增加）|
| **13:45-14:55 dead zone** | **修法 D-1 真實首秀** | **0 reconnect、0 fetch_contracts、quota 0 燒** 🔥 |
| 13:50 | daily_status_ping day TG | 推送正常 |
| 14:55:04 | cron `restart_night.sh`、ORB PID 280699 | ✅ Login Shioaji OK / Subscribe MXF |
| 21:30:00 | ORB session start: `[ORB] New session: 2026-05-18-N` | ✅ |
| **21:35:16** | 🚨 TG「[Sinopac-Paper] 券商連線中斷」 | **誤報**：實為 engine `_check_price_anomaly` 觸發（TMF 21:30→21:35 跌 52 點 ≥ 5×ATR）、label 寫錯 |
| 21:39:17 | 手動重啟 start.py → PID 294633 | ✅ circuit_breaker reset、broker 重連、quota 57 MB / 500 MB |

**5/18 quota 全日預估**：~100-130 MB / 500 MB（早盤 restart 燒 28 + 開盤異常 restart 14 + 夜盤 ORB tick ~60 = ~100）= **降 87%**（vs 5/14 同期 1.59 GB）

**5/18 paper trade**：0 筆（日盤單邊強空無壓縮無回調、breakout 設計不抓；夜盤待 22:15 區間判定）

**5/18 + 5/19 Go/No-Go 結算（5/19 21:30 結算、🚀 7/7 全過）**：
- [x] **quota < 500 MB** ✅（5/18 = 71 MB、5/19 21:30 = 42 MB、修法 D 真兇修對）
- [x] **13:45 dead zone 無 TG spam** ✅（5/18 + 5/19 兩次驗證）
- [x] **至少 1 筆 paper trade** ✅ **5/19 19:20 fire B-Pullback SHORT x3、20:02 trailing 出場、PnL +402**
- [x] **05:00 dead zone 無 spam** ✅（5/19 早 quota=0 印證）
- [x] **永豐客服 + Discord 回覆 ≥ 3 題** ✅（sj.agent + ShioajiCSBot + victoryang）
- [x] **修法 C cross-session subscribe** ✅ **5/19 19:20:38 broker reconnect 實戰、19:21:01「_last_tick_time 已重置」**
- [x] **watchdog 自癒** ✅（5/15 + 5/18 + 5/19 共 5 次實戰、平均 < 1 分鐘拉起）

**5/19 第一筆 paper trade 詳情**：
- 進場 19:20:00 [PAPER] [TMF] SELL x3、strength 0.78、B-Pullback SHORT、ema5=40128 ema20=40128 adx=31
- 持倉 42 分鐘、broker 19:20:38 reconnect 一次（修法 D-2 reset 邏輯實戰驗證）
- 出場 20:02:28 trailing stop @ 39981、**PnL +402 NTD ✅**

**5/18 新發現 5 個待辦、5/19 修完 4 個**：
- [x] **anomaly threshold 太敏感** → commit `e2e201a` + `5a1af65`（修法 1+2+3）
- [x] **TG label 分離** → commit `e2e201a` 新 `on_price_anomaly()`
- [x] **anomaly auto-restore** → commit `e2e201a` state 加 60s reset
- [x] **broker.heartbeat 30s 太敏感** → commit `a9e5d2d` + `692bb17`（修法 E、改 120s）
- [ ] cron 08:30 → 08:35（5/19 早再撞「找不到合約」、watchdog 自癒、5/20 後處理）

**🚀 5/20 切 live 前已就位 8 條修法**（PID 603692 載入確認）：

| 編號 | 修法 | commit |
|---|---|---|
| A | KbarPoller 預設停用 | `0892ac7` |
| C | reconnect cooldown fetch_contracts | `b909bea` |
| D-1 | dead-zone aware heartbeat | `1839db4` |
| D-2 | reset _last_tick_time on reconnect | `1839db4` |
| E | tick_timeout 30→120s | `a9e5d2d` + `692bb17` |
| 1 | on_price_anomaly() TG label 分離 | `e2e201a` |
| 2 | session boundary grace 600s | `e2e201a` + `5a1af65` |
| 3 | anomaly auto-restore 60s | `e2e201a` |

**5/20 早上照 `5_20_GO_LIVE_PLAYBOOK.md` 開賽**

---

**5/18（一）— 原計畫 timeline（保留作 reference）**

cron 自動觸發：
- **08:30 TST**：cron `restart_day.sh` 自動跑、PID 新起、**載入修法 D broker.py**
- **09:05 TST**：cron `log_1..py` 自動量測、推 TG（若 > 100 MB 警示）
- **13:45 TST**：日盤收盤、**修法 D 應該擋下 reconnect storm**
- **13:50 TST**：cron `daily_status_ping.sh day` 推 TG 日盤日報
- **14:00 TST**：cron `log_quota.py` 自動量測（**關鍵：應該 < 200 MB**）
- **14:55 TST**：cron `restart_night.sh` 起 ORB
- **21:30 TST**：ORB session 啟動
- **22:00 TST**：cron `log_quota.py`（< 300 MB）
- **04:30 TST 隔日**：cron `log_quota.py`（< 450 MB）
- **05:10 TST 隔日**：cron pkill ORB
- **05:15 TST 隔日**：daily_status_ping night 推 TG

user 醒來必做（5 分鐘）：
- [ ] 08:35 看 TG 「🔄 [Cron] 日盤 start.py 排程重啟」收到
- [ ] **8:35 ssh 解註解 watchdog cron**：
  ```bash
  ssh ultratrader-night 'crontab -l | sed "s|^# EMERGENCY DISABLED.*||;s|^# \(\* \* \* \* \* /home/xx/.*vps_watchdog.sh.*\)$|\1|" | crontab -'
  ssh ultratrader-night 'crontab -l | grep watchdog'   # 確認 watchdog 行不再有 # 開頭
  ```
- [ ] 09:00 看 log 有「[KbarPoller] 已停用」+「[Heartbeat] 監控啟動」
- [ ] 09:05 後 TG 看是否有警示（**無警示 = quota 正常**）

關鍵成功驗證（5/18 結束時）：
- [ ] `api.usage()` 全日 < 500 MB（修法 D 真兇修對的證明）
- [ ] [Scan] log 整個交易時段都有（策略真正在跑）
- [ ] 至少 1 筆 paper trade（日盤 OR 夜盤、TG「📥 進場」）
  - 若整日無進場：5/19 觀察、若連續 2 天無進場、審查策略條件是否過嚴
  - 這是「下單進場成功」的驗收項目

**5/20（三）切 live 前的 go / no-go 決策**（5/19 23:00 結算）
- [ ] 5/18 quota < 500 MB ✓
- [ ] 5/18 至少 1 筆 paper 進場、TG / CSV / daily JSON 三路一致 ✓
- [ ] 永豐客服回覆（quota reset 時區 / TMF 口數 / 升等機制）
- [ ] 改 `.env` `TRADING_MODE=live` + `INITIAL_BALANCE=<永豐實際權益>`

---

### 🔴 5/20 切 live 前必修待解問題清單（總覽）

**P0 阻塞切 live** ✅ 5/20 已解（disable list_positions、live process 跑著）
- [x] kbars API 是否真會在 quota reset 後恢復 — 5/20 已驗 ✅
- [x] paper 500MB/日 對雙策略不夠 → 修法 A 已驗效 ✅
- [x] 日盤 breakout 80-bar warm-up 仍依賴 kbars — 5/20 quota 恢復、80 bars warmup 成功 ✅
- [x] **5/20 live mode pybind11::error_already_set + GPF crash** — 18:25 已修（disable list_positions、見 Phase 9.7）✅

**P0 永久解（5/21+ 待做、不阻塞 live 運作）**
- [ ] **永豐 API 管理頁面確認帳務查詢權限**（解 401 source）
- [ ] 解 401 後 enable `get_real_positions()` 兩處（engine.py:463 + engine.py:1445）、改成單 thread 呼叫
- [ ] audit broker.py 所有 `self._api.*` 呼叫的 thread origin、修掉所有 multi-thread invoke
- [ ] 移除 broker `_attempt_reconnect` 內起 daemon thread 的 pattern

**P1 等永豐客服回覆**
- [ ] quota reset 時區（TST vs UTC vs 滾動 24h）
- [ ] TMF 微台口數如何換算（決定第一筆 live 下哪個合約）
- [ ] 升等是即時 / 隔日 / 月結
- [ ] paper mode 是否算成交
- [ ] 雙策略 24h 訂閱合理流量 / IP 白名單
- [ ] 帳號 / IP 是否已被當日暫停、是否需主動 unblock 申請

**P3 架構優化**
- [ ] 修法 B：取消 daily 08:30 cron restart（需先驗 risk_state 新交易日 reset 邏輯）
- [ ] 修法 C：reconnect 不重抓 fetch_contracts
- [ ] 提早 cron 啟動時間（08:30 → 02:00、避開 kbars 依賴）
- [ ] bar buffer 持久化（每根 K 棒寫 csv、startup 讀回、完全擺脫 kbars 依賴）

**P4 細節未深究**
- [ ] daily JSON 顯示 `contract=MXF` 而非 `TMF`（顯示 bug、不影響交易）
- [ ] `_kbar_poller_started` flag 在 reconnect 時的邊際 case（修法 A 已從根本避免）
- [ ] risk_state.json 跨日 reset 邏輯（修法 B 前置條件）
- [ ] `scripts/_test_kbars.py` 是否加進 `.gitignore: scripts/_*.py`

**memory 參考**
- [[shioaji-kbars-api-zero-bars]]：kbars 0 bars 根因
- [[vps-traffic-optimization-side-effects]]：3 個修法的副作用清單
- [[tg-full-event-coverage-preference]]：所有異動推 TG 偏好
- [[vps-deploy-without-restart]]：rsync 後不手動重啟偏好

## ⚠️ 5/18 21:35 發現新問題（已修復、待 5/20 前評估永久解）

- **症狀**：TG「🚨 [Sinopac-Paper] 券商連線中斷」at 21:35
- **真因**：engine.py:1512 `_check_price_anomaly` 在 ORB session 開盤誤判（TMF 52 點跳價 > 5×ATR）、呼叫 circuit_breaker.on_connection_lost、TG label 錯誤
- **已處置**：kill + restart_day.sh、PID 269001 → 294633、circuit_breaker reset
- **5/20 切 live 前評估**：
  - [ ] anomaly threshold 從 5×ATR 提高至 6-7×ATR
  - [ ] 或 ORB session start 前後 2 分鐘 grace
  - [ ] 或 anomaly 觸發 auto-restore（1 分鐘）
  - [ ] TG label 分離（價格異常 vs 連線中斷）
- memory: [[engine-price-anomaly-misnamed-connection-lost]]

---

## Phase 8 Day 6 — 5/19（二）Paper 驗證日 2 + Go/No-Go 評估

### 5/18 夜盤事後紀錄（5/19 早 09:10 zcat log 確認）
- [x] ORB session 21:35:00 啟動：`[ORB] New session: 2026-05-18-N`
- [x] 22:15:00 [ORB] Range ready: **[40965, 41381] width=4.18×ATR**（合格 3.0-5.0）
- [x] 22:15-04:00 整夜價格在區間內、**0 突破訊號、CSV 仍 header only**
- [x] 04:00 force_close 無觸發（無持倉）
- [x] 05:10 cron pkill ORB 正常停
- [x] daily JSON: `daily_pnl=0 / total_trades=0`

### 5/19 早上 timeline（事後紀錄）
- [x] **08:00 TST** quota 完全 reset（bytes=0 MB / remaining=500 MB）✅ victoryang 08:00 reset 完全確認
- [x] **08:30 cron restart_day.sh** 起 PID（初始失敗、fetch_contracts partial → 「找不到合約」、5/18 同一個 issue）
- [x] **watchdog 08:38+ 偵測 8888 not listen** 接手拉起新 PID
- [x] **09:16 PID 579124** 經手動 kill + watchdog 拉起、**載入今早 commit `5a1af65` 新版**（grace 600s + on_price_anomaly + auto-restore）
- [x] **09:16:17 init log 全綠**：Contract TMFR1 / Subscribe / KbarPoller 已停用 / Warmup 1473 bars

### 今早提交修法（5/19 09:10-09:16）
- [x] commit `e2e201a` Fix price_anomaly: 三修法（label 分離 + grace + auto-restore）
- [x] commit `5a1af65` Bump anomaly grace 120s → 600s（基於 5/18 21:35:16 實測撞點 5 分鐘 16 秒外推）

### 今夜 21:30 ORB session 驗證重點
- [ ] **21:20-21:40 grace 期**：log 不應有 `[ANOMALY] WARNING/ERROR`、TG 不應有 anomaly 推播 ← 修法 2 驗證
- [ ] 若 grace 不夠擋下：TG 推「⚠️ 價格劇烈異常」而非「🚨 連線中斷」 ← 修法 1 驗證
- [ ] 60 秒後 TG 推「✅ 價格異常已穩定、自動恢復」 ← 修法 3 驗證
- [ ] **22:15** ORB 區間建立 / 跳過判定
- [ ] **22:15-04:00** 等突破訊號
- [ ] 22:00 / 04:30 log_quota cron 量測（預期 < 100 MB）

### Go/No-Go 評估表（5/19 23:00 TST 結算、過 5 項 → 5/20 切 live）

- [x] **5/18 quota < 500 MB**（5/18 結束 ~100-130 MB）/ 5/19 上半天 14 MB ✅
- [ ] 5/18 + 5/19 至少 **1 筆完整 paper trade**：5/18 全日 0 trade、5/19 今夜待觀察
- [x] **5/18 13:45 dead zone 無 TG spam**（修法 D 真兇實戰成功）✅
- [x] **5/18 ORB session 啟動正常**（22:15 區間建立、無突破不是 bug 是市況）
- [ ] 5/19 早 05:00 dead zone 無 spam（5/18 沒撞 storm、5/19 早 quota=0 即印證）
- [x] **永豐客服 + Discord 三道回覆**：sj.agent + ShioajiCSBot + victoryang
- [x] **watchdog 自癒**：5/18 09:39 + 5/19 09:16 兩次實戰、約 1 分鐘內拉起 ✅
- [ ] 修法 C cross-session subscribe（5/18 沒 reconnect 機會驗、5/19 同樣）
- [x] **5/19 早 quota reset 確認 08:00 TST**（bytes=0、跟 victoryang 完全吻合）✅

**目前過 6/9（5/19 09:20 結算）**、主要待證：
1. 今夜 ORB 是否進場（連續 3 日無突破、5/19 第 4 日）
2. 今夜 anomaly 修法 1+2+3 是否生效
3. 修法 C cross-session subscribe（要 broker 真斷一次才能驗、5 天都沒撞）

---

## Phase 9 — 5/20（三）切實單（你做、Claude 旁觀準備）

> **⚠️ 重要 reframe（victoryang 2026-05-16 凌晨 Discord 確認）**
>
> **新帳號 1 筆實單不會直接升 2GB**：
> > 「升等到 2GB 通常代表過去 30 天有顯著交易量。新申請帳號第一筆實單從 500MB → 2GB 跳得不會這麼快、可能要累積一週交易量才到 2GB 門檻。」
>
> 對我們的影響：
> - **第一筆下單 TMF / MXF 皆可**（既然 1 口都不會立即升等、便宜的 TMF 反而合適）
> - **5/20-5/26 整週仍在 500MB 限制**裡、**修法 D 必須每天都生效**
> - 累積一週交易量後、5/27+ 才看 limit_bytes 是否跳 2GB
> - 實證方法：每筆實單後印 `api.usage().limit_bytes`、隔天 08:00 後比較
>
> **daily reset 確定 = 台灣時間 08:00**（不是 00:00 / 不是 UTC、victoryang 2026-04-09 確認）

### 9.0 盤前確認（08:00 TST 前）
- [ ] 跑 `_test_kbars.py` 驗 quota < 100 MB（reset 後乾淨）
- [ ] 跑 `git log -1` 確認 broker.py 是 `1839db4` 或更新（修法 D 在）
- [ ] SSH 確認 watchdog cron 仍啟用：`ssh ultratrader-night 'crontab -l | grep vps_watchdog'`
- [ ] 確認本地 `永豐-自動化交易` paper 已停（如還沒）：[[paper-ea-actual-location]]
- [ ] **永豐 App 確認可用 + 期貨帳戶有足夠保證金**（建議至少 1 口微台保證金 × 5 = 150K NTD）
- [ ] 手機在身邊、SSH tunnel 可用：`ssh -L 8889:localhost:8889 ultratrader-night &`

### 9.1 改設定（07:50-08:25 TST 之間、cron 觸發前）
- [ ] SSH 到 VPS 改 `.env`：
  ```bash
  ssh ultratrader-night
  cd ~/ultra-trader-src
  cp .env .env.backup-paper   # 留 paper 版備份
  sed -i 's/^TRADING_MODE=paper/TRADING_MODE=live/' .env
  # 順便把 INITIAL_BALANCE 改成實際權益（不是 paper 的 222890）
  # 用 nano 或 vim 改 INITIAL_BALANCE=<查永豐戶頭餘額>
  nano .env
  ```
- [ ] 驗證 .env 改對：
  ```bash
  grep -E "TRADING_MODE|INITIAL_BALANCE" .env
  # 應該看到 TRADING_MODE=live 跟 INITIAL_BALANCE=<你的數字>
  ```

### 9.2 cron 自然觸發 / 手動觸發（擇一）
- 等 cron 08:30 自動跑 `restart_day.sh`、PID 新起、載入 `TRADING_MODE=live`
- 或手動：`bash ~/ultra-trader-src/scripts/restart_day.sh`

### 9.3 開盤前 5 分鐘驗證（08:30-08:45）
- [ ] TG 收到「🔄 [Cron] 日盤 start.py 排程重啟 PID=...」
- [ ] log 第一行有「[Mode] **live** (real orders enabled)」←（注意不是 paper）
- [ ] log 內 KbarPoller 已停用 / Reconnect cooldown / Heartbeat 監控啟動 都看到
- [ ] `curl -s http://localhost:8888/api/state | jq '.trading_mode'` 顯示 `"live"`
- [ ] log 有 `[Warmup] N bars synthesized` + ema200 > 0
- [ ] `cat data/risk_state.json` 看 peak_equity 對應你 INITIAL_BALANCE

### 9.4 第一筆 live 訊號處置（最重要）
- [ ] 守在電腦前到 09:30、看 [Scan] 確認策略持續 evaluate
- [ ] 當 TG 推「[LIVE] 進場」：
  - [ ] **立刻**開永豐 App → 期貨成交回報 → 確認真的下單 + 成交價 + 停損價
  - [ ] 對應 log `notify_entry("live"...)` 進場價、跟 TG 一致、跟 App 一致
  - [ ] **任一不一致立刻緊急退場**（見 9.6）
- [ ] 第一筆出場（停損 / 停利 / 出場條件）：
  - [ ] 永豐 App 確認平倉成功
  - [ ] TG 推「[LIVE] 出場 ✅/❌ PnL=...」
  - [ ] 三方一致：log / TG / App
- [ ] 第一筆完成後**手動關閉 start.py 5 分鐘**：
  ```bash
  ssh ultratrader-night 'pkill -f start.py'
  # 看 5 分鐘 broker 是否有殘留問題
  # 確認沒事再啟動：ssh ultratrader-night 'bash ~/ultra-trader-src/scripts/restart_day.sh'
  ```

### 9.5 全日監看（5/20 8:30 - 5/21 8:30）
- [ ] 每 1-2 小時看 TG 一次（quota / 心跳 / 進出場）
- [ ] log_quota cron 4 次點都驗證 < 500 MB（特別注意 14:00 / 22:00 / 04:30）
- [ ] 14:55 night ORB 啟動、ORB session 21:30 看是否進場
- [ ] 04:00 force_close 後檢查 risk_state.json
- [ ] 05:15 看 daily_status_ping night TG（夜盤日報）

### 9.6 緊急退場 SOP（任何時刻可用）

```bash
# A. 切回 paper（不平倉、新單轉 paper）
ssh ultratrader-night "sed -i 's/TRADING_MODE=live/TRADING_MODE=paper/' ~/ultra-trader-src/.env"
ssh ultratrader-night "bash ~/ultra-trader-src/scripts/restart_day.sh"

# B. 全部平倉（緊急）
ssh -L 8889:localhost:8889 ultratrader-night &
curl -X POST http://localhost:8889/api/close_all

# C. 停止整個 EA（包含夜盤、最徹底）
ssh ultratrader-night "pkill -f 'start.py'; pkill -f 'paper_night_orb.py'"
ssh ultratrader-night "crontab -l | sed 's/^\(30 0 .* restart_day\)/# \1/;s/^\(55 6 .* restart_night\)/# \1/' | crontab -"

# D. 永豐 App 手動平倉（最終保險、24h 都可用）
#    用手機 App 直接平掉所有期貨持倉、不依賴 VPS / 程式
```

---

### 🔥 9.7 事後實際結果 (2026-05-20 晚補)

按 9.1 改 `.env` 後 08:33 起 live process → **每 60-90 秒就 die**（process 起跑 14 個循環、quota spam 70→144 MB）

**死前 stderr / journal**：
- `terminate called after throwing an instance of 'pybind11::error_already_set'` × 3 隨機 type
  - `_engine_loop() takes 1 positional argument but 5 were given`
  - `'dict' object is not callable`
  - `'tuple' object is not callable`
- kernel `traps: python3.12 general protection fault ip:0x580ec2`（固定地址、segfault at 0xa / 0x1）

**下午 root cause hunt（17:00-18:25）**：
1. **min-verify (noop callback)** 跑 5 分鐘穩 → 排除 SDK 本身
2. **12-round setter bisect**（累加註冊 13 個 callback setter、每 round 120s）→ 全 SURVIVED、quota 漲 0.2 MB
3. **真兇定位**：`broker.py:802 get_real_positions` → `api.list_positions(account)` 撞 **401 "Token doesn't have permission"**
   - 永豐 API key **沒簽帳務查詢權限**
   - shioaji 1.3.3 SDK 內部 401 handler 起 thread disconnect session
   - main thread / SDK callback thread 用舊 session pointer = use-after-free GPF
   - 第一次撞：engine.py:465 init 階段（main thread）
   - 第二次撞：engine.py:1445 `_heartbeat` 第 60 秒呼叫 `_reconcile_positions`（在 `_engine_loop` daemon thread）→ 對應 60-90s die timing
4. **paper 模式為何不死**：`if trading_mode == "live"` 跳過 list_positions
5. **rshioaji 1.5.13 不存在**：sinotrade.github.io/release/ 確認 1.3.3 是 latest、Discord expert 第 1 次「升 1.5.13」是 hallucination

**修法（18:25 deploy）**：
```python
# core/engine.py:463
if False and self.trading_mode == "live" and hasattr(self.broker, 'get_real_positions'):
    real_positions = self.broker.get_real_positions()

# core/engine.py:1445
if False and self._heartbeat_count % 60 == 0 and self.trading_mode == "live":
    self._reconcile_positions()
```

**驗證（18:26 切 live）**：
- ✅ PID 635823 跑 10+ 分鐘穩定（之前最長 90 秒）
- ✅ 10 個 heartbeat 連續正常、TMF 40600 → 40616
- ✅ Quota 漲幅 0.2 MB / 10 分鐘（正常）
- ✅ 無新 terminate / GPF 訊息
- ✅ Discord expert 第 4 次回覆預測完全對上（list_positions 401 → SDK disconnect race → main thread dangling pointer GPF）

**P0 待解（永久解的 prerequisite）**：
- [ ] **永豐 API 管理頁面確認 API key 帳務查詢權限**（解 401 source）
- [ ] 解 401 後 enable list_positions、但要改成單 thread 呼叫
- [ ] audit `broker.py` 內所有 `self._api.*` 呼叫的 thread origin（place_order / get_account_info / margin / kbars 等）、確保不從 callback thread 或多 thread 同時 invoke
- [ ] 移除 broker 內 `_attempt_reconnect` 起 daemon thread 的 pattern（同樣 multi-thread api.* 風險）

**短期影響**：
- 不能自動 reconcile 持倉、若人工在 App 開倉 / 平倉、engine state 不會跟著更新
- 暫時靠人工監看永豐 App 持倉
- 真實單成交回報走 `_order_cb`（SDK 主動推、不需要 list_positions）、不受影響

完整 incident 記錄見 memory：[[shioaji-1-3-3-live-callback-race]]

---

## Phase 10 — 5/20 首單實單成交驗收（你做）

### 10.1 進場驗收
- [ ] 永豐 App 期貨成交回報顯示 1 筆新單、合約 = TMFR1（或 TMFC6 之類具體月份）
- [ ] 成交價跟 TG「[LIVE] 進場」訊息一致（容差 1-2 tick 正常）
- [ ] 永豐 App 顯示有持倉、保證金被 hold 起來
- [ ] log 內 `notify_entry("live", ...)` 對應該筆

### 10.2 持倉期間（每 15 分鐘看一次）
- [ ] 永豐 App 顯示未實現 PnL、跟 VPS api/state 的 unrealized_pnl 對得起來
- [ ] log 內 `engine:_heartbeat` 顯示 position.side != flat

### 10.3 出場驗收
- [ ] TG「[LIVE] 出場 ✅ / ❌」訊息
- [ ] 永豐 App 平倉成交回報、價格跟 TG 一致
- [ ] 永豐 App 已實現損益 = TG 訊息內的 PnL（容差幾元手續費）
- [ ] log 內 `notify_exit("live", ...)` 對應該筆
- [ ] `data/performance/daily/2026-05-19.json` 內 trades 陣列多 1 筆、`trading_mode=live`

### 10.4 失敗情境的紀錄
- 若三方不一致（log / TG / App）：立刻緊急退場（9.6）+ 截圖保存 + 不再啟用直到查清楚

---

## Phase 11 — 5/21-5/26 Live 首週監控（你做、Claude 協助分析）

每日 checklist：
- [ ] 08:35 看 TG 「🔄 [Cron] 日盤」
- [ ] 08:31 看 log 載入「TRADING_MODE=live」+ 修法 D 在
- [ ] 09:00 / 14:00 / 22:00 / 04:30 log_quota cron 量測 < 500 MB
- [ ] 13:50 日盤日報、看 trades 數 + PnL
- [ ] 05:15 夜盤日報

週六 5/23 09:00：
- [ ] Hermes 自動跑「Live 首週覆盤」、TG 收到

關鍵指標（5 個交易日累計）：
- 總交易筆數 ≥ 5 筆（兩條策略都該有 fire 機會）
- 三方一致率 100%（log / TG / App、容差內）
- 0 次緊急退場
- 0 次 quota 爆量
- 0 次永豐 API 異常
- PnL 跟手動算對得起來

---

## Phase 12 — 5/27+ 持續運維（穩定後）

### 每日（自動 + 你看 TG）
- cron 自動跑日夜盤、TG 自動推進出場 / 日報
- 你看 TG 就好、無事不主動 SSH

### 每週六 09:00（自動）
- Hermes 跑週度覆盤、TG 推送
- 你看完覆盤、決定下週是否要 tune 參數

### 每月（你做）
- 對帳：永豐月對帳單 vs 系統 `data/performance/daily/*.json` 累積
- 結算日（每月第三個週三）：手動 ssh + `restart_day.sh` 一次（確保合約 reference 更新）

### 例外時可能需要的事
- 升級 API 等級（連續超量、聯絡永豐）
- 重置 risk_state（peak_equity 異常時）
- 切回 paper（觀察新策略 / 修 bug）
- 短期停機（颱風 / 法說會 / 結算日波動大）

---

## ⚠️ 補：舊版 Day 5 計畫（已整合進 Phase 9-12、保留作 reference）

- [ ] **盤前再 review 3 天 paper 結果**：
  - [ ] 無連續心跳異常
  - [ ] PnL 計算正確（跟桌機 paper 一致）
  - [ ] risk_state 沒殘留 peak_equity bug（[[paper-trading-peak-equity-bug]]）
- [ ] SSH 到 VPS 改 `.env`：
  - [ ] `TRADING_MODE=live`
  - [ ] `INITIAL_BALANCE=<永豐戶頭實際權益>`（不是 paper 的 222890）
- [ ] 重啟：`bash ~/ultra-trader-src/scripts/restart_night.sh`
- [ ] `curl localhost:8889/api/state | jq '.trading_mode'` 確認 = `"live"`
- [ ] TG 收到「實單模式啟動」通知
- [ ] **第一筆進場後**：永豐 App **必檢成交回報**（最重要）
- [ ] 第一天保持手機在身邊、隨時可緊急平倉：`curl -X POST http://VPS:8889/api/close_all`（透過 SSH tunnel）

### 緊急退場開關

```bash
# 切回 paper（不平倉、新單轉 paper）
ssh VPS "sed -i 's/TRADING_MODE=live/TRADING_MODE=paper/' ~/ultra-trader-src/.env && bash ~/ultra-trader-src/scripts/restart_night.sh"

# 全部平倉（緊急）
ssh -L 8889:localhost:8889 VPS &
curl -X POST http://localhost:8889/api/close_all

# 停止整個 EA
ssh VPS "pkill -f 'api.server.*8889'"
```

### 風險清單（必看）

| 風險 | 機率 | 處置 |
|---|---|---|
| Shioaji 換 IP 第一次登入被擋（風控）| 中 | 第一次失敗別重試、聯絡券商客服解禁 |
| 憑證路徑 Linux 大小寫不同步 | 中 | 路徑大小寫嚴格一致、用 `ls -la` 對 |
| GCP UTC 時區 vs cron 寫成台北 | 高 | crontab 時間 −8、或 `TZ=Asia/Taipei` 在 crontab 開頭 |
| Python 3.12 vs ta-lib 不相容 | 低 | 先驗 `python -c "import shioaji"` 再上線 |
| 桌機 paper 跟 VPS paper 訊號錯位 | 低（同 code）| 確認 .env / strategy/*.py 完全一致 |
| 切 live 第一筆爆損 | 中 | 第一筆下完手動關掉、看 5 分鐘確認沒問題再開回 |

---

## 附錄 A — Rate-limit 觸發 / NVIDIA 政策變更 退路

> NVIDIA build.nvidia.com 目前是 forever-free + ~40 RPM。週度 ~4 calls/月**正常情況永遠不會撞限**。本附錄是當下列**非預期事件**發生時的應急切換清單。

| 觸發條件 | 備案（一條 hermes 指令切換）| 預期成本 |
|---|---|---|
| 收到 429 / rate limit 警告 | `hermes model openrouter:meta-llama/llama-3.3-70b` 走 OpenRouter | 免費額度多 |
| NVIDIA 改成付費 / 把開源 model 下架 | `hermes model groq:llama-3.3-70b-versatile` | 免費（有日限）|
| 要更穩的中文品質 | `hermes model deepseek:deepseek-v3` | ~US$1–3/月 |
| 預算寬鬆、要最佳推理 | `hermes model anthropic:claude-haiku-4-5` | ~US$3–8/月 |
| 想完全離線（NIM container 自建）| `hermes config set NVIDIA_BASE_URL http://localhost:8000/v1` | 需 GPU 機 |

**事前準備**（建議現在就做，省得真出事手忙腳亂）：
- [ ] 順便註冊 [OpenRouter](https://openrouter.ai) + [Groq](https://console.groq.com) 各拿一把 key 放 `.env`，不啟用、當熱備
- [ ] 在 Hermes 跑 `hermes config set OPENROUTER_API_KEY <key>` 與 `hermes config set GROQ_API_KEY <key>` 設好但不切
- [ ] 在記憶寫一條 `lesson: if NVIDIA NIM returns 429 or service degrades, fallback model = openrouter:llama-3.3-70b`，agent 之後遇到自動知道怎麼切

## 附錄 B — 常用指令速查

```bash
# 模組（Python 端）
python -m review.tools_for_hermes load_week  --week_ending=2026-05-15 --compact
python -m review.tools_for_hermes load_daily --date=2026-05-11 --session=day --compact

# Orchestrator（手動跑覆盤）
bash ~/vps_trader/scripts/run_weekly_review.sh                        # 預設 today
WEEK_ENDING=2026-05-15 bash ~/vps_trader/scripts/run_weekly_review.sh  # 指定週末

# Hermes Agent（互動用，不在 orchestrator 內）
hermes                          # 進 TUI
hermes model                    # 切 provider/model
hermes config show              # 看當前設定
hermes status                   # 環境健康檢查
hermes skills list              # 看載入哪些 skill

# Windows Task Scheduler（PowerShell）
Get-ScheduledTaskInfo -TaskName "TMF_Weekly_Review"        # 看下次跑、上次結果
Start-ScheduledTask -TaskName "TMF_Weekly_Review"          # 立即觸發測試
Get-Content "C:\Users\xx\Desktop\vps永豐微台指\scripts\logs\scheduled_*.log" -Tail 50

# Secret scan（push 前必跑）
bash scripts/secret_scan.sh

# 失控防禦
ls ~/.hermes/kill_switch                                   # 看是否被 trip
rm ~/.hermes/kill_switch                                   # 解除
GUARD_MAX_TOOL_CALLS=0 bash scripts/run_weekly_review.sh   # 故意觸發測試
```

## 附錄 C — 檔案位置對照

### 整體拓撲（**現況：VPS 未租、live EA 在本機**）

| 用途 | 位置 |
|---|---|
| **交易執行 + 寫資料**（LIVE）| Windows：`C:\Users\xx\Desktop\永豐-自動化交易\ultra-trader-src\` |
| **本專案（git tracked）** | Windows：`C:\Users\xx\Desktop\vps永豐微台指\` → `xxx789666/VPS--` |
| **WSL2 內視角（本專案）** | `~/vps_trader/` → symlink → `/mnt/c/.../vps永豐微台指/` |
| **WSL2 內視角（live data）** | `~/vps_trader_paper/` → symlink → `/mnt/c/.../永豐-自動化交易/` |
| **資料根（TMF_DATA_ROOT）** | `~/vps_trader_paper/ultra-trader-src/data/` —— loader 從這讀 |
| **資料層 Python 模組** | `~/vps_trader/ultra-trader-src/review/` |
| **Skill 原檔（git 版控）** | `~/vps_trader/hermes_skills/tmf-weekly-review/` |
| **Sync/orchestrator scripts** | `~/vps_trader/scripts/` |
| **Hermes runtime** | WSL2：`~/.hermes/`（skills/、cron/、memory.sqlite、gateway/）|
| **部署 skill 方式** | symlink: `~/.hermes/skills/tmf-weekly-review` → `~/vps_trader/hermes_skills/tmf-weekly-review` |
| **Task Scheduler 入口** | `C:\Users\xx\Desktop\vps永豐微台指\scripts\scheduled_trigger.ps1` |

### 資料流向（現況：無 sync、本機 live）

```
本機 EA (永豐-自動化交易/) write *_live*.json
   │
   │  (~/vps_trader_paper symlink，無延遲、無 sync)
   ▼
WSL2 orchestrator (run_weekly_review.sh)
   │
   │  python3 -m review.tools_for_hermes load_week  →  JSON
   │  bash 格式化為純文字 facts
   │  curl POST integrate.api.nvidia.com/v1/chat/completions
   ▼
NIM 回繁中報告 (~500 bytes)
   │
   │  curl POST api.telegram.org/.../sendMessage
   ▼
Telegram → 你的手機
```

---

## 附錄 D — 原版 OpenAI agent 計畫（已棄用、留作備案）

> 2026-05-12 上午原本規劃用 OpenAI SDK 自寫 agent loop，後因發現 Hermes Agent 已內建所有需要的能力，**全面 pivot**。如果未來 Hermes Agent 出現重大問題或維護中止，可回到本路線：
>
> - 自寫 `review/llm.py`（OpenAI client）
> - 自寫 `review/agent.py`（function-calling loop）
> - 自寫 cron + TG push + lessons memory
>
> 上述四件事全部會比 Hermes Agent 自帶的差，但能避免單一上游依賴。**目前不執行**。

---

*更新規則：每完成一個 `[ ]` 改成 `[x]`，同步更新「進度總覽」。重大決策變更請更新 `memory/vps_tmf_llm_review.md`。*
