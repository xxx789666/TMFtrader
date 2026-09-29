@echo off
echo.
echo  TMFtrader - full auto live start
echo  ==================================
echo.
echo  [Step 1] Start system (paper mode first, verify connection)...
echo.
cd /d C:\Users\User\TMFtrader

REM Start paper mode first (background)
start /B python scripts/start.py --mode paper --risk crisis --no-browser > data\logs\engine.log 2>&1

echo  Waiting for init (15s)...
timeout /t 15 /nobreak > nul

echo.
echo  [Step 2] Auto verify + switch to LIVE...
echo.

REM Auto switch to live once verification passes
python scripts/go_live.py

echo.
pause
