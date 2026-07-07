@echo off
REM 双击打开 MemoryHub 本地记忆面板(Windows;首次自动初始化)
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo 首次启动:正在初始化环境(建虚拟环境 + 装依赖),稍等...
  python -m venv .venv
  call ".venv\Scripts\activate.bat"
  call :pip_install --upgrade pip
  call :pip_install -r requirements.txt
  python scripts\init_db.py
  if not exist ".env" copy ".env.example" ".env" >nul
)

start "" ".venv\Scripts\python.exe" "scripts\web\server.py"
timeout /t 3 >nul
start "" http://127.0.0.1:7788
goto :eof

REM pip 安装带重试 + 备用源兜底:镜像源瞬时抽风(如 numpy "from versions: none")时自愈
:pip_install
for %%U in (https://pypi.tuna.tsinghua.edu.cn/simple https://mirrors.aliyun.com/pypi/simple https://pypi.org/simple) do (
  echo   尝试 pip 源: %%U
  python -m pip install --retries 5 --timeout 30 -i %%U %*
  if not errorlevel 1 goto :eof
  echo   该源失败,换下一个源...
)
echo   所有镜像源均失败: pip install %*
goto :eof
