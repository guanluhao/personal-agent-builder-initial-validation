@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" python -m venv .venv
if not exist ".venv\Scripts\python.exe" (
  echo 无法创建 Python 环境；请先安装 Python 3.10 或更高版本。
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m pip install -e ".[mcp,test]" numpy matplotlib
if errorlevel 1 (
  echo 依赖安装未完成；请查看上方错误信息。
  pause
  exit /b 1
)
echo 本项目环境已准备好。现在可双击 start_app.vbs 打开图形界面。
pause
