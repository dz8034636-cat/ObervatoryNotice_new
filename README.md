# 天气警告通知工具：操作与方法调用指南

> **文档版本说明。** 本指南按你最新确认的方案编写：一个 `app.py` 入口、PyQt5 GUI、WhatsApp 登录及二维码放在监控首页、独立警告订阅、酷热天气图标、Windows 优先。此前上传到对话的 `app.py`／`gui.py`／`engine.py`／`core.py` 仍是较早代码；本指南中标为“新版”的函数表对应对话交付的重构实现和你已描述的本机修改，**不是对未上传的本机最新版做过逐行审计**。`hko_data.py`、`whatsapp_service.py` 和 `whatsapp_bridge_client.py` 部分则依据已上传源码。若你需要与本机所有 `def` 逐字逐项一致，请用本文末尾的清单命令核对并提供最终文件。

## 1. 功能概览

程序在符合工作时间的条件下查询香港天文台警告和劳工处工作暑热警告。按各 WhatsApp 群组勾选的警告等级分别发送文字及相应 PNG 卡片；同轮两条警告发送两条消息。无 24/7 群组时，非工作时间不调用警告 API；明确设为 24/7 的群组可以在非工作时间接收其订阅的警告。

发送内容由 `whatsapp_service.build_warning_message()` 和 `generate_alert_card()` 创建；实际传输由 `WhatsAppBridgeClient.send_text_and_image()` 完成，**不使用** `whatsapp_service.WhatsAppSender` 的 Selenium 发送器。警告消息只显示来源时间，不显示查询时间或 `+08:00`；GUI 每轮检查完成后显示完成时间。

```text
双击 BAT／运行 app.py
  → gui.main() → QApplication → MainWindow
  → 首页 WhatsApp Bridge 启动、二维码、登录状态
  → AlertScheduler → AlertEngine.run_one_poll_cycle()
  → HKO／HSWW 读取与标准化
  → 按工作时间和群组独立订阅过滤
  → whatsapp_service 生成文字、PNG
  → WhatsAppBridgeClient.send_text_and_image()
  → core.Database 记录各群组结果 → GUI 展示
```

## 2. 目录与启动

项目根目录至少包含下列文件；请核对 `app.py` 实际导入的 GUI 文件名和 GUI 导入的引擎文件名：

```text
ObervatoryNotice/
├─ app.py                         # 唯一程序入口 ，app.py 实际导入的 PyQt5 GUI 文件
├─ engine.py                      # 与 core.py 配套的新引擎
├─ core.py                        # 配置、模型、数据库
├─ hko_data.py
├─ whatsapp_service.py
├─ whatsapp_bridge_client.py
├─ whatsapp_bridge/               # 必须保留完整文件夹
│  ├─ server.js
│  ├─ package.json
│  └─ node_modules/               # 在该目录运行 npm install 后产生
├─ assets/
│  └─ warning_icons/
│     └─ hot_weather.png          # 酷热天气图标
├─ .venv/
└─ 启动天气警告通知工具.bat
```

**重要：必须连同整个 `whatsapp_bridge` 文件夹下载或复制。** 只有 `whatsapp_bridge_client.py` 而没有服务端 `server.js`、`package.json` 和已安装的 Node 依赖，不能启动发送服务。源码分发时不一定要复制庞大的 `node_modules`，但目标电脑必须在桥接目录执行 `npm install`。若有服务端所需的其他资源文件，也须一并保留。

Windows 上先安装 Python 项目依赖（至少 PyQt5、requests、Pillow；当前 `whatsapp_service.py` 在导入时也要求 selenium），安装 Node.js，在 `whatsapp_bridge` 目录运行 `npm install`。程序入口固定为 `app.py`；不要同时保留旧 Tkinter 入口并从 BAT 启动它。

### BAT 用法

提供的 BAT 放在与 `app.py` 同一目录：

