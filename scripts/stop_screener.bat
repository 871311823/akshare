@echo off
setlocal EnableExtensions EnableDelayedExpansion

REM 停止脚本：AkShare 周线 MACD 选股器（Flask）
REM 用法：
REM   scripts\stop_screener.bat
REM   set PORT=5001
REM   scripts\stop_screener.bat

set "SCRIPT_DIR=%~dp0"
for %%I in ("%SCRIPT_DIR%..") do set "REPO_ROOT=%%~fI"

set "PORT=%PORT%"
if "%PORT%"=="" set "PORT=5001"

set "PID_FILE=%REPO_ROOT%\.screener_%PORT%.pid"

echo [INFO] 停止 port=%PORT%

if exist "%PID_FILE%" (
  for /f "usebackq delims=" %%P in ("%PID_FILE%") do set "PID=%%P"
  if not "!PID!"=="" (
    echo [INFO] taskkill pid=!PID!
    taskkill /PID !PID! /T /F >nul 2>nul
  )
  del /f /q "%PID_FILE%" >nul 2>nul
)

for /f "tokens=5" %%A in ('netstat -ano ^| findstr /R /C:":%PORT% .*LISTENING"') do (
  echo [INFO] 发现端口 %PORT% 监听进程 PID=%%A，强制结束
  taskkill /PID %%A /T /F >nul 2>nul
)

echo [OK] 已停止（如未运行则已忽略）
exit /b 0
