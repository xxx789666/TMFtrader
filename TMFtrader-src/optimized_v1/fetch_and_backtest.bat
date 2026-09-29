@echo off
chcp 65001 > nul
echo.
echo  TMF optimized strategy v1 - one-shot fetch data + backtest
echo  ======================================
echo.

cd /d "%~dp0.."

echo  [1/2] Fetching latest 60 days of history kbars...
python scripts/fetch_historical.py --days 60 --instrument TMF
if errorlevel 1 (
    echo  [ERROR] fetch failed - check API keys in .env
    pause
    exit /b 1
)

echo.
echo  [2/2] Running backtest...
python optimized_v1/run_backtest.py

echo.
pause
