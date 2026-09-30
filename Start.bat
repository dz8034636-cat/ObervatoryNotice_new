@echo off
rem Start the app with no black console window.
rem Put this file in the same folder as app.py and the .venv folder.
cd /d "%~dp0"

if not exist ".venv\Scripts\pythonw.exe" (
    echo Cannot find .venv\Scripts\pythonw.exe
    echo Please put Start.bat in the project folder.
    pause
    exit /b 1
)
if not exist "app.py" (
    echo Cannot find app.py in this folder.
    pause
    exit /b 1
)

start "" ".venv\Scripts\pythonw.exe" "app.py" %*
exit /b 0