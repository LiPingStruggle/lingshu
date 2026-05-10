@echo off
chcp 65001 >nul
cd /d G:\pure-ai-orchestrator\lingshu_full

REM ============================================================
REM LINGSHU 永恒守护 - 注册开机自启计划任务
REM 以管理员身份运行此文件
REM ============================================================

echo ============================================================
echo   LINGSHU 永恒守护 - 注册系统自启
echo   注意：需要管理员权限
echo   如果未以管理员运行，请右键选择“以管理员身份运行”
echo ============================================================
echo.

REM 检测是否管理员权限
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] 需要管理员权限！
    echo.
    echo 请右键点击本文件，选择“以管理员身份运行”
    pause
    exit /b 1
)

echo [INFO] 正在注册计划任务 "LingShuEternalWatchdog"...

schtasks /Create /F /TN "LingShuEternalWatchdog" /SC ONLOGON /DELAY 0000:01 /RL HIGHEST /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File G:\pure-ai-orchestrator\lingshu_full\eternal_watchdog.ps1" /IT

if %errorlevel% equ 0 (
    echo [OK] 计划任务注册成功！
    echo.
    echo ------------------------------------------------------------
    echo  计划任务名称: LingShuEternalWatchdog
    echo  触发条件: 用户登录时（延迟 1 分钟）
    echo  运行权限: 最高权限
    echo  守护脚本: eternal_watchdog.ps1
    echo  工作目录: G:\pure-ai-orchestrator\lingshu_full
    echo.
    echo  效果:
    echo  - 每次开机/登录后自动启动
    echo  - 自动崩溃重启
    echo  - 完全后台运行
    echo  停止方式: 双击 stop_eternal.bat
    echo ------------------------------------------------------------
    echo.
    echo 立即启动守护进程？(Y/N)
    set /p CHOICE=
    if /i "!CHOICE!"=="Y" (
        schtasks /Run /TN "LingShuEternalWatchdog"
        echo [OK] 已启动
    ) else (
        echo [INFO] 下次登录时将自动启动
    )
) else (
    echo [ERROR] 计划任务注册失败！
    echo 请尝试手动注册:
    echo   schtasks /Create /F /TN "LingShuEternalWatchdog" /SC ONLOGON /DELAY 0000:01 /RL HIGHEST /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File G:\pure-ai-orchestrator\lingshu_full\eternal_watchdog.ps1" /IT
)

echo.
pause