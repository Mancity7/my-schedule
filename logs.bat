@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  下面是系统运行日志，按 Ctrl+C 退出查看（不会停止系统）
echo  ================================================
echo.
docker compose logs -f --tail 200
