@echo off
chcp 65001 >nul
cd /d G:\pure-ai-orchestrator\lingshu_full
setlocal enabledelayedexpansion

REM ============================================================
REM LINGSHU 永恒守护 - 停止入口
REM 双击此文件即可安全停止永恒守护进程和引擎
REM ============================================================

echo ============================================================
echo   LINGSHU 永恒守护进程 停止中...
echo ============================================================
echo.

REM 1. 创建停止信号（守护进程检测到后会自行退出）
if not exist ".lingshu" mkdir .lingshu

echo 1 > ".lingshu\STOP_ETERNAL"
echo [INFO] 已创建停止信号，等待守护进程退出...

REM 2. 读取守护进程 PID 并等待它退出
timeout /t 3 /nobreak >nul

REM 3. 杀死引擎进程
for /f "tokens=*" %%a in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Process -Name python* -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'main\.py.*engine' -or $_.CommandLine -match 'run_engine\.py' } | ForEach-Object { $_.Id }"') do (
    taskkill /F /PID %%a >nul 2>&1
    if !errorlevel! equ 0 echo [INFO] 已停止引擎进程 PID: %%a
)

REM 4. 杀死 watchdog 进程
for /f "usebackq tokens=*" %%a in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "if(Test-Path '.lingshu\watchdog.pid'){Get-Content '.lingshu\watchdog.pid' -Raw}else{echo 0}"`) do set WPID=%%a

if not "%WPID%"=="0" (
    taskkill /F /PID %WPID% >nul 2>&1
    if !errorlevel! equ 0 echo [INFO] 已停止守护进程 PID: %WPID%
)

REM 5. 清理
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-Process -Name python*,powershell* -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -match 'eternal_watchdog' } | Stop-Process -Force -ErrorAction SilentlyContinue"

timeout /t 2 /nobreak >nul

REM 6. 移除停止信号
if exist ".lingshu\STOP_ETERNAL" del /f /q ".lingshu\STOP_ETERNAL"
if exist ".lingshu\watchdog.pid" del /f /q ".lingshu\watchdog.pid"

echo.
echo [OK] 永恒守护进程已停止。
echo [INFO] 引擎进程已清理。
echo.
echo 重新启动请双击: start_eternal.bat
echo.
pause