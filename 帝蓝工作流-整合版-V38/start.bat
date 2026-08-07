@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting Jintong Workflow Integrated V1...
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" integrated_server.py
) else (
  python integrated_server.py
)
pause