- 双击：用 `.venv` 中的 `pythonw.exe` 打开 **可见主窗口**，不保留黑色控制台。
- 在命令提示符运行 `启动天气警告通知工具.bat --minimized`：启动并最小化，适合已有登录会话；首次扫码建议不要最小化。
- 在命令提示符运行 `启动天气警告通知工具.bat --debug`：以前台控制台运行，退出后显示错误，便于排查。

BAT 会检查入口、虚拟环境、Bridge 文件夹及 `node_modules`；这些检查不等于 WhatsApp 已登录或发送成功。启动后以首页状态、日志和测试群组结果为准。`app.py` 自身的登录后自启动设置是另一项功能，只在 Windows 用户**登录后**触发，不是无人登录也运行的系统服务。

## 3. GUI 页面内容

### 监控首页

- 标题、监控状态、当前工作／非工作时间、**本次启动的运行时间**、上次查询时间。
- 开始监控、立即检查、暂停／继续、最小化运行、退出程序。
- 同页 WhatsApp 状态、启动／扫码登录、检查登录；未登录时**在首页**展示二维码，扫码成功后自动隐藏二维码。隐藏二维码不关闭 Bridge。
- 最近检查及逐群发送结果；每轮结束追加 `本轮查询完成：YYYY-MM-DD HH:MM:SS`，不显示 `+08:00`。

“本次运行”指从打开 App 起经过的时间，即使非工作时间不查询也继续计时；它不等于真正执行 API 查询的累计时长。若要跨重启累积，需另外持久化，不能用这个计时器代替。

### 群组

读取已登录账户中的群组，新增／编辑／删除订阅。每个群组可独立勾选：三号、八号四个方向、九号、十号；黄／红／黑雨；劳工处黄／红／黑工作暑热；酷热天气。每群默认**非 24/7**；填写精确群组名称，避免只用相似的显示名。

### 记录

查看查询时间、群组、警告级别、投递状态、图片结果和故障代码；打开日志目录。图片生成失败、文字已发但图片失败、结果未知应分开记录，不可一律显示“发送成功”。

### 设置

选择工作日、开始／结束时间、轮询间隔、每轮尝试上限、演示模式、启动程序自动监控、Windows 登录后自启动。演示模式更改后按界面提示重启，避免 GUI 设置和已有桥接进程的 `dry_run` 状态不一致。字体以 Qt 高 DPI 与窗口宽度自适应。

## 4. 工作时间与通知规则

1. 先判断是否有启用且已订阅警告的群组。
2. 在工作时间内查询这些群组需要的数据源；非工作时间仅为明确设为 24/7 的群组查询其订阅所需来源，没有这类群组便不查询 API。
3. HKO、劳工处数据分别读取，某个来源临时失败不应抹掉另一来源的有效数据。
4. 一条警告按其**具体等级**匹配群组；两条警告分别构建、发送、记录。
5. 文字取 `build_warning_message(event)`，图片取 `generate_alert_card(event)`；图片文件生成失败时应记录故障，而不是悄悄只发文字。
6. 根据 Bridge 返回的实际状态更新每群投递；已确认成功的不再重复发送，失败／照片失败／发送结果不明需区别对待。POST 超时可能发生在实际发送之后，“结果不明”不应自动重发整个消息。
7. 仅在完成一轮检查时写 GUI 完成时间；来源警告的发布时间与 App 查询时间分开记录。**内部 SQLite 时间保留原始值；`+08:00` 只在界面显示时去掉。**

图片图标从 `assets/warning_icons/` 按等级载入。酷热天气图标文件使用 `hot_weather.png`，并在 `whatsapp_service.WARNING_CARD_STYLE['HOT_WEATHER']` 对应；请确认你本机已添加该映射。某等级无图标时卡片生成器可能仍产出 PNG，建议卡片始终印上 `event.display_name()`，以便确认图片与警告等级匹配。

## 5. 更换 WhatsApp 账号

**先在 WhatsApp 退出旧账号的关联设备登录，再切换账号。** 不要只关闭 GUI 中的二维码或 Chromium 窗口；那不是退出登录。建议顺序：

