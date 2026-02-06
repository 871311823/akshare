@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM 一键启动脚本：AkShare 周线 MACD 选股器（Flask）
REM 功能：
REM 1) 关闭占用端口的进程（默认 5001，可用 PORT 环境变量覆盖）
REM 2) 清理 Python 缓存（__pycache__ / *.pyc）与旧的 pid/log
REM 3) 自动创建/复用 venv、安装依赖，并后台启动应用
REM 4) 等待端口真正可访问后才返回（避免“启动成功但脚本误判失败”）
REM
REM 用法：
REM   scripts\start_screener.bat
REM   set PORT=5001
REM   set HOST=127.0.0.1
REM   scripts\start_screener.bat
REM
REM （可选）安装为“开机/登录自启”任务（当前用户）：
REM   scripts\start_screener.bat install
REM   scripts\start_screener.bat uninstall

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"

set "PORT=%PORT%"
if "%PORT%"=="" set "PORT=5001"

set "HOST=%HOST%"
if "%HOST%"=="" set "HOST=127.0.0.1"

set "TASK_NAME=AkShareScreener_%PORT%"

if /I "%~1"=="install" goto :install_task
if /I "%~1"=="uninstall" goto :uninstall_task

set "APP_SCRIPT=%REPO_ROOT%\examples\stock_screener_web.py"
set "VENV_DIR=%REPO_ROOT%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "PID_FILE=%REPO_ROOT%\.screener_%PORT%.pid"
set "LOG_FILE=%REPO_ROOT%\.screener_%PORT%.log"

echo [INFO] repo=%REPO_ROOT%
echo [INFO] host=%HOST% port=%PORT%

if not exist "%APP_SCRIPT%" (
  echo [ERROR] 未找到应用脚本: %APP_SCRIPT%
  exit /b 1
)

REM 0) 停旧 PID
if exist "%PID_FILE%" (
  for /f "usebackq delims=" %%P in ("%PID_FILE%") do set "OLDPID=%%P"
  if not "!OLDPID!"=="" (
    echo [INFO] 停止旧进程 pid=!OLDPID!
    taskkill /PID !OLDPID! /T /F >nul 2>nul
  )
  del /f /q "%PID_FILE%" >nul 2>nul
)

REM 1) 关闭占用端口的进程
call :kill_port %PORT%

REM 2) 清理缓存/旧日志
echo [INFO] 清理缓存与旧日志...
for /d /r "%REPO_ROOT%" %%D in (__pycache__) do (
  rd /s /q "%%D" >nul 2>nul
)
del /s /q "%REPO_ROOT%\*.pyc" >nul 2>nul
del /f /q "%LOG_FILE%" >nul 2>nul

REM 3) venv + 依赖
if not exist "%VENV_PY%" (
  echo [INFO] 创建 venv: %VENV_DIR%
  python -m venv "%VENV_DIR%"
  if errorlevel 1 (
    echo [ERROR] 创建 venv 失败，请确认已安装 Python。
    exit /b 1
  )
)

echo [INFO] 安装依赖（pip / editable / flask）...
"%VENV_PY%" -m pip install -U pip >nul
"%VENV_PY%" -m pip install -e "%REPO_ROOT%" >nul
"%VENV_PY%" -m pip install -U flask >nul

REM 4) 后台启动（用 PowerShell 获取 PID，更可靠）
set "START_PS=$ErrorActionPreference='Stop';" ^
+ "Remove-Item -Force -ErrorAction SilentlyContinue '%LOG_FILE%';" ^
+ "$env:HOST='%HOST%'; $env:PORT='%PORT%';" ^
+ "$p=Start-Process -FilePath '%VENV_PY%' -ArgumentList @('-u','%APP_SCRIPT%') -WindowStyle Hidden -RedirectStandardOutput '%LOG_FILE%' -RedirectStandardError '%LOG_FILE%' -PassThru;" ^
+ "Write-Output $p.Id"

for /f "usebackq delims=" %%I in (`powershell -NoProfile -Command "%START_PS%"`) do set "NEWPID=%%I"

if "%NEWPID%"=="" (
  echo [ERROR] 启动失败：未获取到 PID
  if exist "%LOG_FILE%" powershell -NoProfile -Command "Get-Content -Tail 120 '%LOG_FILE%'"
  exit /b 1
)

echo %NEWPID%>"%PID_FILE%"
echo [INFO] 已启动 pid=%NEWPID%

REM 5) 等待端口就绪（最多 ~15 秒）
echo [INFO] 等待服务就绪...
set "WAIT_PS=$ok=$false; for($i=0;$i -lt 50;$i++){ try{ $c=New-Object Net.Sockets.TcpClient('127.0.0.1',%PORT%); $c.Close(); $ok=$true; break } catch { Start-Sleep -Milliseconds 300 } }; if($ok){ exit 0 } else { exit 1 }"
powershell -NoProfile -Command "%WAIT_PS%"
if errorlevel 1 (
  echo [ERROR] 端口 %PORT% 仍不可用，输出日志：
  if exist "%LOG_FILE%" powershell -NoProfile -Command "Get-Content -Tail 200 '%LOG_FILE%'"
  exit /b 1
)

echo [OK] 启动成功
echo [INFO] 访问地址: http://%HOST%:%PORT%
echo [INFO] 日志文件: %LOG_FILE%
echo [INFO] PID 文件: %PID_FILE%
exit /b 0

:kill_port
set "P=%~1"
for /f "tokens=5" %%A in ('netstat -ano ^| findstr /R /C:":%P% .*LISTENING"') do (
  echo [INFO] 发现端口 %P% 被占用，taskkill PID=%%A
  taskkill /PID %%A /T /F >nul 2>nul
)
exit /b 0

:install_task
echo [INFO] 安装计划任务: %TASK_NAME%
REM 当前用户登录后自启；/RL LIMITED 避免要求管理员权限
schtasks /Create /F /SC ONLOGON /TN "%TASK_NAME%" /TR "\"%~f0\"" /RL LIMITED >nul
if errorlevel 1 (
  echo [ERROR] 创建计划任务失败（可能需要权限/系统策略限制）。
  exit /b 1
)
echo [OK] 已安装自启任务: %TASK_NAME%
exit /b 0

:uninstall_task
echo [INFO] 删除计划任务: %TASK_NAME%
schtasks /Delete /F /TN "%TASK_NAME%" >nul 2>nul
echo [OK] 已删除（如不存在则已忽略）
exit /b 0
