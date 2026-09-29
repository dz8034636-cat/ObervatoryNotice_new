@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PYTHON=%~dp0.venv\Scripts\python.exe"
set "PYTHONW=%~dp0.venv\Scripts\pythonw.exe"
set "ENTRY=%~dp0app.py"

if not exist "%ENTRY%" goto no_entry
if not exist "%PYTHON%" goto no_python
if not exist "whatsapp_bridge\server.js" goto no_bridge
if not exist "whatsapp_bridge\package.json" goto no_bridge
if not exist "whatsapp_bridge\node_modules\" goto no_modules

where node >nul 2>&1
if errorlevel 1 echo [注意] PATH 未找到 Node.js；如桥接不能启动，请安装 Node.js 并重新打开终端。

if /i "%~1"=="--debug" goto debug
if not exist "%PYTHONW%" goto debug
if /i "%~1"=="--minimized" (
    start "" "%PYTHONW%" "%ENTRY%" --minimized
) else (
    start "" "%PYTHONW%" "%ENTRY%"
)
exit /b 0

:debug
echo [启动] 前台调试模式。程序关闭前请保留此窗口。
"%PYTHON%" -u "%ENTRY%"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" echo [错误] app.py 退出代码：%RC%
pause
exit /b %RC%

:no_entry
echo [错误] 未找到 app.py。请把 BAT 文件放在项目根目录。
goto failed
:no_python
echo [错误] 未找到 .venv\Scripts\python.exe。请先建立项目虚拟环境。
goto failed
:no_bridge
echo [错误] 缺少 whatsapp_bridge\server.js 或 package.json。
echo 必须保留完整 whatsapp_bridge 文件夹，不能只复制 Python 客户端。
goto failed
:no_modules
echo [错误] 缺少 whatsapp_bridge\node_modules。请在 whatsapp_bridge 文件夹运行 npm install。
goto failed
:failed
pause
exit /b 1