1. 暂停监控，避免账号切换期间向错误群组发送。
2. 在旧账号的 WhatsApp 手机端“已关联设备／Linked Devices”中退出本工具使用的网页会话；确认旧会话已注销。
3. 从 App 的“退出程序”或托盘“退出程序”正常退出，让 Bridge 关闭；不要直接杀掉 Python／Node 进程。
4. 重新打开 App，在首页使用新账号扫码，等登录状态确认成功。
5. **重新读取群组并逐一核对订阅。** 数据库里旧账号的精确群组名称不会因为换号自动变成新账号群组；同名群组也应先核实目标，再恢复正式监控。
6. 先在测试群组做一次经确认的测试，再恢复正式发送。

若 Bridge 仍复用旧会话，应先停止 App 与 Bridge、备份会话数据，再按 `server.js` 的实际会话存储位置处理；在未确认服务端实现前**不要删除整个 AppData、SQLite 或项目文件夹**。当前 Python 客户端把 `chrome_profile_dir` 当作 Bridge 的 `SESSION_PATH`，但服务端如何保存子目录须查看实际 `server.js`。

## 6. 函数和调用关系

以下“新版 GUI／引擎／core”列出本次重构设计使用的方法名；如果你的本机版本改过名或删改方法，**以实际运行源码为准**。HKO、Service、Bridge 函数表对应上传源码。`__init__` 是创建对象时调用；以下私有 `_` 方法通常不从外部手动调用。

### 6.1 入口 `app.py`

| `def` | 调用与作用 |
|---|---|
| `main()` | 启动入口；调用 `gui.main()`，进入 Qt 事件循环；不创建 Tkinter 窗口或重复初始化托盘。 |

### 6.2 PyQt5 GUI（新版）模块与弹窗

