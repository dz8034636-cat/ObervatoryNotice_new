<#
Clean-OldBridge.ps1
Purpose : Clean up leftover WhatsApp Bridge processes (node.exe server.js)
          and the Chromium windows that hold the WhatsApp login session.
Default : PREVIEW mode. Nothing is stopped.
          Add -Apply to actually clean up.
Safe    : Does NOT delete the login session (chrome_profile\session).

Usage:
  powershell -NoProfile -ExecutionPolicy Bypass -File .\Clean-OldBridge.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File .\Clean-OldBridge.ps1 -Apply
#>
param([switch]$Apply)

$portFirst = 8765
$portLast  = 8784
$session   = Join-Path $env:LOCALAPPDATA 'HKO_WhatsApp_Alert\chrome_profile\session'
$sessionRx = [regex]::Escape($session)

function Get-BridgeListeners {
    Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalPort -ge $portFirst -and $_.LocalPort -le $portLast }
}

function Get-SessionChrome {
    Get-CimInstance Win32_Process |
        Where-Object { $_.Name -eq 'chrome.exe' -and $_.CommandLine -match $sessionRx }
}

function Get-BridgeNodes {
    $listenPids    = @(Get-BridgeListeners | Select-Object -ExpandProperty OwningProcess -Unique)
    $chromeParents = @(Get-SessionChrome   | Select-Object -ExpandProperty ParentProcessId -Unique)
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -eq 'node.exe' -and $_.CommandLine -match 'server\.js' -and
            ($listenPids -contains $_.ProcessId -or $chromeParents -contains $_.ProcessId)
        }
}

if ($Apply) { $mode = 'APPLY mode' } else { $mode = 'PREVIEW mode (nothing will be stopped)' }
Write-Host "=== Bridge cleanup: $mode ===" -ForegroundColor Cyan

# 0. Is the app still running?
$apps = @(Get-CimInstance Win32_Process |
    Where-Object { $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -match 'app\.py' })
if ($apps.Count -gt 0) {
    Write-Host ''
    Write-Host 'The app may still be running:' -ForegroundColor Yellow
    $apps | Select-Object ProcessId, CommandLine | Format-List
    if ($Apply) {
        Write-Host 'Quit the app first (window or tray icon -> Exit), then run again. Stopped.' -ForegroundColor Red
        exit 1
    }
}

Write-Host ''
Write-Host "[1] Listening bridge ports (${portFirst}-${portLast})"
$listen = @(Get-BridgeListeners)
if ($listen.Count -eq 0) { Write-Host '  none' }
else { $listen | Select-Object LocalPort, OwningProcess | Format-Table -AutoSize }

Write-Host '[2] Bridge node processes'
$nodes = @(Get-BridgeNodes)
if ($nodes.Count -eq 0) { Write-Host '  none' }
else { $nodes | Select-Object ProcessId, ParentProcessId | Format-Table -AutoSize }

Write-Host '[3] Chrome processes holding the login session'
$chrome = @(Get-SessionChrome)
Write-Host "  $($chrome.Count) process(es)"

if (-not $Apply) {
    Write-Host ''
    Write-Host 'Preview only. If everything listed is your bridge, run again with -Apply.' -ForegroundColor Yellow
    exit 0
}

# Step 1/3: graceful shutdown (same thing the app does on exit)
Write-Host ''
Write-Host '[Step 1/3] Asking bridges to shut down gracefully'
foreach ($item in $listen) {
    try {
        Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$($item.LocalPort)/shutdown" -TimeoutSec 5 | Out-Null
        Write-Host "  shutdown requested on port $($item.LocalPort)"
    } catch {
        Write-Host "  port $($item.LocalPort) did not respond: $($_.Exception.Message)"
    }
}
Start-Sleep -Seconds 8

# Step 2/3: leftover node processes (/T also ends their Chromium children)
Write-Host '[Step 2/3] Stopping leftover bridge process trees'
foreach ($node in @(Get-BridgeNodes)) {
    Write-Host "  stopping node PID $($node.ProcessId)"
    taskkill /PID $node.ProcessId /T /F 2>$null | Out-Null
}
Start-Sleep -Seconds 2

# Step 3/3: chrome still holding the session
Write-Host '[Step 3/3] Stopping Chrome still holding the session'
foreach ($c in @(Get-SessionChrome)) {
    taskkill /PID $c.ProcessId /T /F 2>$null | Out-Null
}
Start-Sleep -Seconds 2

Write-Host ''
Write-Host '=== Result ===' -ForegroundColor Cyan
$leftPorts  = @(Get-BridgeListeners)
$leftChrome = @(Get-SessionChrome)
if ($leftPorts.Count -eq 0 -and $leftChrome.Count -eq 0) {
    Write-Host 'Clean: no bridge ports and no Chrome holding the session. Login session was NOT deleted.' -ForegroundColor Green
    Write-Host 'You can now start the app (only one copy).'
} else {
    Write-Host 'Something is left. Run again, or check manually:' -ForegroundColor Yellow
    if ($leftPorts.Count -gt 0) { $leftPorts | Select-Object LocalPort, OwningProcess | Format-Table -AutoSize }
    if ($leftChrome.Count -gt 0) { Write-Host "  $($leftChrome.Count) Chrome process(es) still hold the session folder" }
}
