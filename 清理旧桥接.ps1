<#
清理旧桥接.ps1
用途：清理遗留的 WhatsApp Bridge（node.exe server.js）及其 Chromium 窗口。
默认是"预览模式"，只显示将被处理的内容，不结束任何进程。
加上 -Apply 才会真正执行。
不会删除 WhatsApp 登录会话（chrome_profile\session）。

用法：
  powershell -NoProfile -ExecutionPolicy Bypass -File .\清理旧桥接.ps1
  powershell -NoProfile -ExecutionPolicy Bypass -File .\清理旧桥接.ps1 -Apply
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

if ($Apply) { $mode = '执行模式' } else { $mode = '预览模式（不会结束任何进程）' }
Write-Host "=== 天气警告 Bridge 清理：$mode ===" -ForegroundColor Cyan

# 0. App 是否仍在运行
$apps = @(Get-CimInstance Win32_Process |
    Where-Object { $_.Name -match '^pythonw?\.exe$' -and $_.CommandLine -match 'app\.py' })
if ($apps.Count -gt 0) {
    Write-Host "`n检测到可能仍在运行的 App：" -ForegroundColor Yellow
    $apps | Select-Object ProcessId, CommandLine | Format-List
    if ($Apply) {
        Write-Host '请先从 App 或系统托盘选择退出程序，再重新运行。已停止。' -ForegroundColor Red
        exit 1
    }
}

Write-Host "`n[1] 监听中的 Bridge 端口（$portFirst-$portLast）"
$listen = @(Get-BridgeListeners)
if ($listen.Count -eq 0) { Write-Host "  无" }
else { $listen | Select-Object LocalPort, OwningProcess | Format-Table -AutoSize }

Write-Host "[2] Bridge 的 Node 进程"
$nodes = @(Get-BridgeNodes)
if ($nodes.Count -eq 0) { Write-Host "  无" }
else { $nodes | Select-Object ProcessId, ParentProcessId | Format-Table -AutoSize }

Write-Host "[3] 占用登录会话的 Chrome"
$chrome = @(Get-SessionChrome)
Write-Host "  $($chrome.Count) 个进程"

if (-not $Apply) {
    Write-Host "`n以上仅为预览。确认这些都是天气警告工具的 Bridge 后，加上 -Apply 再运行。" -ForegroundColor Yellow
    exit 0
}

# 1/3 优雅关闭（等同 App 退出时做的事）
Write-Host "`n[执行 1/3] 请求优雅关闭"
foreach ($item in $listen) {
    try {
        Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$($item.LocalPort)/shutdown" -TimeoutSec 5 | Out-Null
        Write-Host "  已请求关闭端口 $($item.LocalPort)"
    } catch {
        Write-Host "  端口 $($item.LocalPort) 未响应：$($_.Exception.Message)"
    }
}
Start-Sleep -Seconds 8

# 2/3 仍残留的 Node 进程（/T 会连同其 Chromium 子进程一起结束）
Write-Host "[执行 2/3] 结束仍残留的 Bridge 进程树"
foreach ($node in @(Get-BridgeNodes)) {
    Write-Host "  结束 Node PID $($node.ProcessId)"
    taskkill /PID $node.ProcessId /T /F 2>$null | Out-Null
}
Start-Sleep -Seconds 2

# 3/3 仍占用会话的 Chrome
Write-Host "[执行 3/3] 结束仍占用会话的 Chrome"
foreach ($c in @(Get-SessionChrome)) {
    taskkill /PID $c.ProcessId /T /F 2>$null | Out-Null
}
Start-Sleep -Seconds 2

Write-Host "`n=== 结果 ===" -ForegroundColor Cyan
$leftPorts  = @(Get-BridgeListeners)
$leftChrome = @(Get-SessionChrome)
if ($leftPorts.Count -eq 0 -and $leftChrome.Count -eq 0) {
    Write-Host "已清理干净：没有 Bridge 端口，也没有 Chrome 占用会话。登录会话未被删除。" -ForegroundColor Green
    Write-Host "现在可以只启动一份 App。"
} else {
    Write-Host "仍有残留，请再运行一次，或手动检查：" -ForegroundColor Yellow
    if ($leftPorts.Count  -gt 0) { $leftPorts  | Select-Object LocalPort, OwningProcess | Format-Table -AutoSize }
    if ($leftChrome.Count -gt 0) { Write-Host "  仍有 $($leftChrome.Count) 个 Chrome 进程占用会话目录" }
}