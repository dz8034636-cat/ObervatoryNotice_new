# 天气警告 WhatsApp 通知工具 README

香港天文台（HKO）／劳工处（HSWW）天气警告 → 按群组订阅、工作时间与 24/7 规则 → 通过 WhatsApp Bridge 发送文字与图片。Windows 优先，界面为 PyQt5。

> 依据：`app.py`、`core_new.py`、`engine_new.py`、`hko_data.py`、`whatsapp_service.py`、`whatsapp_bridge_client.py`、`server.js`，以及本对话中确认的规则。
> 部分上传文件有截断，函数清单以 9.3 的静态命令在本机重新生成为准。

---

## 1. 重要：WhatsApp Bridge 是改动过的版本

**`whatsapp_bridge/server.js` 已被修改，普通的 whatsapp-web.js 示例 `.js` 不能替代它。** Python 端依赖它的接口和环境变量：

| 项目 | 约定 |
|---|---|
| 启动 | `node server.js`（工作目录 `whatsapp_bridge/`） |
| 环境变量 | `PORT`、`SESSION_PATH`、`HEADLESS` |
| 会话 | `LocalAuth({ dataPath: SESSION_PATH })`，实际资料在 `SESSION_PATH\session\` |
| 监听 | 仅 `127.0.0.1` |
| 接口 | `/status`、`/qr`、`/list_groups`、`/verify_group`、`/send`、`/shutdown` |
| `/status` 字段 | `status`、`detail`、`has_qr`、`session_path`、`headless` 等 |
| `status` 取值 | `STARTING`、`QR_PENDING`、`AUTHENTICATED`、`CONNECTED`、`READY`、`AUTH_FAILURE`、`DISCONNECTED`、`ERROR` |
| `/send` 回传 | `SENT`＝文字成功（带图片时图片也成功）；`SENT_TEXT_ONLY`＝文字已发、图片失败；`FAILED`＝失败 |
| `/shutdown` | 回复 `{ok:true}` → `client.destroy()` → `process.exit(0)`，Chromium 与 Node 一并关闭 |

`server.js` 的改动要点：`/list_groups`、`/verify_group`、`/send` 绕开 `client.getChats()`，直接读 `WAWebCollections.Chat`；认证后 12 秒未收到 `ready` 会检查页面并兜底设为 `READY`；群组名必须**精确匹配**。

**部署与备份：**

1. 必须复制**整个 `whatsapp_bridge/` 文件夹**（`server.js`、`package.json`、`package-lock.json`）。
2. 目标电脑在该目录运行 `npm install`。
3. 如对 `node_modules` 内的库做过手工补丁，重新 `npm install` 会覆盖，请单独备份补丁并记录库版本。
4. 升级 whatsapp-web.js 前先备份整个文件夹。
5. `chrome_profile\session`（登录资料）不要上传 GitHub，也不要发给他人。

---

## 2. 发送规则

| 事件 | 群组是否收到 | 内容 |
|---|---:|---|
| 首次发布 | 是 | 既定文字 + 对应图片 |
| 升级 / 降级（仍有效） | 是 | 当前新等级的文字 + 图片 |
| Update（同等级，仅更新时间变化） | **否** | 仅内部记录 |
| Cancel | 是 | "已取消"文字，**不附图片** |
| 昨天已发、今天仍有效 | 是（每日一次） | 在该群组工作时间内首次成功查询后重发 |
| 今天已发过同一事件 | 否 | — |
| 同轮两条不同警告 | 各发一次 | 分开两次调用 |

- **Cancel 的判定：** 三号变为一号或以下；黄雨取消；黄色工作暑热取消；酷热天气取消。
- 假设：黑雨之后必为红雨，八号之后必为三号，因此属于降级而非取消。
- 文字与图片来自 `whatsapp_service.py`，消息只显示来源时间，无 `+08:00`。

**工作时间与 24/7**

- 群组默认沿用全局工作时间；勾选"自定义本群工作时间"后使用自己的工作日与起止时间。
- 所有群组都在工作时间外、且没有 24/7 群组 → 本轮**不调用任何警告 API**。
- 有 24/7 群组 → 非工作时间仍查询，但只向 24/7 群组发送。
- 24/7 群组今天凌晨已收到的警告，早上不重复；昨天收到的仍有效警告，在其工作时段内重发一次。
- 时间统一为香港时间。

---

## 3. 目录结构

```text
ObervatoryNotice_new/
├─ app.py                     # 入口 + PyQt5 界面
├─ core_new.py                # 路径、日志、设置、数据模型、SQLite
├─ engine_new.py              # 工作时间、去重、逐群发送、调度线程
├─ hko_data.py                # HKO/劳工处取数与标准化
├─ whatsapp_service.py        # 文字模板 + PNG 卡片（不用其中的 Selenium 发送器）
├─ whatsapp_bridge_client.py  # Python <-> Node Bridge 客户端
├─ 清理旧桥接.ps1              # 清理遗留 Bridge（见第 10 节）
├─ assets/warning_icons/      # tc3.png、hsww_amber.png、hot_weather.png 等
├─ whatsapp_bridge/           # server.js、package.json、node_modules
└─ .venv/
```

**未使用的文件（可移走）：** `notification_content.py`（无人导入，且仍 `import core`）、`whatsapp_integration.py`（同样 `import core`）、旧的 `core.py` / `engine.py` / `gui.py`（Tkinter 旧版，不能与 `*_new.py` 混用）。

---

## 4. 安装

- Windows 10/11、Python 3.11+、Node.js 18+（新终端 `node -v` 可用）。Bridge 使用 Puppeteer 自带的 Chrome，不需要另装 Chrome。
- Python 依赖（`whatsapp_service.py` 顶部仍导入 selenium，所以需要安装）：

```powershell
.\.venv\Scripts\python.exe -m pip install PyQt5 requests Pillow selenium
cd whatsapp_bridge; npm install; cd ..
```

- 图标放入 `assets/warning_icons/`，文件名以 `WARNING_CARD_STYLE` 为准。缺图不会报错，只生成没有图标的卡片（日志有 `找不到警告图标`）。
- 启动（**只用一种方式**：PyCharm、BAT、快捷方式、自启动四选一）：

```powershell
.\.venv\Scripts\python.exe app.py
.\.venv\Scripts\python.exe app.py --minimized
```

---

## 5. 使用方法

### 5.1 首次配置

1. 保持**演示模式**，启动 App。
2. **群组**页：新增群组，填 WhatsApp **精确群名**，勾选具体等级。
3. **设置**页：工作日、起止时间、查询间隔、每轮尝试上限。
4. 用**专用测试群组**验证文字、图片、时间。
5. 关闭演示模式 → 保存 → **重启 App** → 在监控首页登录 WhatsApp。

### 5.2 页面

| 页面 | 内容 |
|---|---|
| 监控 | 状态、运行时间、上次查询；开始 / 立即检查 / 暂停 / 最小化 / 退出；**WhatsApp 登录与二维码**；最近结果 |
| 群组 | 读取群组；新增 / 编辑 / 删除；逐等级订阅；24/7；自定义工作时间 |
| 记录 | 投递记录（时间、群组、等级、结果、照片、故障代码）；打开日志目录 |
| 设置 | 全局工作时间、查询间隔、重试上限、演示模式、自动监控、Windows 自启动 |

### 5.3 切换 WhatsApp 账号

**必须先在 WhatsApp 里退出旧账号，再登录新账号。**

1. App 首页点"暂停"。
2. 在手机 WhatsApp 中：**设置 → 已关联设备 → 选中本工具的设备 → 退出登录（删除设备）**。
3. 回到 App，点击"启动／扫码登录"，**等待二维码刷新**（可能需要几十秒），用**新账号**扫码。
4. 若长时间没有出现二维码，或状态一直是 `DISCONNECTED`：
   - `server.js` 收到断线后只把状态设为 `DISCONNECTED`，**不会自己重新初始化**；
   - 请从 App 或托盘选择"退出程序"，用 9.4 ① 确认端口全部关闭，再重新打开 App 点登录。
5. 登录成功后：**重新读取群组，并逐个核对订阅**。旧账号的群名不会自动对应新账号。
6. 先用测试群组做一次真实发送，再恢复正式监控。

补充：
- 只关闭窗口或二维码不等于退出登录。
- 若仍无二维码，先用 9.4 ② 查看 `status/detail`，必要时运行第 10 节的清理脚本。
- 最后手段：退出 App 后把 `chrome_profile\session` **改名备份**（不要直接删除），重启后会重新要求扫码。
- 今天已发送的记录与本地群组编号绑定，切换账号后若复用同一个群组条目，今天已发的警告可能被去重而不再发送；测试请用新的测试群组或模拟事件。

### 5.4 最小化与自启动

- 关闭窗口 → 隐藏到系统托盘继续运行；**真正退出**用托盘菜单或首页"退出程序"（此时 App 会请求 Bridge `/shutdown`）。
- 自启动在 Startup 文件夹写入 `WeatherAlert.cmd`，是**用户登录后**才启动，不是 Windows 服务。
- 电脑睡眠期间不会轮询。长期运行请设置：屏幕可关，睡眠"从不"。

---

## 6. 调用流程

**启动**

```text
python app.py -> main() -> QApplication -> MainWindow.__init__
   setup_logging -> SettingsManager -> Database -> WhatsAppBridgeClient
   -> AlertEngine -> AlertScheduler -> build() -> 托盘 / QTimer
   自动监控：演示模式直接 scheduler.start()；正式模式先 start_whatsapp()，登录后再 start
