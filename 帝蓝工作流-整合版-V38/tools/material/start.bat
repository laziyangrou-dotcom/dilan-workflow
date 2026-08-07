@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting Jintong Art Tool V1...
start "" cmd /c "timeout /t 2 /nobreak >nul & start http://127.0.0.1:8790"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" image_server.py
) else (
  python image_server.py
)
pause
