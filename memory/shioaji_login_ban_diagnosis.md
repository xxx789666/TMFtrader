# Shioaji 登入失敗診斷教訓

## 錯誤案例（2026-04-28）

錯誤訊息：`111.243.139.191 not allow`

### 我的錯誤診斷流程
1. 看到 IP 出現在錯誤訊息 → 判斷是 IP 封鎖
2. 引導用戶 PPPoE 重撥換 IP
3. 新 IP `111.249.25.59` 立刻也失敗 → 才發現是 API Key 問題
4. 換 Key → 成功

**浪費用戶時間在不必要的換 IP 操作上。**

## 正確診斷流程

Shioaji 的 `{IP} not allow` 錯誤訊息把 IP 寫進去，容易誤導以為是 IP 問題。
實際上可能是：
1. **API Key 過期/被停用**（更常見）
2. **IP 封鎖**（需要大量重複登入才會觸發）

### 正確順序
1. **先在同一 IP 測試備用 Key**（410.txt: `<SHIOAJI_BACKUP_KEY_REDACTED>`）
2. 備用 Key 成功 → Key 問題，更新 .env，**不需換 IP**
3. 備用 Key 也失敗 → 才考慮 IP 封鎖，再做 PPPoE 重撥

## API Key 資訊
- `.env` 主 Key：`<SHIOAJI_PRIMARY_KEY_REDACTED>`（2026-04-28 已失效）
- `410.txt` 備用 Key：`<SHIOAJI_BACKUP_KEY_REDACTED>`（已更新進 .env）

> 註：實際 key 值只在本機 `.env` 與離線備份 `410.txt`，**禁止入 git history**。

## IP 封鎖真正的觸發條件
- 同一 IP 短時間內大量登入（如心跳 bug 造成的重啟風暴：2 小時 11 次重啟）
- 根本解決方法是修復重啟風暴（heartbeat bug），而不是換 IP