```

**单轮查询**

```text
AlertScheduler._run -> AlertEngine.run_one_poll_cycle（非阻塞锁）-> _cycle
  过滤群组 -> eligible = 在工作时间内 或 24/7
  eligible 为空 -> 不调用 API
  HKOClient.fetch_warnsum_snapshot -> normalize_warnsum
  fetch_hsww_warning               -> normalize_hsww
  逐条警告：
    已取消 -> _cancel：只发文字
    有效   -> ISSUE / UPGRADE / DOWNGRADE 发送；UPDATE 不发送
```

**发送**

```text
build_warning_message(event) -> 文字
generate_alert_card(event)   -> PNG（Cancel 不生成）
WhatsAppBridgeClient.send_text_and_image(群名, 文字, 图片或 None)
   -> 检查 /status -> POST /send -> server.js 按精确群名定位并发送
interpret_result -> db.save_delivery
```

后台线程通过 `queue` / `pyqtSignal` 回主线程，由 `QTimer -> drain_events()` 刷新界面。

---

## 7. `.py` 互相调用

```text
app.py                 -> core_new, engine_new, whatsapp_bridge_client
engine_new.py          -> core_new, hko_data, whatsapp_service
whatsapp_service.py    -> core_new, hko_data（_fmt_time 等）
hko_data.py            -> core_new
whatsapp_bridge_client -> core_new
        └─(HTTP)-> whatsapp_bridge/server.js -> Puppeteer/Chrome -> WhatsApp Web
