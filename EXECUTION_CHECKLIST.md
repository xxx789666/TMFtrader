# 永豐微台指 — Hermes Agent 每週自動覆盤 執行清單

> 建立日期：2026-05-12 ｜ 改週度：2026-05-12 ｜ 改本機 WSL2：2026-05-12 ｜ 確認 VPS 尚未租用：2026-05-12
>
> **現況**（2026-05-12）：交易 EA 在 **本機 Windows** 上跑（VPS 尚未租用），資料寫在本機 `ultra-trader-src/data/performance/daily/`。Hermes / 覆盤 / TG 推送全部在本機 WSL2 跑，**不需要 sync**。
>
> **未來**（VPS 租用後）：交易 EA 搬到 VPS、資料寫 VPS，本機 WSL2 透過 `sync_from_vps.sh` 拉資料；本架構同時支援這兩種模式（orchestrator 偵測有沒有 `.env.sync` 自動切換）。
>
> Stack：**Hermes Agent**（Nous Research、2026-02 發佈）+ NVIDIA NIM provider
> 覆盤頻率：**每週六 09:00 (Asia/Taipei)** 一次（Windows Task Scheduler 觸發），聚合上週 Mon-Fri 的日盤+夜盤
> 專案模組：`ultra-trader-src/review/`（資料層）+ `hermes_skills/`（skill）+ `scripts/`（orchestrator + 可選 sync）

## 部署架構速覽

```
VPS (GCP e2-small 2GB)            本機 Windows (WSL2 Ubuntu)
─────────────────────             ────────────────────────────
ultra-trader-src/                 ~/vps_trader/  (symlink → /mnt/c/...)
├ strategy/ core/                 ├ ultra-trader-src/
├ data/performance/daily/   ━━┓   │   ├ review/ (loader/tools/guard)
│  *_live*.json              ┃   │   └ data/performance/daily/ ◀━━┛
├ data/risk_state*.json     ━╋━ rsync (WSL2, every Sat 08:55)
└ Shioaji + watchdog         ┃   ├ hermes_skills/tmf-weekly-review/
                             ┃   ├ scripts/sync_from_vps.sh
                             ┃   ├ scripts/run_weekly_review.sh
                             ┃   └ scripts/scheduled_trigger.ps1
                             ┃
                             ┃   ~/.hermes/  (WSL2 home)
                             ┃   ├ skills/tmf-weekly-review (→ vps_trader)
                             ┃   ├ memory.sqlite
                             ┃   └ gateway/ (Telegram bot)
                             ┃
                             ┗━ Windows Task Scheduler (Sat 09:00)
                                  └─ scheduled_trigger.ps1
                                       └─ wsl.exe → run_weekly_review.sh
                                                      ├─ sync_from_vps.sh
                                                      ├─ hermes invoke skill
                                                      └─ runaway_guard.py
```

## 進度總覽

- [x] **Phase 0** 架構釐清（含全面 pivot 到 Hermes Agent）
- [x] **Phase 1** 資料層 MVP（`loader.py` + `tools_for_hermes.py`，含 `load_daily` + `load_week`）
- [x] **Phase 2** 第一版 skill 檔（`hermes_skills/tmf-weekly-review/SKILL.md`，含 budget + kill-switch 自律規則）
- [x] **Phase 2.5** 失控防禦 —— skill 預檢 + 外部 watchdog（`review/runaway_guard.py`）
- [x] **Phase 3** 本機 WSL2 安裝 Hermes Agent + 接 NIM ✅
- [ ] **Phase 4** 部署 skill + Windows Task Scheduler 排程
- [ ] **Phase 4.5** orchestrator 內整合 runaway_guard
- [ ] **Phase 5** Telegram gateway 串接（在 WSL2 內）—— wizard 已設好，需驗證
- [ ] **Phase X**（**未來**，VPS 租用後）：設定 `.env.sync` 啟用 sync from VPS
- [ ] **Phase 6** 上線驗證（paper 4 週 → 開放低風險自動套用）

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

## Phase 3 — 本機 WSL2 安裝 Hermes Agent + 接 NIM

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

## Phase 4 — 部署 skill + Task Scheduler 排程

### 部署 skill（WSL2 內 symlink）

- [ ] `mkdir -p ~/.hermes/skills`
- [ ] symlink：
  ```bash
  ln -s ~/vps_trader/hermes_skills/tmf-weekly-review ~/.hermes/skills/tmf-weekly-review
  ls -la ~/.hermes/skills/
  ```
- [ ] 在 Hermes 內執行 `hermes skill list` 或 TUI `/skill list` 確認偵測到
- [ ] 手動觸發測試（只用便宜 model 確認流程通）：
  ```bash
  hermes --skill tmf-weekly-review --prompt "覆盤 2026-05-11 那週 TMF，依 skill 流程執行"
  ```

### 設定 .env.sync（VPS 連線資訊）

- [ ] `cp ~/vps_trader/scripts/.env.sync.example ~/vps_trader/scripts/.env.sync`
- [ ] 編輯 `.env.sync` 填入：
  - `VPS_HOST=root@<GCP VM 外部 IP>`
  - `VPS_PROJECT_DIR=/root/ultra-trader-src`
  - `SSH_KEY=$HOME/.ssh/id_ed25519`
  - `TG_BOT_TOKEN` + `TG_CHAT_ID`（從 `ultra-trader-src/.env` 抄過來）

