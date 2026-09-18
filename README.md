# 小雨音乐控制器 — Python + Vue 3

<img src="frontend/public/icon-512.png" width="96" alt="小雨音乐控制器图标">

原项目（Good Music）的 Python + Vue 3 版本。后端只用 **Python 标准库**，通过 PowerShell 调用 Windows SMTC 捕获正在播放的歌曲；前端是 Vue 3 + Three.js 粒子歌词。

> 当前机器上的 Python 是 **3.15 beta**，FastAPI / winrt / pyyaml 等包没有现成 wheel，`pip install` 会编译失败并让 `run.bat` 闪退。所以这个版本不再依赖这些包。

## 功能

与 C# / WebView2 版本对齐：

- 自动捕获系统正在播放的音乐（歌名、歌手、专辑封面）
- Three.js 三层粒子歌词 + 全屏背景粒子
- 玻璃态 UI、唱片旋转、播放控制、进度跳转
- 歌词来源：lrclib.net → 网易云 API 回退
- 主题色跟随专辑封面：从封面提取主色，驱动高亮色与粒子颜色（见 `frontend/src/theme.js`），
  默认金色（hue 38）在封面没有明确主色时兜底；歌词当前行最亮、上下行压暗

> 有些播放器（部分版本的网易云音乐）不会注册 Windows SMTC，此时自动改用播放器窗口标题兜底识别
> 「歌名 - 歌手」。兜底模式下没有 SMTC 封面，后端会额外走网易云 `song/detail` 取一张专辑封面
> （base64 内联下发给前端，避免 canvas 因跨域图片被污染而无法取色）；进度按检测到的时间起算，
> 总长取不到时先按歌词最后一行推算、再被网易云搜索结果里的真实时长覆盖（两者都没有才退回 4 分钟
> 兜底），界面会提示「仅同步显示」；播放/上一首/下一首改为发送系统全局媒体键，需要
> 播放器响应媒体键（网易云音乐默认响应）。暂停状态取自「播放器进程是否真的在输出声音」（查音频会话
> 状态），播放器响应媒体键后约半秒生效；点击播放/暂停时会先按下一次点击应得的状态显示，等探针确认
> 后交还给它，避免图标和进度条滞后。
>
> 兜底模式下拖动进度条是**真跳转**：向播放器窗口的浏览控件投递鼠标消息，点击它自己的进度条实现
> （网易云不注册 SMTC，其 Chromium 界面也读不到无障碍树，只能走点击）。窗口最小化时会先以不抢焦点
> 的方式临时显示、点完再缩回（约 0.7 秒，期间会闪现一下窗口）；窗口本身可见时无任何副作用，也不会
> 移动鼠标或抢焦点。该方式依赖窗口布局（进度条在窗口底部向上 83 像素处），换皮肤或大版本改版后可能
> 失效，失效时拖动退化为仅同步界面显示。

## 技术栈

- **前端**: Vue 3 + Vite + Three.js
- **后端**: Python 标准库 HTTP + WebSocket（`wshttp.py`）
- **系统 API**: Windows SMTC（`smtc.ps1` PowerShell WinRT）
- **窗口**: 默认系统浏览器（可选 pywebview）

## 环境

- Windows 10 / 11
- Python 3.11+（3.15 也可以，不需要额外 pip 包）
- Node.js 18+

## 运行

双击 `run.bat`，或：

```bash
cd python-vue
cd frontend
npm install
npm run build
cd ..
python main.py --browser
```

开发模式（热更新前端）：

```bash
# 终端 1 — API
python server.py

# 终端 2 — Vite
cd frontend
npm run dev
```

打开 `http://localhost:5173`。先在汽水 / 网易云 / QQ 音乐里放一首歌。

```bash
python main.py --browser      # 用系统浏览器
python main.py --no-window    # 只跑服务
python main.py --port 8765
```

## 目录

```
python-vue/
├── run.bat              # 一键启动（失败会 pause，不再闪退）
├── main.py              # 启动服务并打开浏览器
├── server.py            # HTTP + WebSocket
├── wshttp.py            # 标准库 HTTP/WebSocket
├── smtc.py              # SMTC 轮询（调用 PowerShell）
├── smtc.ps1             # Windows SMTC
├── lyrics.py            # lrclib + 网易云歌词、网易云封面、歌曲时长
├── tools/
│   └── make_icon.py     # 生成图标（纯标准库，见下）
└── frontend/
    ├── public/          # favicon.svg / favicon.ico / icon-512.png
    ├── src/App.vue
    ├── src/particles.js
    ├── src/theme.js     # 封面主色 → CSS 变量 / 粒子调色
    ├── src/lyrics.js
    └── src/socket.js
```

## 图标

`frontend/public/` 下的图标由 `tools/make_icon.py` 生成，改动后重新跑一次即可：

```bash
python tools/make_icon.py
```

用的是纯标准库手写的 PNG / ICO 编码器，不依赖 Pillow —— 这台机器上的 Python 3.15 beta
没有 Pillow 的现成 wheel，也没有 ImageMagick。脚本同时输出矢量 SVG 和位图 PNG/ICO，
两者共用同一套几何定义，不会各画各的。

16 / 32 像素用的是单独一版加粗字形：细字形的符干只有画布宽度的 2%，在 16px 下不到一个
像素，整个音符会糊成一块。小尺寸放大符头、去掉符尾，保住「音乐」这个意思。跑完脚本可以
看 `tools/icon_preview.png`（各尺寸放大 6 倍并排）检查小尺寸是否还认得出来。

## License

MIT