未被调用：notification_content.py、whatsapp_integration.py
```

不要让 `hko_data` 反向导入 `whatsapp_service`（会形成循环依赖）。

---

## 8. `def` 速查

**`core_new.py`**

| 名称 | 作用 |
|---|---|
| `get_*_dir()` / `get_db_path()` / `get_config_file_path()` | 返回 `%LOCALAPPDATA%\HKO_WhatsApp_Alert\...` 并自动建目录 |
| `setup_logging()` / `get_logger()` | 每日轮转的 `app.log` |
| `NormalizedWarning.rank / is_active / display_name` | 等级高低、是否有效、名称 |
| `SettingsManager.save / update` | 读写 `settings.json` |
| `Database.list_groups / save_group / delete_group` | 群组与订阅、工作时间 |
| `get_observed / save_observed` | 每类警告上次观测等级（判断发布/升降级/取消） |
| `get_delivery / save_delivery / last_text_send` | 逐群投递记录；日内去重与跨日重发 |
| `recent_deliveries` | 记录页 |

**`engine_new.py`**

| 名称 | 作用 |
|---|---|
| `worktime` / `group_worktime` | 全局 / 群组工作时间 |
| `issue_key` / `transition_key` / `cancellation_key` | 发布、升降级、取消各自独立的去重键 |
| `interpret_result` | 把 Bridge 回传归类为 SENT / FAILED / UNKNOWN 及照片状态 |
| `AlertEngine._cycle / _cancel / _send_to_groups / _record_content_failure` | 单轮流程、取消、逐群发送与重试、内容生成失败记录 |
| `AlertScheduler.start / pause / resume / stop / trigger_immediate_check / _run` | 单一轮询线程 |

**`hko_data.py`**

| 名称 | 作用 |
|---|---|
| `fetch_hsww_warning` / `HKOClient.fetch_warnsum_snapshot` | 取数（HKO 快照存入 `snapshots/`） |
| `normalize_warnsum` / `normalize_hsww` | 标准化为 `NormalizedWarning`；劳工处有效资料在 `raw["hsww"]` |
| `_fmt_time` | 显示为 `YYYY-MM-DD HH:MM`（仅显示用，不改原始数据） |
| `diff_state`、`should_dispatch_to_group`、`build_*_message`、`McpHkoDataSource` | 旧流程遗留，新引擎不使用 |

**`whatsapp_service.py`**

| 名称 | 作用 |
|---|---|
| `WARNING_MESSAGE_FORMATS` | 每个等级 `(标题, 等级名, 状态, 指示语, 来源)`，**必须是完整 5 元组** |
| `WARNING_CARD_STYLE` | 每个等级的图标、颜色、来源 |
| `build_warning_message` / `generate_alert_card` | 文字 / PNG（保存到 `data/generated_images/`） |
| `WhatsAppSender.*` | 旧 Selenium 发送器，不使用 |

**`whatsapp_bridge_client.py`**

| 名称 | 作用 |
|---|---|
| `start_browser` | 启动或复用 Node Bridge；`ERROR` 直接抛错 |
| `is_logged_in` / `is_browser_alive` | `/status == READY` / 进程存活 |
| `get_login_qr_data_url` | `/qr` -> 二维码，供首页显示 |
| `list_groups` / `search_groups` / `verify_group_exists` | 群组列表与校验 |
| `send_text_and_image` | 引擎唯一发送入口，POST `/send` |
| `quit` | POST `/shutdown` 并终止自己启动的进程 |

**`app.py`**

| 名称 | 作用 |
|---|---|
| `theme` / `resizeEvent` | 字体随窗口宽度调整 |
| `setup_tray` / `minimize_to_tray` / `closeEvent` / `exit_app` | 托盘与退出 |
| `configure_startup` | Windows 登录自启动 |
| `GroupEditor` | 订阅、24/7、自定义工作时间 |
| `start_whatsapp` / `poll_auth` / `on_worker_complete` / `hide_qr` | 首页登录与二维码 |
| `start_monitor` / `check_now` / `toggle_monitor` | 控制调度器 |
| `update_runtime` / `drain_events` | 运行时间；显示每轮完成时间 |
| `save_settings` | 校验并保存；改演示模式需重启 |

---

## 9. 日志、数据与端口

### 9.1 位置

```text
%LOCALAPPDATA%\HKO_WhatsApp_Alert\
├─ logs\app.log             # 当天日志；午夜轮转为 app.log.YYYY-MM-DD
├─ config\settings.json
├─ data\app_state.sqlite3   # 群组、观测状态、投递记录
├─ data\snapshots\          # HKO 原始 JSON
├─ data\generated_images\   # 发送用 PNG
└─ chrome_profile\session\  # WhatsApp 登录会话（勿删勿外传）
```

### 9.2 查看日志

```powershell
explorer "$env:LOCALAPPDATA\HKO_WhatsApp_Alert\logs"
Get-Content "$env:LOCALAPPDATA\HKO_WhatsApp_Alert\logs\app.log" -Tail 80
Get-Content "$env:LOCALAPPDATA\HKO_WhatsApp_Alert\logs\app.log" -Wait
Select-String "$env:LOCALAPPDATA\HKO_WhatsApp_Alert\logs\app.log" -Pattern "ERROR|WARNING|Traceback|Bridge"
```

"记录"页有"打开日志目录"按钮；Node Bridge 输出以 `[Bridge]` 前缀写入同一份日志。

### 9.3 列出本机所有 `def`

```powershell
.\.venv\Scripts\python.exe -c "import ast,pathlib; [print(f, [n.name for n in ast.walk(ast.parse(pathlib.Path(f).read_text(encoding='utf-8'))) if isinstance(n,(ast.FunctionDef,ast.ClassDef))]) for f in ['app.py','core_new.py','engine_new.py','hko_data.py','whatsapp_service.py','whatsapp_bridge_client.py']]"
```

### 9.4 用 PowerShell 检查 Bridge 端口是开启还是关闭

Bridge 端口范围为 `8765`–`8784`（仅本机）。

| 场景 | 正常结果 |
|---|---|
| App 已完全退出 | **没有监听端口**（等待 5–10 秒） |
| App 运行中（正式模式） | **恰好 1 个端口**，`status` 为 `READY` 或 `QR_PENDING` |
| 演示模式 | 不启动 Bridge，没有端口 |
| 2 个或更多端口 | 异常，见第 10 节 |

> PowerShell 的 `$PID` 是只读保留变量，循环变量请用 `$procId`。

**① 端口是否开启**（无输出＝全部关闭）

```powershell
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Where-Object { $_.LocalPort -ge 8765 -and $_.LocalPort -le 8784 } |
  Select-Object LocalPort, OwningProcess
