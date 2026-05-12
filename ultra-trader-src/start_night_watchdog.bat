@echo off
cd /d "C:\Users\xx\Desktop\永豐-自動化交易\ultra-trader-src"
echo [%date% %time%] Night Watchdog starting... >> data\logs\watchdog_night.log
start "UltraTrader-NightWatchdog" /MIN pythonw scripts\watchdog.py --night
echo Night Watchdog started (background).
