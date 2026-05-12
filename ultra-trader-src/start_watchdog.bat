@echo off
cd /d "C:\Users\xx\Desktop\永豐-自動化交易\ultra-trader-src"
echo [%date% %time%] Watchdog starting... >> data\logs\watchdog.log
start "UltraTrader-Watchdog" /MIN pythonw scripts\watchdog.py
echo Watchdog started (background).