| `def` | 谁调用 → 做什么／调用什么 |
|---|---|
| `theme(scale)` | `MainWindow.resizeEvent()` → 按窗口宽度生成统一的字体及控件样式。 |
| `app_icon()` | `MainWindow.__init__()`、`setup_tray()` → 生成窗口及托盘图标。 |
| `startup_file()` | 设置页、`configure_startup()` → 确定当前用户的 Windows 登录启动文件。 |
| `configure_startup(enabled)` | `save_settings()` → 添加／删除登录启动项，目标为同一个 `app.py`。 |
| `form_row(title, control)` | `GroupEditor.__init__()`、`MainWindow.build()` → 放置统一的表单行。 |
| `GroupEditor.__init__()` | `edit_group_dialog()` → 构造群组名称、24/7、逐等级勾选弹窗。 |
| `GroupEditor.save()` | 弹窗保存按钮 → 校验群组名称及警告，写回 `GroupConfig`；外层负责数据库保存。 |
| `MainWindow.__init__()` | `gui.main()` → 建配置／DB／Bridge／引擎／调度器，装配 GUI、托盘和计时器。 |
| `MainWindow.build()` | `__init__()` → 创建监控、群组、记录、设置四个页面；WhatsApp 二维码在监控页。 |
| `MainWindow.resizeEvent(event)` | Qt 窗口尺寸变化 → 调用 `theme()` 调整字体与布局样式。 |
| `MainWindow.setup_tray()` | `__init__()` → 建立显示、立即检查、退出的托盘菜单。 |
| `MainWindow.restore_window()` | 托盘显示动作 → 恢复并聚焦主窗口。 |
| `MainWindow.minimize_to_tray()` | 最小化按钮或 `closeEvent()` → 隐藏到托盘；无托盘则最小化到任务栏。 |
| `MainWindow.closeEvent(event)` | Qt 关闭窗口事件 → 默认不退出，改为最小化；真正退出交给 `exit_app()`。 |
| `MainWindow.exit_app()` | 退出按钮／托盘退出 → 停止调度、关闭 Bridge，再退出 Qt；进行中的发送不得被强制中断。 |
| `MainWindow.run_worker(kind, operation)` | 登录、读取群组等耗时动作 → 后台线程执行，通过 `WorkerSignals.completed` 回主线程。 |
| `MainWindow.start_whatsapp()` | 首页登录按钮或自动监控准备 → `sender.start_browser()`，然后开始查询登录及二维码。 |
| `MainWindow.poll_auth()` | 首页检查登录／二维码定时器 → `sender.is_logged_in()`；未登录时请求 `get_login_qr_data_url()`。 |
| `MainWindow.on_worker_complete(kind, result)` | 后台任务完成信号 → 更新 Bridge 状态、显示 QR、关闭 QR、填入群组名称。 |
| `MainWindow.hide_qr()` | 首页隐藏二维码按钮 → 仅隐藏二维码，**不调用** `sender.quit()`。 |
| `MainWindow.start_monitor()` | 开始按钮 → 确认正式模式已登录，再调用 `scheduler.start()`。 |
| `MainWindow.check_now()` | 首页／托盘立即检查 → 调用 `scheduler.trigger_immediate_check()`。 |
| `MainWindow.toggle_monitor()` | 暂停／继续按钮 → 调用 `scheduler.pause()` 或 `resume()`。 |
| `MainWindow.update_runtime()` | 每秒 GUI 定时器 → 更新本次运行时长、当前工作时间标识。 |
| `MainWindow.drain_events()` | GUI 队列定时器 → 接收进度／错误／完成结果，刷新记录，在每轮末尾显示完成时间。 |
| `MainWindow.refresh_groups()` | 启动、编辑、删除后 → 从 DB 读取群组并刷新表格。 |
| `MainWindow.selected_group()` | 编辑／删除动作 → 取得当前表格选中的 `GroupConfig`。 |
| `MainWindow.edit_group_dialog(group)` | 新增／编辑动作 → 弹出 `GroupEditor`；确认后调用 `db.save_group()`。 |
| `MainWindow.add_group()` | 新增按钮 → `edit_group_dialog(None)`。 |
| `MainWindow.edit_group()` | 编辑按钮 → `selected_group()` → `edit_group_dialog(group)`。 |
| `MainWindow.delete_group()` | 删除按钮 → 确认后 `db.delete_group()`、`refresh_groups()`。 |
| `MainWindow.load_groups()` | 群组页读取按钮 → 后台调用 `sender.list_groups('')`；演示模式列表可能是模拟值。 |
| `MainWindow.refresh_history()` | 启动、查询完成、刷新按钮 → `db.recent_deliveries()` 更新投递表。 |
| `MainWindow.save_settings()` | 设置保存按钮 → 校验时间／模式、`SettingsManager.update()`、`configure_startup()`；改演示模式时提示重启。 |
| `main()` | `app.py` → 设置 Qt DPI／字体，创建 `QApplication`、`MainWindow`，运行事件循环。 |

`WorkerSignals.completed` 是 Qt 信号而非 `def`；`QTimer` 的定时回调仍在 GUI 主线程执行，因此网络查询、Node 启动不能直接在回调中长时间阻塞。

### 6.3 告警引擎与调度（新版设计）

| `def` | 谁调用 → 做什么／调用什么 |
|---|---|
| `now()` | 引擎、调度 → 产生内部香港时间戳；GUI 显示时另行格式化。 |
| `worktime(settings)` | 引擎、GUI → 判断工作日与工作时段。 |
| `issue_key(w)` | 引擎 → 生成警告去重键，配合逐群投递记录。 |
| `interpret_result(result, photo_requested)` | 发送循环 → 把 Bridge 回传分类为已发、文字已发照片失败、失败或未知；不能凭未确认结果宣称照片成功。 |
| `AlertEngine.__init__()` | GUI → 注入 DB、配置管理器、Bridge 实例。 |
| `AlertEngine.run_one_poll_cycle()` | 调度器 → 获取单轮锁，调用 `_cycle()`，防止重叠轮询。 |
| `AlertEngine._cycle()` | 单轮入口 → 选目标群组／数据源、读取和标准化、生成通知、逐群发送和落库。 |
| `AlertScheduler.__init__()` | GUI → 保存引擎、配置及事件队列。 |
| `AlertScheduler._emit()` | 调度器内部 → 将开始、进度、完成及错误放入 GUI 队列。 |
| `AlertScheduler.start()` | GUI 登录后／开始按钮 → 启动或唤醒唯一调度线程。 |
| `AlertScheduler.pause()` | GUI → 暂停后续定时检查。 |
| `AlertScheduler.resume()` | GUI → 恢复检查。 |
| `AlertScheduler.trigger_immediate_check()` | GUI／托盘 → 要求调度线程执行手动检查。 |
| `AlertScheduler.stop()` | GUI 真正退出 → 停止线程并等待当前操作收尾。 |
| `AlertScheduler._run()` | 后台线程 → 定期调用 `engine.run_one_poll_cycle()` 并发事件。 |

