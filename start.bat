@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo.
echo  ================================================
echo     A股策略验证系统  -  启动中
echo  ================================================
echo.

docker info >nul 2>&1
if errorlevel 1 (
  echo  [X] Docker 没有在运行。
  echo.
  echo      解决办法：
  echo      1. 按键盘上的 Win 键，输入 Docker Desktop
  echo      2. 双击打开它，耐心等 1-2 分钟
  echo      3. 等到屏幕右下角的"小鲸鱼"图标不再闪动
  echo      4. 再回来双击本文件
  echo.
  pause
  exit /b 1
)

echo  [1/3] 准备程序...
echo        第一次运行需要下载依赖，大约 3-10 分钟，请耐心等待。
echo        以后每次启动只要几秒钟。
echo.
docker compose up -d --build
if errorlevel 1 (
  echo.
  echo  [X] 启动失败。请把上面显示的红字截图保存下来。
  echo      查看日志命令： docker compose logs --tail 100
  echo.
  pause
  exit /b 1
)

echo.
echo  [2/3] 等待服务就绪...
set /a tries=0
:waitloop
timeout /t 2 /nobreak >nul
curl -s -o nul http://localhost:8000/api/health
if not errorlevel 1 goto ready
set /a tries+=1
if %tries% lss 60 goto waitloop
echo.
echo  [X] 等待超时。请运行 logs.bat 查看日志。
echo.
pause
exit /b 1

:ready
echo  [3/3] 打开浏览器...
start "" http://localhost:8000

echo.
echo  ================================================
echo   [OK] 启动成功！
echo.
echo   在浏览器打开：  http://localhost:8000
echo.
echo   想关闭系统：  双击 stop.bat
echo   想看运行日志：双击 logs.bat
echo  ================================================
echo.
echo  这个黑色窗口可以关掉了，系统会一直在后台运行。
echo.
pause
endlocal
