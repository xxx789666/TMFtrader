@echo off
cd /d "C:\Users\xx\Desktop\永豐-自動化交易\TMFtrader-src"

echo ========================================
echo  TMF 夜盤 ORB Paper Trading
echo  B2 ML Filter ^| threshold=0.40
echo  21:30-04:00 TST ^| max 3 contracts
echo ========================================
echo.

REM 確認主伺服器在跑（只確認，夜盤腳本獨立運行）
curl -s http://localhost:8888/api/state >nul 2>&1
if %errorlevel%==0 (
    echo [OK] 主伺服器運行中
) else (
    echo [WARN] 主伺服器未回應（夜盤可獨立運行）
)
echo.

REM 啟動夜盤 ORB（前景執行，方便監控 log）
echo [Start] 啟動夜盤 ORB... 按 Ctrl+C 停止
echo.
python scripts\paper_night_orb.py --threshold 0.40

echo.
echo [Done] 夜盤 ORB 已停止
pause
