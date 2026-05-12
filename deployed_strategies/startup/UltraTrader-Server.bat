@echo off
cd /d "C:\Users\xx\Desktop\永豐-自動化交易\ultra-trader-src"
echo [%date% %time%] Server auto-start on boot >> data\logs\restart.log
start "UltraTrader-Server" /MIN pythonw scripts\start.py --no-browser
