# 專案規範

## 🚨 安全編輯 VPS crontab 鐵律（違反 = 整張排程表被洗空）

背景：VPS `ultratrader-night` 的 user crontab 已被清空**三次**（2026-05-28 一次性腳本自刪 race、2026-07-01 多層 SSH 轉義吃掉 `$(date)`、2026-07-04 cleanup session）。根因都一樣：`crontab -` 是**整張表替換**，任何「讀出→過濾→寫回」管線只要讀出那步失敗回空，就靜默裝入空表（exit code 仍是 0）。crontab 是 VPS 唯一排程器，表空 = watchdog / launcher / 報告 / 真錢部位管理全停。

**改 crontab 一律走這個流程，不准偷懶：**

1. **先備份到固定檔名並驗證非空**：
   ```bash
   crontab -l > /home/xx/crontab_backup_<用途>_$(date +%Y%m%d_%H%M%S).bak
   wc -l <備份檔>   # 必須 >0（正常 ~65 行）才准繼續；回 0 = 立刻停手
   ```
2. **禁用自刪/過濾管線**：永遠不寫 `crontab -l | grep -v ... | crontab -`。刪行改成：dump 到檔 → `sed` 改檔 → 檢查 → 安裝。
3. **加行用純 append**：`cat 備份檔 /tmp/additions.txt > /tmp/cron_new.txt`，不碰既有行。
4. **安裝前 `cat -n /tmp/cron_new.txt` 給 user 審**，確認行數合理（~65 行）再裝。
5. **安裝用 `crontab /tmp/cron_new.txt`（檔案），永遠不用 pipe（`| crontab -`）**。
6. **裝完立刻驗證**：`crontab -l | wc -l` 行數要和預期一致。
7. **經 SSH 改 crontab 特別危險**：指令穿過 PowerShell→WSL→gcloud ssh→遠端 bash 多層引號，`$(...)`、glob、引號極易被轉義吃掉。複雜操作先把腳本 scp 上去再在 VPS 端執行，不要內嵌在 `--command=` 裡。
8. 備援哨兵：systemd `crontab-sentinel.timer` 每小時檢查行數 <10 就推 TG。收到告警 = 用最新 `/home/xx/crontab_backup_*.bak` 還原。

## VPS 基本事實

- VPS = GCP VM `ultratrader-night`（zone asia-east1-b），存取只能經 WSL：`wsl bash -c "gcloud compute ssh ultratrader-night --zone=asia-east1-b --command='...'"`
- VPS 非 git repo，部署走 rsync；crontab 不在版本控制裡，唯一真相 = VPS 上的表本身 + 時間戳備份檔
- 系統時鐘/cron 是 UTC，應用 log 是台灣時間（UTC+8），grep 前先換算
