@echo off
REM 双击打开 MemoryHub 本地记忆面板(Windows;首次自动初始化)
REM 与 mac 版「打开记忆面板.command」行为对齐:UTF-8 输出、任意路径定位、
REM 检查 Python、首跑补齐素材库与目录、就绪轮询后再开浏览器、失败停窗给线索。
chcp 65001 >nul
setlocal enabledelayedexpansion
title MemoryHub 记忆面板

REM ---- 定位 MemoryHub 安装目录(支持任意路径):环境变量 / 自身目录 / 记录文件 / 默认位置 ----
set "POINTER=%USERPROFILE%\.memoryhub_home"
set "HUB="
if not "%MEMORYHUB_HOME%"=="" if exist "%MEMORYHUB_HOME%\scripts\web\server.py" set "HUB=%MEMORYHUB_HOME%"
if not defined HUB if exist "%~dp0scripts\web\server.py" set "HUB=%~dp0."
if not defined HUB if exist "%POINTER%" (
  set /p _P=<"%POINTER%"
  if exist "!_P!\scripts\web\server.py" set "HUB=!_P!"
)
if not defined HUB if exist "%USERPROFILE%\MemoryHub\scripts\web\server.py" set "HUB=%USERPROFILE%\MemoryHub"

if not defined HUB (
  echo 找不到 MemoryHub 安装目录。
  echo 解决办法^(任选其一^):
  echo   - 把本启动器放回 MemoryHub 目录内再双击
  echo   - 或把安装路径写入记录文件: echo 你的\MemoryHub路径 ^> "%POINTER%"
  echo   - 或设置环境变量 MEMORYHUB_HOME 后重试
  pause
  exit /b 1
)
cd /d "%HUB%"
REM 记住这次定位到的目录,方便下次从任意路径启动
>"%POINTER%" echo %CD%

REM ---- 检查 Python ----
where python >nul 2>nul
if errorlevel 1 (
  echo 未检测到 Python。请到 https://www.python.org 下载安装 Python 3.11 或更高版本,
  echo 安装时务必勾选 "Add Python to PATH",装好后重新双击本启动器。
  pause
  exit /b 1
)

REM ---- 首次:建虚拟环境 + 装依赖 + 建库 + 建目录 ----
if not exist ".venv\Scripts\python.exe" (
  echo 首次启动:正在初始化环境^(建虚拟环境 + 装依赖^),需要几分钟,请稍候...
  python -m venv .venv
  if errorlevel 1 ( echo 创建虚拟环境失败,请确认 Python 安装完整。& pause & exit /b 1 )
  call ".venv\Scripts\activate.bat"
  call :pip_install --upgrade pip
  call :pip_install -r requirements.txt
  if errorlevel 1 ( echo 依赖安装失败^(见上方日志^)。请检查网络后重试。& pause & exit /b 1 )
  python scripts\init_db.py
  python scripts\init_material_db.py
  for %%D in (logs imports staging raw raw\claude-code raw\claude-memory raw\claude-web raw\chatgpt raw\evolution raw\materials raw\roundtable vault vault\cards vault\materials) do (
    if not exist "%%D" mkdir "%%D"
  )
  if not exist ".env" copy ".env.example" ".env" >nul
)

set "PY=%HUB%\.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
if not exist logs mkdir logs

REM ---- 就绪探测优先用 curl;老 Windows(1803 以下)无 curl 时降级为固定等待 ----
set "HAVE_CURL="
where curl >nul 2>nul && set "HAVE_CURL=1"

REM ---- 若未在运行则启动(日志落 logs\web.log)----
set "RUNNING="
if defined HAVE_CURL ( curl -s -o nul http://127.0.0.1:7788/api/stats 2>nul && set "RUNNING=1" )
if not defined RUNNING (
  start "MemoryHub" /min cmd /c ""%PY%" "scripts\web\server.py" > "logs\web.log" 2>&1"
)

REM ---- 就绪后再开浏览器,避免打开太早白屏 ----
set "READY="
if defined HAVE_CURL (
  for /l %%i in (1,1,60) do (
    if not defined READY (
      curl -s -o nul http://127.0.0.1:7788/api/stats 2>nul && set "READY=1"
      if not defined READY timeout /t 1 >nul
    )
  )
) else (
  REM 无 curl:固定等 6 秒兜底(不做健康检查,尽量避免白屏)
  timeout /t 6 >nul
  set "READY=1"
)
if not defined READY (
  echo 面板未能在预期时间内启动。请查看 logs\web.log 里的报错。
  echo 常见原因:依赖没装全、或端口 7788 被别的程序占用。
  pause
  exit /b 1
)
start "" http://127.0.0.1:7788
exit /b 0

REM ---- pip 安装带重试 + 备用源兜底:镜像源瞬时抽风时自愈 ----
:pip_install
for %%U in (https://pypi.tuna.tsinghua.edu.cn/simple https://mirrors.aliyun.com/pypi/simple https://pypi.org/simple) do (
  echo   尝试 pip 源: %%U
  python -m pip install --retries 5 --timeout 30 -i %%U %*
  if not errorlevel 1 exit /b 0
  echo   该源失败,换下一个源...
)
echo   所有镜像源均失败: pip install %*
exit /b 1
