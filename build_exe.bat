@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"
title 打包 exe - 小雨音乐控制器

echo.
echo ========================================
echo   打包 小雨音乐控制器 为单文件 exe
echo ========================================
echo.

set "PYEXE="

echo [1/5] 查找合适的 Python...
rem pywebview 依赖的 pythonnet 3.x 只出到 3.12，更高版本 pip 只能拿到需要现场
rem 编译的源码包。所以先找 3.12；实在没有，才退回 PATH 上的 python 并检查上界。
py -3.12 -c "import sys" >nul 2>nul
if not errorlevel 1 (
  set "PYEXE=py -3.12"
  goto :python_ok
)
python -c "import sys; sys.exit(0 if sys.version_info[:2] <= (3, 12) else 1)" >nul 2>nul
if not errorlevel 1 (
  set "PYEXE=python"
  goto :python_ok
)
echo.
echo [错误] 没找到可用的 Python 3.12（或更低）。
echo        pywebview 依赖的 pythonnet 3.x 最高只支持 3.12，更高的版本装不上；
echo        装不上 pywebview，打出来的就不是桌面窗口而是浏览器窗口了。
echo.
echo        装一个 3.12 即可：
echo            winget install --id Python.Python.3.12 -e
goto :fail

:python_ok
for /f "delims=" %%v in ('%PYEXE% -c "import sys;print(sys.version.split()[0])"') do set PYVER=%%v
echo       Python %PYVER%  (%PYEXE%)

where node >nul 2>nul
if errorlevel 1 (
  echo [错误] 找不到 node。请先安装 Node.js。
  goto :fail
)

echo [2/5] 构建 Vue 前端...
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

if not exist "frontend\dist\index.html" (
  echo [错误] frontend\dist\index.html 不存在，前端没构建成功。
  goto :fail
)

echo [3/5] 检查 PyInstaller...
%PYEXE% -m PyInstaller --version >nul 2>nul
if errorlevel 1 (
  echo       没装，正在安装...
  %PYEXE% -m pip install --upgrade pyinstaller
  if errorlevel 1 (
    echo.
    echo [错误] PyInstaller 安装失败。
    goto :fail
  )
)
for /f "delims=" %%v in ('%PYEXE% -m PyInstaller --version') do set PIVER=%%v
echo       PyInstaller %PIVER%

echo [4/5] 检查 pywebview...
%PYEXE% -c "import webview" >nul 2>nul
if errorlevel 1 (
  echo       没装，正在安装...
  %PYEXE% -m pip install --upgrade pywebview
  if errorlevel 1 (
    echo.
    echo [错误] pywebview 安装失败 —— 它是窗口本体，装不上就只能开浏览器。
    goto :fail
  )
)

echo [5/5] 打包中，大约 1-2 分钟...
%PYEXE% -m PyInstaller music.spec --noconfirm --clean
if errorlevel 1 (
  echo [错误] 打包失败，原因见上方输出。
  goto :fail
)

echo.
echo ========================================
echo   完成
echo ========================================
echo.
for %%f in (dist\*.exe) do echo   产物: %%~ff  (%%~zf 字节)
echo.
echo 双击 dist\XiaoyuMusic.exe 即可运行，会直接弹出桌面窗口，
echo 没有控制台，也不经过浏览器。
echo.
echo 单文件 exe，拷到别的 Windows 电脑上直接双击就能跑，那台机器不用装
echo Python、Node 或任何本项目的依赖；只要求有 WebView2 运行时
echo （Win11 自带，Win10 一般随 Edge 装好了）。
echo.
pause
goto :eof

:fail
echo.
echo 打包中止。窗口不会关闭，方便查看上面的错误。
pause
exit /b 1
