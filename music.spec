# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 —— 单文件 exe，双击直接出原生窗口。

    python -m PyInstaller music.spec --noconfirm --clean

产物：dist/XiaoyuMusic.exe

窗口由 pywebview 提供（WebView2 内核），所以打包环境必须是 **Python 3.12**：
pywebview 依赖的 pythonnet 3.x 只出到 3.12 为止，更高版本 pip 会退回需要现场
编译的 pythonnet 2.5.2，装不上。build_exe.bat 里已经固定用 3.12。

数据文件必须显式列在 datas 里。单文件模式启动时会把它们解压到一个临时目录，
``sys._MEIPASS`` 指向那里，由 ``paths.resource_root()`` 统一定位 —— 所以
``smtc.ps1`` 和 ``frontend/dist`` 少写一条，打出来的 exe 就会缺对应功能。

``webview`` 自带 PyInstaller hook（``webview/__pyinstaller``），会把 ``webview/lib``
下的 WebView2 程序集和 native loader 一起收进来；但只有当分析阶段真的看到了这个
import 才会触发，所以下面 hiddenimports 里显式列出来。

exe 名字用 ASCII 是为了避开打包日志和杀软对宽字符文件名的兼容问题；
想要中文名，直接重命名 dist 下那个 exe 即可（单文件 exe 可以随意改名）。
"""

from pathlib import Path

ROOT = Path(SPECPATH)

a = Analysis(
    ["main.py"],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        # smtc.py 靠 PowerShell 跑 Windows SMTC，缺了它整个捕获功能会哑掉。
        (str(ROOT / "smtc.ps1"), "."),
        # server.py 从这里读静态页面；不存在时只会吐一页「请先构建前端」的提示。
        (str(ROOT / "frontend" / "dist"), "frontend/dist"),
        # main.py 找 favicon.ico 用，同时也是 exe 图标来源。
        (str(ROOT / "frontend" / "public"), "frontend/public"),
    ],
    # main.py 里 `from server import run` 和 `import webview` 都写在函数内部，
    # 静态分析能跟到，这里再兜一层，避免打出来的 exe 到运行时才报 ImportError。
    hiddenimports=[
        "server",
        "smtc",
        "lyrics",
        "wshttp",
        "paths",
        # 窗口本体。winforms 提供窗口，edgechromium 提供 WebView2 渲染。
        "webview",
        "webview.platforms.winforms",
        "webview.platforms.edgechromium",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 都是这个项目用不到的重家伙。webview 不能出现在这里 —— 它就是窗口本身。
    excludes=["tkinter", "PIL", "numpy", "pandas", "matplotlib"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="XiaoyuMusic",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX 压得动，但很容易被杀软当木马误报，不划算。
    upx=False,
    runtime_tmpdir=None,
    # 出的是桌面窗口，不该再跟一个黑框。代价是 sys.stdout/stderr 变成 None，
    # main.py 的 ensure_console_streams() 把它们接到
    # %LOCALAPPDATA%\XiaoyuMusic\app.log —— 没有这一步，logging 的
    # StreamHandler 和 wshttp 的启动 print 都会静默失效。
    console=False,
    # 无控制台时崩溃默认什么都不显示，留个弹窗至少能看见 traceback。
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / "frontend" / "public" / "favicon.ico"),
)
