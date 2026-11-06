@echo off
setlocal
chcp 65001 > nul
title Telegram Business - Mini App
cd /d "%~dp0"
python run.py
set "app_exit=%errorlevel%"
pause
exit /b %app_exit%
