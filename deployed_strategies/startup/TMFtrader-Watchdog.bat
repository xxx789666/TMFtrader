@echo off
cd /d "C:\Users\xx\Desktop\永豐-自動化交易\TMFtrader-src"
echo [%date% %time%] Watchdog starting... >> data\logs\watchdog.log
start "TMFtrader-Watchdog" /MIN pythonw scripts\watchdog.py
echo Watchdog started (background).
