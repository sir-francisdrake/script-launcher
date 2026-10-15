# Name: Disk Space Report
# Description: Display available disk space
# Self-elevate to administrator if needed
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Not running as administrator. Requesting elevation (UAC)..." -ForegroundColor Yellow
    Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}

Write-Host "`n=== Disk Space Report ===" -ForegroundColor Cyan

Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3" |
    Select-Object DeviceID, VolumeName,
        @{Name = "Size(GB)";    Expression = { [math]::Round($_.Size / 1GB, 2) } },
        @{Name = "Free(GB)";    Expression = { [math]::Round($_.FreeSpace / 1GB, 2) } },
        @{Name = "Used(%)";     Expression = { if ($_.Size -gt 0) { [math]::Round((($_.Size - $_.FreeSpace) / $_.Size) * 100, 2) } else { 0 } } } |
    Format-Table -AutoSize

Write-Host ""

Read-Host "Press Enter to close this window"
