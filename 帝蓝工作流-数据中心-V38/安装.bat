@echo off
chcp 65001 >nul
cd /d %~dp0
echo 正在创建虚拟环境 .venv ...
python -m venv .venv
call .venv\Scripts\activate.bat
echo 正在安装依赖 ...
python -m pip install --upgrade pip
pip install -r requirements.txt
echo.
echo 安装完成！之后请双击 start.bat 启动数据中心。
pause
