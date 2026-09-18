@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title 小雨音乐控制器 - Python + Vue

echo.
echo ========================================
echo   小雨音乐控制器  (Python + Vue)
echo ========================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 找不到 python。请先安装 Python 3.11+ 并勾选 Add to PATH。
  goto :fail
)

where node >nul 2>nul
if errorlevel 1 (
  echo [错误] 找不到 node。请先安装 Node.js。
  goto :fail
)

if not exist "frontend\dist\index.html" (
  echo [1/2] 构建 Vue 前端...
  pushd frontend
  if not exist node_modules (
    call npm install
    if errorlevel 1 (
      popd
      echo [错误] npm install 失败。
      goto :fail
    )
  )
  call npm run build
  if errorlevel 1 (
    popd
    echo [错误] npm run build 失败。
    goto :fail
  )
  popd
) else (
  echo [1/2] 前端已构建，跳过。
)

echo [2/2] 启动服务  http://127.0.0.1:8765
echo 提示：先打开汽水/网易云/QQ音乐播放歌曲，再看这个窗口。
echo.

python main.py --browser
if errorlevel 1 goto :fail
echo.
echo 服务已结束。
pause
goto :eof

:fail
echo.
echo 启动失败，窗口不会关闭，方便查看上面的错误。
pause
exit /b 1
