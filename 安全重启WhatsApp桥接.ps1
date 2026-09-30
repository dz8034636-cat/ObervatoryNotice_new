# Windows: 放在与 app.py 同一目录；须先从 App／系统托盘正常退出。
# 仅备份并移走已无进程占用的 Chromium Singleton* 锁文件；不删除 WhatsApp 会话。
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonw = Join-Path $project '.venv\Scripts\pythonw.exe'
$app = Join-Path $project 'app.py'
$session = Join-Path $env:LOCALAPPDATA 'HKO_WhatsApp_Alert\chrome_profile\session'

if (-not (Test-Path -LiteralPath $app -PathType Leaf)) {
    throw "找不到程序入口：$app"
}
if (-not (Test-Path -LiteralPath $pythonw -PathType Leaf)) {
    throw "找不到虚拟环境 Python：$pythonw"
}

$sessionPattern = [regex]::Escape($session)
$projectPattern = [regex]::Escape($project)
$related = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -in @('python.exe','pythonw.exe','node.exe','chrome.exe','chromium.exe','chrome-headless-shell.exe') -and
    $_.CommandLine -and (
        $_.CommandLine -match $sessionPattern -or
        $_.CommandLine -match $projectPattern -or
        ($_.Name -eq 'node.exe' -and $_.CommandLine -match 'server\.js')
    )
})
if ($related.Count -gt 0) {
    $related | Select-Object ProcessId,ParentProcessId,Name,CommandLine | Format-List
    throw '检测到可能占用会话的进程；请先确认并正常退出。脚本不会强行结束进程或启动第二份 App。'
}

if (Test-Path -LiteralPath $session -PathType Container) {
    $probe = Join-Path $session ('.write-probe-' + $PID)
    try {
        [System.IO.File]::WriteAllText($probe, 'test')
    } finally {
        if (Test-Path -LiteralPath $probe) { Remove-Item -LiteralPath $probe -Force }
    }
    $names = @('SingletonLock','SingletonCookie','SingletonSocket')
    $locks = @($names | ForEach-Object { Join-Path $session $_ } | Where-Object { Test-Path -LiteralPath $_ })
    if ($locks.Count -gt 0) {
        $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
        $backup = Join-Path (Split-Path -Parent $session) ("singleton-lock-backup-$stamp")
        New-Item -ItemType Directory -Path $backup -Force | Out-Null
        foreach ($lock in $locks) {
            Move-Item -LiteralPath $lock -Destination (Join-Path $backup (Split-Path -Leaf $lock)) -ErrorAction Stop
        }
        Write-Host "已备份旧 Singleton 锁文件：$backup"
    } else {
        Write-Host '没有需要清理的 Singleton 锁文件。'
    }
}

Write-Host '未删除 session／登录资料；正在启动一份 App。'
Start-Process -FilePath $pythonw -ArgumentList ('"' + $app + '"') -WorkingDirectory $project
Write-Host '已启动。如仍显示 ERROR，请查看日志并检查 server.js；不要反复运行本脚本。'
