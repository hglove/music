from __future__ import annotations

import argparse
import inspect
import threading
import time
import webbrowser
from pathlib import Path

ICON = Path(__file__).resolve().parent / "frontend" / "public" / "favicon.ico"


def run_server(host: str, port: int) -> None:
    import asyncio
    from server import run

    asyncio.run(run(host, port))


class WindowApi:
    def close(self) -> None:
        try:
            import webview

            for window in webview.windows:
                window.destroy()
        except Exception:
            pass


def open_webview(url: str) -> bool:
    try:
        import webview
    except Exception:
        return False
    webview.create_window(
        "小雨音乐控制器",
        url,
        width=1800,
        height=1100,
        frameless=True,
        easy_drag=False,
        js_api=WindowApi(),
        background_color="#f4f5f8",
    )
    # pywebview 只有部分后端认 icon，旧版本连这个关键字都没有。先查签名再决定传不传，
    # 否则一个不受支持的后端会把整个窗口带崩。
    start_kwargs = {}
    try:
        if ICON.is_file() and "icon" in inspect.signature(webview.start).parameters:
            start_kwargs["icon"] = str(ICON)
    except (TypeError, ValueError):
        pass
    webview.start(**start_kwargs)
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
