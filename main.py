from __future__ import annotations

import argparse
import inspect
import os
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

from paths import resource_root

ICON = resource_root() / "frontend" / "public" / "favicon.ico"
LOG_DIR_NAME = "XiaoyuMusic"

WINDOW_TITLE = "小雨音乐控制器"
WINDOW_SIZE = (1000, 760)
# Below roughly this the album art, the lyric lines and the floating control bar
# start to overlap, so the window refuses to shrink any further.
WINDOW_MIN_SIZE = (620, 640)


def ensure_console_streams() -> None:
    """Point stdout/stderr at a log file when the build has no console.

    A windowed PyInstaller build has no console, so both streams are None.
    logging's StreamHandler would then be handed a None stream and wshttp's
    startup print would raise, which turns any launch failure into silence.
    Giving them a file keeps the window able to start and leaves a trace to
    read afterwards.
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    stream = None
    try:
        base = os.environ.get("LOCALAPPDATA") or tempfile.gettempdir()
        log_dir = Path(base) / LOG_DIR_NAME
        log_dir.mkdir(parents=True, exist_ok=True)
        stream = open(log_dir / "app.log", "a", encoding="utf-8", buffering=1)
    except OSError:
        stream = None
    if stream is None:
        stream = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def run_server(host: str, port: int) -> None:
    import asyncio
    from server import run

    asyncio.run(run(host, port))


def resize_hit_code(direction: str) -> int:
    """"se" -> HTBOTTOMRIGHT and so on, as Windows names window regions."""
    return {
        "n": 12,
        "s": 15,
        "e": 11,
        "w": 10,
        "nw": 13,
        "ne": 14,
        "sw": 16,
        "se": 17,
    }[direction]


class WindowApi:
    """Window commands the page is allowed to call.

    A frameless window gets no title bar from Windows, so minimising, maximising
    and dragging the edges to resize all have to be driven from the page.

    Both attributes must stay underscore-prefixed. pywebview builds the JS bridge
    by walking every public attribute of this object and recursing into anything
    that is not callable (util.inject_pywebview), so a bare `window` attribute
    sends it down through the whole native form and drowns the log in COM errors
    until the bridge is ready far too late to be useful.
    """

    def __init__(self) -> None:
        self._window = None
        self._hwnd = None
        self._form = None

    def _targets(self):
        if self._window is not None:
            return [self._window]
        try:
            import webview
        except Exception:
            return []
        return list(webview.windows)

    def close(self) -> None:
        for window in self._targets():
            window.destroy()

    def minimize(self) -> None:
        for window in self._targets():
            window.minimize()

    def toggle_maximize(self) -> bool:
        """Toggle maximise and report the state the window ended up in.

        The current state is read back from the native window rather than
        remembered here, so it stays right when the window was maximised by
        something other than this button.
        """
        maximized = _is_maximized(self._hwnd)
        for window in self._targets():
            if maximized:
                window.restore()
            else:
                window.maximize()
        return not maximized

    def start_resize(self, direction) -> None:
        """Hand an edge drag over to Windows.

        Tracking the pointer in the page and shipping every step over the js
        bridge does not hold up: the window lags behind the cursor, and the
        moment the cursor outruns the window the rest of the drag is dropped
        because those events go to whatever is under it instead. Faking a
        non-client left-button-down makes Windows run its own resize loop
        instead, which tracks the pointer exactly and enforces MinimumSize.

        This has to happen on the thread that owns the window. Mouse capture
        belongs to a thread, so ReleaseCapture from the bridge thread fails and
        the resize loop then finds the mouse still captured somewhere else and
        gives up. form.Invoke marshals over and blocks until the drag is over,
        so this method likewise returns only once the user let go.
        """
        if self._hwnd is None:
            return
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user32.SendMessageW.argtypes = [
            wintypes.HWND,
            ctypes.c_uint,
            ctypes.c_size_t,
            ctypes.c_ssize_t,
        ]
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        lparam = (point.y << 16) | (point.x & 0xFFFF)
        hit = resize_hit_code(direction)

        def enter_loop() -> None:
            user32.ReleaseCapture()
            user32.SendMessageW(self._hwnd, 0x00A1, hit, lparam)  # WM_NCLBUTTONDOWN

        form = self._form
        if form is None:
            enter_loop()
            return
        from System import Action

        form.Invoke(Action(enter_loop))


def _is_maximized(hwnd) -> bool:
    if hwnd is None:
        return False
    try:
        import ctypes

        return bool(ctypes.windll.user32.IsZoomed(hwnd))
    except Exception:
        return False


def configure_window(window, api: WindowApi) -> None:
    """Native fix-ups that only work once WinForms has a live window.

    Two things need the real form. pywebview sizes it while it still carries a
    normal frame and only then switches to a borderless one, so the frame it
    dropped comes off the window and the requested size has to be re-applied.
    And a borderless form maximises over the taskbar unless MaximizedBounds
    tells it where the work area is.
    """
    try:
        import clr

        clr.AddReference("System.Windows.Forms")
        from System import Action
        from System.Windows.Forms import Screen
    except Exception as exc:
        print(f"Window framing setup skipped ({exc}).")
        return

    form = window.native
    api._form = form
    api._hwnd = int(form.Handle.ToInt64())
    try:
        form.Invoke(
            Action(lambda: setattr(form, "MaximizedBounds", Screen.FromControl(form).WorkingArea))
        )
    except Exception as exc:
        print(f"MaximizedBounds setup skipped ({exc}).")
    window.resize(*WINDOW_SIZE)


def open_webview(url: str) -> bool:
    try:
        import webview
    except Exception:
        return False
    try:
        api = WindowApi()
        window = webview.create_window(
            WINDOW_TITLE,
            url,
            width=WINDOW_SIZE[0],
            height=WINDOW_SIZE[1],
            min_size=WINDOW_MIN_SIZE,
            frameless=True,
            easy_drag=False,
            js_api=api,
            background_color="#f4f5f8",
        )
        api._window = window
        window.events.shown += lambda: configure_window(window, api)
        # pywebview 只有部分后端认 icon，旧版本连这个关键字都没有。先查签名再决定传不传，
        # 否则一个不受支持的后端会把整个窗口带崩。
        start_kwargs = {}
        try:
            if ICON.is_file() and "icon" in inspect.signature(webview.start).parameters:
                start_kwargs["icon"] = str(ICON)
        except (TypeError, ValueError):
            pass
        webview.start(**start_kwargs)
    except Exception as exc:
        # Usually a missing WebView2 runtime. Fall back to the browser instead of
        # exiting silently, which is what a console-less build would otherwise do.
        print(f"Desktop window failed ({exc}); falling back to the browser.")
        return False
    return True


def wait_ready(url: str, seconds: float = 8.0) -> bool:
    import urllib.request

    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url + "/api/health", timeout=0.3)
            return True
        except Exception:
            time.sleep(0.15)
    return False


def main() -> None:
    ensure_console_streams()
    parser = argparse.ArgumentParser(description="小雨音乐控制器 (Python + Vue 3)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--browser", action="store_true", help="Open the system browser instead of a desktop window")
    parser.add_argument("--no-window", action="store_true", help="API/server only, do not open a UI")
    args = parser.parse_args()

    url = f"http://{args.host}:{args.port}"
    thread = threading.Thread(target=run_server, args=(args.host, args.port), daemon=True)
    thread.start()
    wait_ready(url)

    if args.no_window:
        thread.join()
        return
    if args.browser or not open_webview(url):
        print(f"Opening browser: {url}")
        webbrowser.open(url)
        thread.join()
        return


if __name__ == "__main__":
    main()