```

**② 完整诊断：端口 + 进程 + WhatsApp 状态**

```powershell
$listen = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
  Where-Object { $_.LocalPort -ge 8765 -and $_.LocalPort -le 8784 }
if (-not $listen) {
  Write-Host "Bridge 端口：全部关闭" -ForegroundColor Green
} else {
  $rows = foreach ($item in $listen) {
    $procId = $item.OwningProcess
    $proc = Get-Process -Id $procId -ErrorAction SilentlyContinue
    try {
      $s = Invoke-RestMethod "http://127.0.0.1:$($item.LocalPort)/status" -TimeoutSec 3
      $state = $s.status; $detail = $s.detail
    } catch { $state = "无法读取"; $detail = $_.Exception.Message }
    [pscustomobject]@{ Port = $item.LocalPort; ProcId = $procId
                       Process = $proc.ProcessName; Status = $state; Detail = $detail }
  }
  $rows | Format-Table -AutoSize
  if (@($rows).Count -gt 1) { Write-Host "警告：多个 Bridge 端口，可能有残留" -ForegroundColor Yellow }
  else { Write-Host "仅 1 个（正常）" -ForegroundColor Green }
}
```

**③ 每 5 秒刷新（Ctrl+C 结束）**

```powershell
while ($true) {
  Clear-Host; Get-Date -Format "yyyy-MM-dd HH:mm:ss"
  $l = Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $_.LocalPort -ge 8765 -and $_.LocalPort -le 8784 }
  if ($l) { $l | Select-Object LocalPort, OwningProcess | Format-Table -AutoSize } else { "Bridge 端口：全部关闭" }
  Start-Sleep -Seconds 5
}
```

**④ 手动优雅关闭某个端口**（等同 App 退出时做的事，不删除登录会话）

```powershell
Invoke-RestMethod -Method Post "http://127.0.0.1:8765/shutdown"
```

**⑤ 确认没有 Chrome 仍占用会话目录**（无输出才算干净）

```powershell
$s = "$env:LOCALAPPDATA\HKO_WhatsApp_Alert\chrome_profile\session"
Get-CimInstance Win32_Process |
  Where-Object { $_.Name -eq 'chrome.exe' -and $_.CommandLine -match [regex]::Escape($s) } |
  Select-Object ProcessId, ParentProcessId
