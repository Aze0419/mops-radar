$dir = "C:\Users\USER\我的雲端硬碟\01_WORK\AI Database"
$latest = Get-ChildItem "$dir\dashboard_*.xlsx" | Sort-Object LastWriteTime | Select-Object -Last 1

if (-not $latest) {
    Write-Host "找不到 dashboard_*.xlsx" -ForegroundColor Red
    exit 1
}

Write-Host "上傳：$($latest.Name)"
curl -s -F "file=@`"$($latest.FullName)`"" https://iromanmac-mini.tailaf6ec0.ts.net/api/upload-dashboard
Write-Host ""