### 手動測一次完整 orchestrator

- [ ] WSL2 內跑：
  ```bash
  bash ~/vps_trader/scripts/run_weekly_review.sh
  ```
- [ ] 觀察 log 三段都 OK：
  - [ ] sync 成功（拉到本機 N 個 daily JSON）
  - [ ] Hermes 完成覆盤、TG 推送格式正確
  - [ ] runaway_guard 通過（exit 0）

### Windows Task Scheduler 排程

- [ ] PowerShell **系統管理員**權限執行：
  ```powershell
  powershell.exe -ExecutionPolicy Bypass -File "C:\Users\xx\Desktop\vps永豐微台指\scripts\install_task_scheduler.ps1"
  ```
- [ ] 立即觸發測試（不等到週六）：
  ```powershell
  Start-ScheduledTask -TaskName "TMF_Weekly_Review"
  Start-Sleep 3
  Get-Content "C:\Users\xx\Desktop\vps永豐微台指\scripts\logs\scheduled_*.log" -Tail 50
  ```
- [ ] 確認電腦電源計畫允許「喚醒以執行排程」：
  - 控制台 → 電源選項 → 變更計畫設定 → 進階設定 → 睡眠 → 允許喚醒計時器 = **啟用**
- [ ] 確認排程：
  ```powershell
  Get-ScheduledTaskInfo -TaskName "TMF_Weekly_Review" | Select-Object NextRunTime, LastRunTime, LastTaskResult
  ```

---

## Phase 5 — Telegram gateway 串接（WSL2 內）

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
- [ ] 確認 `runaway_guard.py` 觸發時會推 TG
- [ ] 確認 SKILL 內 prompt 要求 agent 把覆盤結果用人類格式推 TG（非 raw JSON）

### 推送多管道（可選）

- [ ] 若要同時推 Discord / Slack / Signal：
  ```bash
  hermes gateway add discord
  hermes gateway add slack
  ```
- [ ] 修改 SKILL.md Procedure step 6 改成「Channels: all」

---

## Phase 4.5 — runaway_guard 整合到 orchestrator（L2 外部監控）

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

- [ ] 第 1 週：TG 收到第一份週度覆盤，人工 review 品質
  - [ ] 數字是否跟 `load_week` 回傳一致（用工具當 ground truth）
  - [ ] 有沒有對 best_day / worst_day 真的呼叫 `load_daily` 縱深
  - [ ] 有無幻覺（編造訊號類型 / 參數 / 不存在的訊號）
  - [ ] 建議是否具體（不能是「優化止損」這種空話）
  - [ ] 若 `n_trades < 5`，建議是否真的都是 observe-only
- [ ] 第 2–3 週：開始觀察跨週引用
  - [ ] 第 2 週起，記憶有沒有引用上週的觀察
  - [ ] `evidence_weeks` 欄位有沒有正確標示
  - [ ] `~/.hermes/skills/` 有沒有 Hermes 自動生出的衍生 skill
- [ ] 第 4 週：累積 4 週基礎後評估
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

# Hermes Agent
hermes                          # 進 TUI
hermes model                    # 切 provider/model
hermes config set KEY VALUE     # 設環境變數
hermes gateway setup            # 設 Telegram bot
hermes gateway start            # 啟動 gateway daemon
hermes cron list                # 看排程
hermes cron run <id>            # 立即執行某條 cron（用來測試週度流程）
```

## 附錄 C — 檔案位置對照

### 整體拓撲

| 用途 | 位置 |
|---|---|
| **交易執行** | VPS：`/root/ultra-trader-src/`（GCP e2-small）|
| **交易資料寫入** | VPS：`/root/ultra-trader-src/data/performance/daily/*_live*.json` |
| **本機開發** | Windows：`C:\Users\xx\Desktop\vps永豐微台指\` |
| **WSL2 內視角** | `~/vps_trader/` → symlink → `/mnt/c/Users/xx/Desktop/vps永豐微台指/` |
| **資料層 Python 模組** | `~/vps_trader/ultra-trader-src/review/` |
| **Skill 原檔（git 版控）** | `~/vps_trader/hermes_skills/tmf-weekly-review/` |
| **Sync/orchestrator scripts** | `~/vps_trader/scripts/` |
| **Hermes runtime** | WSL2：`~/.hermes/`（skills/、cron/、memory.sqlite、gateway/）|
| **部署 skill 方式** | symlink: `~/.hermes/skills/tmf-weekly-review` → `~/vps_trader/hermes_skills/tmf-weekly-review` |
| **Task Scheduler 入口** | `C:\Users\xx\Desktop\vps永豐微台指\scripts\scheduled_trigger.ps1` |

### 資料流向

```
VPS write *_live*.json
   │
   │  (rsync over SSH, 每週六 08:55 WSL2 拉)
   ▼
本機 WSL2 ~/vps_trader/ultra-trader-src/data/
   │
   │  (Hermes execute_code → python3 -m review.tools_for_hermes)
   ▼
JSON 統計餵 LLM (NIM hosted)
   │
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
