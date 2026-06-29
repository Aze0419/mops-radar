$dir = "C:\Users\USER\我的雲端硬碟\01_WORK\AI Database"
$url = "https://iromanmac-mini.tailaf6ec0.ts.net/api/upload-dashboard"

$watcher = New-Object System.IO.FileSystemWatcher $dir, "dashboard_*.xlsx"
$watcher.NotifyFilter = [System.IO.NotifyFilters]::FileName
$watcher.EnableRaisingEvents = $true

Register-ObjectEvent $watcher Created -Action {
    Start-Sleep 3  # 等檔案寫完
    $file = $Event.SourceEventArgs.FullPath
    $result = curl -s -F "file=@`"$file`"" $using:url
    $ts = Get-Date -Format "HH:mm:ss"
    Write-Host "[$ts] 上傳：$(Split-Path $file -Leaf) → $result"
}

Write-Host "監聽 $dir 中，等待新 dashboard_*.xlsx..."
while ($true) { Start-Sleep 5 }
