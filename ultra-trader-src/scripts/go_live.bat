@echo off
echo.
echo  ⚡ UltraTrader — 全自動實戰啟動
echo  ==================================
echo.
echo  [Step 1] 啟動系統（paper 模式先驗證連線）...
echo.
cd /d C:\Users\User\UltraTrader

REM 先啟動 paper 模式（背景執行）
start /B python scripts/start.py --mode paper --risk crisis --no-browser > data\logs\engine.log 2>&1

echo  等待系統初始化（15 秒）...
timeout /t 15 /nobreak > nul

echo.
echo  [Step 2] 自動驗證 + 切換 LIVE...
echo.

REM 驗證通過就自動切 live
python scripts/go_live.py

echo.
pause
