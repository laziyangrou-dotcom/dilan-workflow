@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo Creating .venv and checking dependencies
echo ============================================

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m venv .venv
) else (
  python -m venv .venv
)

if not exist ".venv\Scripts\python.exe" (
  echo Failed to create .venv. Please install Python 3.10 or newer first.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" -m pip install --upgrade pip setuptools wheel
if exist requirements.txt (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt --prefer-binary
)

echo.
echo Install finished. You can double click start.bat to run the integrated tool.
pause
