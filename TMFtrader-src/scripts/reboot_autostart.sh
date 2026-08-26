#!/bin/bash
# VM 開機自啟引擎 —— 補 @reboot 缺口(2026-07-01 事故:VM 被關後引擎不自啟、要等 08:15 cron)。
# 安全性:launchers 自帶 PIDFILE 單例(已在跑會精準 kill 舊的重啟、不雙開)+ market_holidays 假日守門。
#         所以開機任何時間跑都安全:假日 no-op、與每日 cron 重疊時只會乾淨重啟。
# 2026-08-05 修:8/2 退役 breakout 的編輯誤刪「for s in \」行(breakout 是清單首項)→
#   @reboot 語法壞掉靜默失效,8/5 停權恢復開機時首次咬人。順帶補 fishing paper 進清單
#   (原只靠 13:50 cron,開機在 13:50 前會漏一段;launcher 同樣 PIDFILE 安全)。
# 2026-08-26 修:清單同步 8/15、8/17 的暫停(教訓=7/16「paper↔live 切換要同步本檔」,8/17 漏了):
#   - start_chips_exec_live / start_maxpain_exec_live 移出(8/17 起 live 暫停;開機自啟真錢引擎
#     還會繞過「live 復跑必須先注入魅影 lock_provider」前提)。恢復 live 時把行加回來。
#   - start_fishing_paper 移出(8/15 起 paper 暫停)。
#   - start_chips_exec_paper 加入(chips 恢復 paper 前推,供順風燈 forward 期真 tick 證據)。
LOGDIR=/home/xx/TMFtrader-src/data/logs
mkdir -p "$LOGDIR"
sleep 120                                   # 等網路/系統就緒(永豐 login 需穩定網路 + 時鐘同步)
cd /home/xx/TMFtrader-src || exit 1
# 2026-08-26 加:wave_exec_c(政策C真tick)、night_b(夜盤變體B真tick;開機重啟時若有過夜倉,
#   引擎持倉恢復 + entered marker 防重進,任何時間跑都安全)。
for s in start_chips_exec_paper \
         start_wave_exec_paper \
         start_wave_exec_c_paper \
         start_night_b_paper; do
  echo "[$(date '+%F %T')] @reboot autostart -> $s"
  bash "scripts/$s.sh"
  sleep 20                                  # 錯開連線登入尖峰
done
echo "[$(date '+%F %T')] @reboot autostart 完成"
# PAUSED-20260817 恢復 live 時加回 for 清單:start_chips_exec_live、start_maxpain_exec_live
# PAUSED-20260815 恢復釣魚 paper 時加回:start_fishing_paper
