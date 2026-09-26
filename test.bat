@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo.
echo  ================================================
echo     自检  -  自动跑一遍"能用数字复核"的验收项
echo  ================================================
echo.

docker info >nul 2>&1
if errorlevel 1 goto no_docker

echo  [1/3] 检查部署配置
set RC=0
findstr /C:"127.0.0.1:8000:8000" docker-compose.yml >nul
if errorlevel 1 (
  echo        [X] 端口没有只绑本机
  set RC=1
) else (
  echo        [OK] 端口只绑本机，同 WiFi 别人打不开
)
findstr /C:"no-new-privileges:true" docker-compose.yml >nul
if errorlevel 1 (
  echo        [X] 缺少 no-new-privileges
  set RC=1
) else (
  echo        [OK] 容器禁止提权
)

echo.
echo  [2/3] 准备运行环境（改了代码会自动重建，稍等）
docker compose up -d --build
if errorlevel 1 goto build_fail

echo.
echo  [3/3] 运行自检
echo  ------------------------------------------------
docker compose exec -T app python -m app.selfcheck %*
echo  ------------------------------------------------
if errorlevel 1 goto test_fail

echo.
echo  ================================================
echo   [OK] 全部通过
echo  ================================================
echo.
pause
endlocal
exit /b 0

:no_docker
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

:build_fail
echo.
echo  [X] 环境起不来。请双击 logs.bat 查看日志，或直接告诉我上面的红字。
echo.
pause
exit /b 1

:test_fail
echo.
echo  [X] 有测试没通过。请把上面打叉的条目连同上下文一起发给我。
echo      注意：不要自己改测试文件让它通过。
echo.
pause
exit /b 1
