@echo off
cd /d "C:\Users\xx\Desktop\永豐-自動化交易\ultra-trader-src"

echo ========================================
echo  TMF 夜盤 ORB Server  [Port 8889]
echo  Paper Trading / tmf_3x / 200,000
echo  B2 ML Filter / Session: 21:30-04:00
echo ========================================

REM 若舊程序仍在跑（port 8889），先終止再重啟（刷新 JWT + Solace 連線）
netstat -ano | findstr ":8889" | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo [INFO] 偵測到舊伺服器，正在終止...
    for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8889" ^| findstr "LISTENING"') do (
        powershell -NoProfile -Command "Stop-Process -Id %%p -Force -ErrorAction SilentlyContinue"
    )
    timeout /t 3 /nobreak >nul
    echo [INFO] 舊伺服器已終止
)

REM 用獨立 ps1 腳本啟動：PowerShell 自行解析中文路徑，避免 cp950 編碼問題
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0start_night.ps1"

echo [OK] 夜盤 ORB Server 啟動中...
echo      Dashboard: http://localhost:8889
echo      約 30 秒後可以訪問