```

---

## 10. 清理旧的混乱桥接窗口

用于清理**遗留**的 Bridge：多个 `node.exe server.js`、以及占用登录会话的 Chromium 窗口。正常关闭 App 后不需要。

**先决条件：** 先从 App 或托盘"退出程序"。脚本发现 `app.py` 仍在运行时，执行模式会自动中止。

**做了什么：**

1. 找出监听 `8765`–`8784` 的进程、对应的 `node.exe server.js`、以及命令行含 `chrome_profile\session` 的 Chrome。
2. 先向每个端口发 `/shutdown`（优雅关闭）。
3. 仍残留的 Node，用 `taskkill /T /F` 结束（连同其 Chromium 子进程）。
4. 仍占用会话的 Chrome，再结束一次。
5. 复查端口和 Chrome。

**不会做：** 不删除 `chrome_profile\session`（不会丢登录）；不碰命令行不含会话路径的 Chrome，也不碰不监听这些端口且没有会话子进程的 Node。

**用法：** 保存为 `清理旧桥接.ps1`（**UTF-8 带 BOM**，Windows PowerShell 5.1 才能正确显示中文；单独提供的文件已是此编码）。

```powershell
# 1) 预览（默认，不结束任何进程）
powershell -NoProfile -ExecutionPolicy Bypass -File .\Clean-OldBridge.ps1

