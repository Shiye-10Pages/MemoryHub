@echo off
REM 双击打开 MemoryHub 本地记忆面板(Windows;首次自动初始化)
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo 首次启动:正在初始化环境(建虚拟环境 + 装依赖),稍等...
  python -m venv .venv
  call ".venv\Scripts\activate.bat"
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
  python scripts\init_db.py
  if not exist ".env" copy ".env.example" ".env" >nul
)

start "" ".venv\Scripts\python.exe" "scripts\web\server.py"
timeout /t 3 >nul
start "" http://127.0.0.1:7788
