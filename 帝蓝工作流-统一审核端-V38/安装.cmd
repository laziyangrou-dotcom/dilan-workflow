@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

echo ========================================
echo 帝蓝工作流 - 统一审核端 依赖安装
echo ========================================
echo 当前目录：%cd%
echo.

set "USE_PY=0"
py -3 -c "import sys; print(sys.version)" >nul 2>nul
if not errorlevel 1 set "USE_PY=1"

if "%USE_PY%"=="1" (
    echo 使用 Python Launcher: py -3
    py -3 -m venv ".venv"
) else (
    python -c "import sys; print(sys.version)" >nul 2>nul
    if errorlevel 1 (
        echo [错误] 没有找到可用的 Python。
        echo 请先安装 Python 3.10 或更高版本，并勾选 Add Python to PATH。
        echo.
        pause
        exit /b 1
    )
    echo 使用 Python: python
    python -m venv ".venv"
)

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 虚拟环境创建失败。
    echo.
    pause
    exit /b 1
)

echo.
echo 正在升级 pip...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 (
    echo [错误] pip 升级失败。
    echo.
    pause
    exit /b 1
)

echo.
echo 正在安装 requirements.txt 依赖...
".venv\Scripts\python.exe" -m pip install -r "requirements.txt"
if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络、pip 源或 Python 环境。
    echo.
    pause
    exit /b 1
)

echo.
echo ========================================
echo 安装完成。
echo 之后双击 start.bat 启动审核端。
echo ========================================
pause
