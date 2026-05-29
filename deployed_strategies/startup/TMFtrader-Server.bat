@echo off
cd /d "C:\Users\xx\Desktop\永豐-自動化交易\TMFtrader-src"
echo [%date% %time%] Server auto-start on boot >> data\logs\restart.log
start "TMFtrader-Server" /MIN pythonw scripts\start.py --no-browser
