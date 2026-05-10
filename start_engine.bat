@echo off
cd /d G:\pure-ai-orchestrator\lingshu_full
del /f /q lingshu.db 2>nul
start /B /wait py -3.12 run_engine.py > engine.log 2>&1