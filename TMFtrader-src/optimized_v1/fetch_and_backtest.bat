@echo off
chcp 65001 > nul
echo.
echo  TMF 優化策略 v1 — 一鍵抓資料 + 回測
echo  ======================================
echo.

cd /d "%~dp0.."

echo  [1/2] 抓取最新 60 天歷史 K 棒...
python scripts/fetch_historical.py --days 60 --instrument TMF
if errorlevel 1 (
    echo  [錯誤] 資料抓取失敗，請確認 .env 中的 API 金鑰設定
    pause
    exit /b 1
)

echo.
echo  [2/2] 執行回測...
python optimized_v1/run_backtest.py

echo.
pause
