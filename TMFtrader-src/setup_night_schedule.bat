@echo off
REM ================================================================
REM  TMFtrader 夜盤 ORB — 工作排程器設定
REM  每週一至週五 20:55 自動啟動夜盤 Server（port 8889）
REM ================================================================
cd /d "C:\Users\xx\Desktop\永豐-自動化交易\TMFtrader-src"

set TASK_NAME=TMFtrader-NightORB
set BAT_PATH=C:\Users\xx\Desktop\永豐-自動化交易\TMFtrader-src\start_night_server.bat

echo [設定] 建立夜盤 ORB 工作排程...
echo.
echo  任務名稱: %TASK_NAME%
echo  執行時間: 每週一~五 20:55
echo  執行檔案: %BAT_PATH%
echo.

REM 刪除舊任務（若存在）
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

REM 建立新排程（每週一至週五 20:55）
schtasks /create ^
  /tn "%TASK_NAME%" ^
  /tr "cmd /c \"%BAT_PATH%\"" ^
  /sc WEEKLY ^
  /d MON,TUE,WED,THU,FRI ^
  /st 20:55 ^
  /ru "%USERNAME%" ^
  /rl HIGHEST ^
  /f

if %errorlevel%==0 (
    echo.
    echo [OK] 排程建立成功！
    echo      每週一至週五 20:55 自動啟動夜盤 Server
    echo.
    echo [提示] 手動管理指令:
    echo   查看排程: schtasks /query /tn "%TASK_NAME%"
    echo   立即執行: schtasks /run /tn "%TASK_NAME%"
    echo   刪除排程: schtasks /delete /tn "%TASK_NAME%" /f
) else (
    echo.
    echo [ERROR] 排程建立失敗，請用系統管理員身份執行此 bat
)

pause
