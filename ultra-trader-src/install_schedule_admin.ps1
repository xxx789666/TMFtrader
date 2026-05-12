# 以管理員身份執行此腳本
$TaskName = "UltraTrader-NightORB"
$XmlPath  = "C:\Users\xx\Desktop\永豐-自動化交易\ultra-trader-src\NightORB_Task.xml"

# 刪除舊任務
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue

# 從 XML 匯入
$xml = Get-Content $XmlPath -Raw -Encoding Unicode
Register-ScheduledTask -TaskName $TaskName -Xml $xml -Force

# 確認
$t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($t) {
    Write-Host "[OK] 排程建立成功: $TaskName" -ForegroundColor Green
    Write-Host "     下次執行: $(($t | Get-ScheduledTaskInfo).NextRunTime)"
} else {
    Write-Host "[ERROR] 排程建立失敗" -ForegroundColor Red
}