若你本机引擎仍定义 `text_for(w)`，它是早期自定义通知文案；按现行要求应改由 `whatsapp_service.build_warning_message(w)` 生成，并继续用 `generate_alert_card(w, ...)` 创建照片。若本机引擎还是附件中的 `dispatch_event()`、`diff_state()`／重试游标版本，此表**不适用**，须先统一版本。

### 6.4 配置、模型与仓储（配套新版 `core.py`）

| `def` | 谁调用 → 做什么／调用什么 |
|---|---|
| `get_app_data_dir()`、`get_config_dir()`、`get_data_dir()`、`get_logs_dir()` | GUI、配置、DB、日志 → 创建／返回数据路径。 |
| `get_snapshots_dir()`、`get_generated_images_dir()`、`get_chrome_profile_dir()` | HKO 快照、图片生成、Bridge 会话 → 返回对应目录。 |
| `get_db_path()`、`get_config_file_path()` | `Database`、`SettingsManager` → 确定 SQLite／JSON 位置。 |
| `setup_logging()`、`get_logger(name)` | 入口／各模块 → 初始化并取得日志对象。 |
| `level_display_name(category, level)` | 模型、消息／卡片 → 将等级码变为可读名称。 |
| `NormalizedWarning.rank()`、`is_active()`、`display_name()` | 引擎、GUI／图片 → 比较等级、筛掉取消状态、显示名称。 |
| `SettingsManager.__init__()`、`save()`、`update()` | GUI → 加载和保存设置；`update()` 在保存按钮使用。 |
| `Database.__init__()`、`conn()` | GUI／仓储 → 初始化 SQLite 与事务连接。 |
| `Database.list_groups()`、`save_group()`、`delete_group()` | 群组页／引擎 → 查询、保存、删除订阅。 |
| `Database.get_observed()`、`save_observed()` | 引擎 → 持久化来源观测等级与时间，供下一轮比较。 |
| `Database.get_delivery()`、`save_delivery()` | 引擎 → 按警告／群组查询及记录发送结果、图片结果和故障。 |
| `Database.recent_deliveries()` | 记录页 → 读取最近逐群投递记录。 |

这里列的是重构版契约。最初上传的旧 `core.py` 使用 `warning_state`／`alert_events` 等旧方法，**不能与这里的 `Database.get_observed()`／`recent_deliveries()` 混用**。数据库迁移前先关闭程序并备份整个本地数据目录。

### 6.5 `hko_data.py`（上传文件）

