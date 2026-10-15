# Name: Network Connectivity Check
# Description: Test DNS and TCP connectivity
# Self-elevate to administrator if needed
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "Not running as administrator. Requesting elevation (UAC)..." -ForegroundColor Yellow
    Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}

Write-Host "`n=== Network Connectivity Check ===" -ForegroundColor Cyan

$targets = @("www.google.com", "github.com", "1.1.1.1")
foreach ($target in $targets) {
    Write-Host "Resolving $target ... " -NoNewline
    try {
        $resolved = [System.Net.Dns]::GetHostAddresses($target) | Select-Object -First 1
        Write-Host "OK ($($resolved.IPAddressToString))" -ForegroundColor Green
    } catch {
        Write-Host "FAILED" -ForegroundColor Red
    }
}

Write-Host ""
$portTests = @(
    @{ Host = "www.google.com"; Port = 443 },
    @{ Host = "github.com";       Port = 443 }
)
foreach ($test in $portTests) {
    Write-Host "Testing TCP $($test.Host):$($test.Port) ... " -NoNewline
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect($test.Host, $test.Port, $null, $null)
        $ok = $async.AsyncWaitHandle.WaitOne(3000)
        if ($ok -and $client.Connected) {
            Write-Host "OPEN" -ForegroundColor Green
        } else {
            Write-Host "CLOSED/TIMEOUT" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
    } finally {
        $client.Close()
    }
}

Write-Host ""
Write-Host "Local IPv4 addresses:"
Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike "127.*" } |
    Select-Object InterfaceAlias, IPAddress |
    Format-Table -AutoSize

Read-Host "`nPress Enter to close this window"
