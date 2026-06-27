@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting Dilan Workflow Integrated V33...
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" integrated_server.py
) else (
  python integrated_server.py
)
pause
