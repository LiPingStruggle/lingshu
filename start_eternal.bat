@echo off
chcp 65001 >nul
cd /d G:\pure-ai-orchestrator\lingshu_full

REM ============================================================
REM LINGSHU 永恒守护 - 启动入口
REM 双击此文件即可启动永不停止的引擎守护
REM 唯一停止方式：双击 stop_eternal.bat
REM ============================================================

if not exist ".lingshu" mkdir .lingshu

REM 清理旧的停止信号（如果有）
if exist ".lingshu\STOP_ETERNAL" del /f /q ".lingshu\STOP_ETERNAL"

echo ============================================================
echo   LINGSHU 永恒守护进程 启动中...
echo   工作目录: %CD%
echo   检查间隔: 5分钟
echo   引擎方式: python main.py engine 99999
echo ============================================================
echo.
echo [INFO] 启动 PowerShell 守护进程（隐藏窗口运行）...

REM 以后台方式启动 PowerShell 看门狗
start /B /MIN powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0eternal_watchdog.ps1"

REM 等待并检查是否启动成功
timeout /t 3 /nobreak >nul

for /f "usebackq tokens=*" %%a in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "if(Test-Path '.lingshu\watchdog.pid'){Get-Content '.lingshu\watchdog.pid' -Raw}else{echo 0}"`) do set PID=%%a

if "%PID%"=="" set PID=0
if "%PID%"=="0" (
    echo [ERROR] 守护进程似乎未启动，请检查 powershell 执行策略
    echo. 
    echo 尝试手动启动:
    echo   powershell -ExecutionPolicy Bypass -File "%~dp0eternal_watchdog.ps1"
    pause
    exit /b 1
)

echo [OK] 永恒守护进程已启动，PID: %PID%
echo.
echo ------------------------------------------------------------
echo  查看日志: type ".lingshu\eternal_watchdog.log"
echo  停止守护: 双击 stop_eternal.bat
echo  检查状态: tasklist /FI "PID eq %PID%"
echo.
echo  守护进程每 5 分钟检查一次引擎状态
echo  引擎崩溃/退出/被杀都会自动重启
echo  除非双击 stop_eternal.bat，否则永不停止
echo ------------------------------------------------------------
echo.
pause