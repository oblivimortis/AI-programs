@echo off
REM 双击即可回滚到加载 notice 技能之前的版本。
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0restore.ps1" %*
pause
