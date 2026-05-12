@echo off
cd /d "C:\Users\xx\Desktop\永豐-自動化交易\ultra-trader-src"
taskkill /F /FI "WINDOWTITLE eq python*" >/dev/null 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8888"') do taskkill /F /PID %%a >/dev/null 2>&1
timeout /t 3 /nobreak >/dev/null
start /B pythonw scripts/start.py --no-browser
echo [%date% %time%] Trader restarted >> data/logs/restart.log
