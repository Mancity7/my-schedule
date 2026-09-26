@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo.
echo  正在停止 A股策略验证系统 ...
echo.
docker compose down
echo.
echo  ================================================
echo   已停止。
echo.
echo   你的策略、回测记录、已下载的股票数据
echo   都保存在本文件夹的 data 目录里，不会丢失。
echo   下次双击 start.bat 就能接着用。
echo  ================================================
echo.
pause
