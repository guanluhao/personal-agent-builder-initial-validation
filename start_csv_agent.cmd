@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo 未找到本项目 Python 环境。请先双击 setup_environment.cmd。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" "agents\csv-agent\agent.py"
pause