| `def` | 谁调用 → 做什么／调用什么 |
|---|---|
| `HKODataSource.fetch_warnsum()` | 数据源抽象接口 → 子类必须实现。 |
| `HKOOpenDataAPISource.fetch_warnsum()` | `HKOClient.fetch_warnsum_snapshot()` → 请求官方 warnsum JSON。 |
| `fetch_hsww_warning()` | 引擎／`HKOClient.fetch_hsww_snapshot()` → 请求工作暑热 JSON。 |
| `McpHkoDataSource.is_environment_available()` | 客户端选源 → 检查 Node／npx；不表示 MCP 已接入。 |
| `McpHkoDataSource.fetch_warnsum()` | 实验性预留 → 当前未实现实际 MCP 数据读取。 |
| `WarnsumSnapshot.__init__()` | `HKOClient.fetch_warnsum_snapshot()` → 包装原始数据、时间、hash、快照路径。 |
| `HKOClient.__init__()`、`_resolve_source()` | 引擎／客户端 → 选择官方 API 或回退。 |
| `HKOClient.fetch_hsww_snapshot()` | 调用 `fetch_hsww_warning()` 返回劳工处资料。 |
| `HKOClient.fetch_warnsum_snapshot()` | 引擎 → 获取 warnsum、保存原始 JSON 快照并返回对象。 |
| `normalize_warnsum()` | 引擎 → 将风球、暴雨、酷热标准化为 `NormalizedWarning`。 |
| `normalize_hsww()` | 引擎 → 将工作暑热等级及来源时间标准化。 |
| `diff_state()` | **旧引擎** → 比较上一轮与当前等级，决定发出／升级／降级／取消。 |
| `now_hkt()`、`_parse_hhmm()`、`is_within_work_hours()` | **旧工作时间模块** → 计算香港当前工作时间。 |
| `is_high_priority()`、`should_dispatch_to_group()`、`should_resend_on_work_start()` | **旧发送规则** → 含高等级非工作时间例外；新版不得直接沿用该例外。 |
| `group_subscribes_event()` | **旧订阅规则** → 按类别匹配；新版应使用逐等级勾选。 |
| `_fmt_time()` | `whatsapp_service` → 来源时间用于消息和卡片显示，去除 ISO 格式的 `+08:00` 后缀。 |
| `is_urgent()`、`build_single_message()`、`build_merged_message()` | 旧消息路径；新版正式通知应统一调用 `whatsapp_service.build_warning_message()`，不合并多条。 |

### 6.6 `whatsapp_service.py`（上传文件）

| `def` | 谁调用 → 做什么／调用什么 |
|---|---|
| `_load_font()` | `generate_alert_card()` → 读取中文字体；无适用字体时可能出现缺字。 |
| `get_warning_card_style()` | 卡片／图标 → 依据 `warning_level` 取得配色与图标文件名。 |
| `load_warning_icon()` | `paste_warning_icon()` → 从 `assets/warning_icons/` 载入对应 PNG。 |
| `paste_warning_icon()` | `generate_alert_card()` → 在卡片上绘制图标；缺图时返回 False。 |
| `_warning_content()` | `build_warning_message()` → 返回繁体中文等级说明与现场安全提示。 |
| `build_warning_message()` | **新版引擎** → 生成将实际发送的既定文字。 |
| `generate_alert_card()` | **新版引擎** → 生成与同一事件对应的本地 PNG 路径。 |
| `WhatsAppSender.__init__()`、`start_browser()`、`is_logged_in()`、`is_browser_alive()` | **旧 Selenium 发送器**；本方案不调用它们。 |
| `WhatsAppSender._find_and_open_group()`、`verify_group_exists()`、`list_groups()`、`search_groups()` | **旧 Selenium 路径**；本方案用 Bridge 客户端查群。 |
| `WhatsAppSender.send_text_and_image()`、`_send_text()`、`_send_image()`、`quit()` | **旧 Selenium 发送／退出路径**；实际发送只调用 Bridge 同名方法。 |

Hot weather：`WARNING_CARD_STYLE['HOT_WEATHER']` 对应 `hot_weather.png`；`generate_alert_card()` 绘出机构、状态、来源时间及图标。若确实已加等级文字，请检查它不会与图片区、时间重叠。`build_warning_message()` 与图片使用同一 `event`，确保两者等级及来源一致。

### 6.7 `whatsapp_bridge_client.py`（上传文件，保持不改）

