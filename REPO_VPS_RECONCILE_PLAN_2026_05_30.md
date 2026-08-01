# Repo ↔ VPS 結構重整計畫 — 2026-05-30

## 背景:目前的三方分歧
| 來源 | 結構 | 內容 | 狀態 |
|---|---|---|---|
| `feat/donchian` (git) | `ultra-trader-src/` | Donchian 回測 + 報告 + fast_engine intrabar 修正 | 已 commit + 已 push |
| `chore/lock-tmf-only` (git) | `TMFtrader-src/` | 改名 + lock-tmf + notify(止盈) | 已 commit + 已 push |
| **VPS** `/home/xx/TMFtrader-src/` | `TMFtrader-src/` | live 碼 + 本 session 改動(notify 硬止盈/追蹤回落、night_orb 0.3、watchdog guard、日報格式) | **不在 git** |

額外問題:
- **本機 repo ≠ VPS 結構**:本機有 top-level `scripts/`+`ultra-trader-src/`;VPS 就是套件內容本身。兩邊 `scripts/` 檔案集合已分歧(VPS 有 daily_status_ping.sh 在 scripts/、本機 ultra-trader-src/scripts/ 沒有)。
- **mass-M**:整 repo CRLF↔LF 全檔差異(既有)。
- **未追蹤殘留**:`TMFtrader-src/`(feat/donchian 工作樹的冗餘)、`SECURITY_SCAN_2026_05_28.md`(含舊 key、永遠別 commit)、donchian data 輸出(刻意未追蹤)。

## canonical 模型(提案)
- **repo 的 `TMFtrader-src/` = VPS `/home/xx/TMFtrader-src/` 套件的 1:1 鏡像**(只碼、不含 runtime:data/logs/.env/.venv/certs/*.parquet 由 .gitignore 擋)。
- repo 根目錄保留專案 meta(docs/、Donchian 報告、deployed_strategies 冷備份、memory 參照)—— 不部署。
- **VPS 是運維真相來源**;這次一次性「VPS → repo」拉齊,之後恢復「repo → VPS rsync」的部署方向。

---

## 需要你拍板的決策(Phase 0)
- **D1 canonical 分支**:把一切收斂到單一分支。提案:從 `chore/lock-tmf-only` 重建為新 `main`(TMFtrader-src 結構)。
- **D2 行尾正規化**:加 `.gitattributes`(`* text=auto eol=lf`)+ `git add --renormalize` → 一次清掉 mass-M。建議:做。
- **D3 deployed_strategies/ 冷備份**:含舊 UltraTrader 命名 + 重複 breakout.py。保留 or 移除?(它是 5/13 前快照、冗餘)
- **D4 GitHub 分支清理**:重整後是否刪 stale `main`(16123b9)、把 feat/* 實驗分支合併或保留?

## 執行步驟(Phase 1-3,決策確定後)
**Phase 1 — 鏡像 VPS 套件 → repo `TMFtrader-src/`(一次捕捉所有 VPS live-edits)**
- 在新 worktree/分支上,rsync VPS `/home/xx/TMFtrader-src/` → repo `TMFtrader-src/`,排除 runtime(data/、*.log、.env、.venv、certs/、*.parquet、__pycache__)。
- 一次同步 notify 硬止盈/追蹤回落、night_orb 0.3、watchdog guard、日報格式 + 任何其它 drift。

**Phase 2 — 併入 Donchian 回測成果**
- feat/donchian 的新檔(backtest_*.py、報告 .md、diag_vol_mult.py)+ 改檔(fast_engine.py intrabar 修正、donchian.py、optimize/run_donchian、test_donchian)→ 對映到 `TMFtrader-src/` 路徑。
- 兩邊都改過的檔(fast_engine.py 等)逐檔比對和解。
- 決定這些 backtest/dev 檔是否也要上 VPS(或只留 repo)。

**Phase 3 — 正規化 + 掃描 + commit + push**
- 加 `.gitattributes` + `git add --renormalize`(清 mass-M)。
- Secret 全樹 + 歷史掃描(確認 `.env`/`SECURITY_SCAN`/`vps api.txt`/`*.pfx` 未入)。
- commit → push canonical 分支 → 設 default → 清 stale 分支(若 D4 同意)。

## 風險 / 回滾
- **全部已備份**:3 個分支都在 GitHub;在「新 worktree + 新分支」做,不動現有分支 → 出錯直接丟棄重整分支,零損失。
- VPS 不動(只讀 rsync 下來);live 交易不受影響。

## 估時
- Phase 0 決策:你回 4 題即可。
- Phase 1:~10 分(rsync + 檢視 diff)。
- Phase 2:~20-40 分(逐檔和解 Donchian,最費工)。
- Phase 3:~10 分。

*計畫產生:2026-05-30。執行前需 D1-D4 決策。*
