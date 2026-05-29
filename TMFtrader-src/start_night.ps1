# start_night.ps1 - Launch night ORB server as independent process
# PowerShell resolves its own path natively (no cmd encoding issues)
$WorkDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python  = "C:\Users\xx\AppData\Local\Programs\Python\Python312\python.exe"
Start-Process -FilePath $Python `
              -ArgumentList "scripts\start_night.py" `
              -WorkingDirectory $WorkDir `
              -WindowStyle Hidden