| `def` | 谁调用 → 做什么／调用什么 |
|---|---|
| `_find_node_executable()` | `build_node_popen_args()` → 寻找 Node 可执行文件。 |
| `build_node_popen_args()` | `start_browser()` → 组装启动 Node Bridge 的命令。 |
| `_find_free_port()` | `start_browser()` → 为本地 Bridge 选择端口。 |
| `WhatsAppBridgeClient.__init__()` | GUI → 指定会话目录、headless 与演示模式。 |
| `start_browser()` | 首页登录 → 启动 Node Bridge；首次登录可由 GUI 展示 QR。 |
| `_wait_for_bridge_ready()` | `start_browser()` → 等待本地 `/status` 可用。 |
| `is_logged_in()` | 首页／发送前 → 通过 Bridge `/status` 判断是否 READY。 |
| `is_browser_alive()` | GUI／发送前 → 判断 Bridge 子进程是否仍存活。 |
| `get_login_qr_data_url()` | 首页二维码 → 获取 `/qr` 的 data URL；仅在 GUI 中显示，不代表登录成功。 |
| `search_groups()`、`list_groups()` | 群组页 → 请求 Bridge 群组列表，返回名称与 ID；演示模式是模拟数据。 |
| `verify_group_exists()` | 可选群组测试 → 用精确名称检查群组。 |
| `send_text_and_image()` | **引擎唯一实际发送入口** → POST `/send`，传群组精确名称、文字和图片路径。 |
| `quit()` | 真正退出 App → 请求关闭并终止本程序启动的 Bridge 进程。 |

`get_login_qr_data_url()` 只负责读取二维码，隐藏二维码不等于退出 WhatsApp。Bridge 客户端将服务端 `/send` 的 JSON 原样返回；因未收到 `server.js`，不能保证其包含独立的“图片成功”字段或标准故障代码。不要把 `SENT` 擅自解释为照片确认成功。

## 7. 测试与常见故障

- **GUI 可打开但不自动发送：** 检查是否演示模式、是否已登录、群组是否启用与勾选，以及工作时间／24/7 状态。
- **只有旧版方法或缺少 `RANKS`：** 代码混版；核对 `app.py → GUI → engine → core` 的实际导入，先备份再统一文件。
- **有文字没照片：** 看图片是否生成，检查 `/send` 回传、群组实际消息；不要重新发送整条造成文字重复。
- **不见酷热图标：** 核对等级码 `HOT_WEATHER`、`WARNING_CARD_STYLE` 映射、`assets/warning_icons/hot_weather.png` 的文件名与大小写。
- **切号后仍连旧号：** 先在旧账号手机端退出关联设备，再退出 App，核对 Bridge 会话缓存后重新扫码；重新确认群组订阅。
- **黑窗／程序意外退出：** 用 BAT `--debug` 启动，并查看 `%LOCALAPPDATA%\HKO_WhatsApp_Alert\logs\app.log`。
- **电脑睡眠：** 即使最小化或设了 Windows 登录自启，设备睡眠期间也不能按时轮询；关屏与睡眠是不同设置。

在试发前保持演示模式，使用**专用测试群组**确认文字、图片、时间格式、逐群记录、托盘与账号切换。保存旧版数据库备份；不要将 WhatsApp 会话、SQLite 数据库、日志、图标版权不明素材或 `node_modules` 直接上传公开仓库。

## 8. 核对本机所有 `def`

你的本机最新版尚未上传。要列出**实际正在运行的**各模块函数名称，可在项目根目录临时运行以下命令（仅做静态解析，不会发送消息）：

```bat
.venv\Scripts\python.exe -c "import ast,pathlib; files=('app.py','gui.py','engine.py','core.py','hko_data.py','whatsapp_service.py','whatsapp_bridge_client.py'); [(print('\n'+f),[print((n.name+'.'+m.name) if isinstance(n,ast.ClassDef) else n.name) for n in ast.parse(pathlib.Path(f).read_text(encoding='utf-8')).body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) for m in (n.body if isinstance(n,ast.ClassDef) else [n]) if isinstance(m,ast.FunctionDef)]) for f in files]"
```

如果实际 GUI／引擎文件名不同，请在命令中的 `files` 修改。此命令只列顶层函数及类方法，**不会替你证明哪个函数真的被调用**；要做到与运行版完全一致的逐函数调用图，需要依据本机最新版源码进行审计。
