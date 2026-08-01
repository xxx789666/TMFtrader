@echo off
REM A_struct bridge (08:35): read lab queue+decisions -> next_signal.json -> scp to VPS realtick executor.
REM Runs ASCII-path bridge copy (lab repo) + full python path -> avoids cmd cp950 CJK and Task Scheduler PATH issues.
"C:\Users\xx\AppData\Local\Programs\Python\Python312\python.exe" "C:\Users\xx\Desktop\tmf-strategy-lab-main\tmf-strategy-lab-main\scripts\Astruct_bridge.py" >> "C:\Users\xx\Desktop\tmf-strategy-lab-main\tmf-strategy-lab-main\data\logs_astruct_bridge.log" 2>&1
echo done.
