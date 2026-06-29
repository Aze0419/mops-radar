$dir = "C:\Users\USER\我的雲端硬碟\01_WORK\AI Database"
$url = "https://iromanmac-mini.tailaf6ec0.ts.net/api/upload-dashboard"
$log = "C:\Users\USER\watch_dashboard.log"
$lastFile = ""

Add-Content $log "[$(Get-Date -Format 'HH:mm:ss')] start"

while ($true) {
    try {
        # 按檔名排序：dashboard_YYYYMMDD_HHmmss.xlsx，最新的字典序最大
        $latest = Get-ChildItem -Path "$dir\dashboard_*.xlsx" -ErrorAction Stop |
                  Sort-Object Name | Select-Object -Last 1
        if ($latest -and $latest.FullName -ne $lastFile) {
            $lastFile = $latest.FullName
            Add-Content $log "[$(Get-Date -Format 'HH:mm:ss')] found: $($latest.Name)"
            Start-Sleep 3
            $result = & curl.exe -s -F "file=@`"$($latest.FullName)`"" $url
            Add-Content $log "[$(Get-Date -Format 'HH:mm:ss')] curl: $result"
        }
    } catch {
        Add-Content $log "[$(Get-Date -Format 'HH:mm:ss')] error: $_"
    }
    Start-Sleep 10
}
