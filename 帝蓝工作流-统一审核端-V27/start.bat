@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
    echo 使用本地虚拟环境启动...
    ".venv\Scripts\python.exe" "review_server.py"
) else (
    echo 未找到 .venv，尝试直接使用系统 Python 启动...
    py -3 "review_server.py"
    if errorlevel 1 (
        python "review_server.py"
    )
)

pause
