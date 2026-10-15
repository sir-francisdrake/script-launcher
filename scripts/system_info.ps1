# Name: System Information
# Description: Display basic Windows system information
# Self-elevate to administrator if needed
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Not running as administrator. Requesting elevation (UAC)..." -ForegroundColor Yellow
    Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}

Write-Host "`n=== System Information ===" -ForegroundColor Cyan

Get-ComputerInfo |
    Select-Object CsName, WindowsProductName,
        WindowsVersion, OsArchitecture,
        CsTotalPhysicalMemory |
    Format-List

Write-Host "Current User: $env:USERDOMAIN\$env:USERNAME"
Write-Host "PowerShell Version: $($PSVersionTable.PSVersion)"

Read-Host "`nPress Enter to close this window"