# 2) 确认无误后执行
powershell -NoProfile -ExecutionPolicy Bypass -File .\Clean-OldBridge.ps1 -Apply
```

完整代码：

```powershell
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

```

执行后用 9.4 ① 复查：**没有输出**，再只启动一份 App。

> 该脚本未在 PowerShell 环境中实际运行验证。请先用预览模式确认列出的都是你的 Bridge。如果你电脑上另有其他项目也运行 `node server.js` 并监听 `8765`–`8784`，预览时会被列出，请勿执行。

---

## 11. 尚需注意的问题

1. **照片状态记录与 `server.js` 不一致。** `server.js` 中 `SENT`＝文字成功且（带图片时）图片也成功，`SENT_TEXT_ONLY`＝图片失败；但 `interpret_result()` 会把二者都记为 `PHOTO_UNKNOWN`。后果：成功的图片显示"未知"，失败也不显示"失败"。建议在 `explicit` 判断之前加入（只影响记录显示，不影响是否重发）：

   ```python
   if status == "SENT_TEXT_ONLY":
       return "PHOTO_FAILED", "FAILED", code, detail
   if status == "SENT" and photo_requested:
       return "SENT", "SENT", code, detail
   ```

2. **`app.py` 没有 Windows 单实例锁。** 同时启动两份（PyCharm 与 BAT、自启动与手动）仍可能争用同一会话。
3. **异常退出可能残留 Bridge。** 崩溃、被强杀、断电后，下次启动客户端会向上换端口；用 9.4 检查，用第 10 节清理。
4. **POST 超时后的结果不明。** 超时可能发生在服务端已发送之后，被记为 `UNKNOWN` 且**不自动重发**；需人工核对群组。
5. **HTTP 4xx 的细节丢失。** 未就绪时 `server.js` 返回 409 且带 `detail`，Python 端只显示 `Bridge HTTP 409`。
6. **UPDATE 判定依赖来源数据。** 若 `issueTime` 变化会产生新的去重键，是否算新发布取决于 HKO / 劳工处的实际返回。
7. **去重会让"改完没反应"。** 今天已成功发给某群组的同一事件不会再发。测试请用专用测试群组或新的模拟事件，**不要删除正式投递记录**。
8. `/groups` 与 `/search_groups` 仍调用 `client.getChats()`，在受影响的 WhatsApp Web 版本上可能 500；Python 走 `/list_groups`，不受影响。
9. 仅验证 Windows；`CREATE_NO_WINDOW`、Startup 文件夹、`LOCALAPPDATA` 是 Windows 特性，macOS 未验证。
10. whatsapp-web.js 是非官方自动化库，账号可能被限制，请自行评估合规性。

**日常运维：** 开工前看首页（是否已登录、是否演示模式）；出问题先看 `app.log` 末尾和"记录"页故障代码；睡眠设"从不"；定期备份 `app_state.sqlite3`、`settings.json`、`whatsapp_bridge\`；升级时 `app.py + core_new.py + engine_new.py` 三者必须同版本整套替换。

---

## 12. 故障代码

| 代码 | 含义 | 自动重试 |
|---|---|---|
| `SENT` | 文字已发（带图片时图片也成功） | — |
| `SENT_TEXT_ONLY` | 文字已发，图片失败 | 否 |
| `PHOTO_UNKNOWN` | 文字已发，图片结果未确认 | 否 |
| `PHOTO_FAILED` | 文字已发，图片失败 | 否（避免文字重复） |
| `NOT_REQUESTED` | Cancel 等不带图片 | — |
| `FAILED` | 明确未发送（未登录、群不存在、Bridge 未启动等） | 是，本轮内按上限 |
| `UNKNOWN` | 超时或回传异常，无法确认 | **否** |
| `CONTENT_GENERATION_FAILED` | 文字或图片生成失败（不改发纯文字） | 否 |
| `SOURCE_FAILED` | HKO / 劳工处 API 失败 | 下一轮再查 |
| `DRY_RUN` | 演示模式，未真实发送 | — |
