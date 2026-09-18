from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from lyrics import LyricsFetcher, estimate_duration
from paths import resource_root
from smtc import SmtcMonitor
from wshttp import WebSocket, serve

log = logging.getLogger("music")
logging.basicConfig(
    level=logging.INFO,
    format="[MusicWidget %(asctime)s] %(message)s",
    datefmt="%H:%M:%S",
)

DIST_DIR = resource_root() / "frontend" / "dist"

FALLBACK_HTML = """<!DOCTYPE html><html><head><meta charset='UTF-8'><style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#060614;color:#fff;font-family:'Segoe UI',sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;flex-direction:column;gap:20px}
h1{font-size:32px;font-weight:300;background:linear-gradient(135deg,#ffb832,#ff6b35);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
p{color:#888;font-size:14px}
code{color:#f0a830}
</style></head><body>
<h1>小雨音乐控制器</h1>
<p>请先构建 Vue 前端：<code>cd frontend && npm install && npm run build</code></p>
</body></html>"""


class Hub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.lyrics = LyricsFetcher()
        self.smtc = SmtcMonitor(self.emit)
        self._lyric_task: asyncio.Task | None = None
        self._cover_task: asyncio.Task | None = None
        self._duration_task: asyncio.Task | None = None
        self._last: dict[str, dict[str, Any]] = {}

    async def start(self) -> None:
        await self.smtc.start()

    async def stop(self) -> None:
        await self.smtc.stop()
        await self.lyrics.aclose()
        for task in (self._lyric_task, self._cover_task, self._duration_task):
            if task and not task.done():
                task.cancel()

    async def register(self, ws: WebSocket) -> None:
        self.clients.add(ws)
        for key in ("status", "music", "playback", "position", "lyrics", "lyrics-status", "cover"):
            event = self._last.get(key)
            if event:
                try:
                    await ws.send_text(json.dumps(event, ensure_ascii=False))
                except Exception:
                    self.unregister(ws)
                    return

    def unregister(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def emit(self, event: dict[str, Any]) -> None:
        if event.get("type") == "fetch-lyrics":
            title, artist = event.get("title", ""), event.get("artist", "")
            for task in (self._lyric_task, self._cover_task, self._duration_task):
                if task and not task.done():
                    task.cancel()
            self._lyric_task = asyncio.create_task(self._fetch_lyrics(title, artist))
            self._cover_task = asyncio.create_task(self._fetch_cover(title, artist))
            self._duration_task = asyncio.create_task(self._fetch_duration(title, artist))
            return
        await self.broadcast(event)

    async def _fetch_duration(self, title: str, artist: str) -> None:
        # Carries the real length to the monitor for players that report none. No event of
        # its own: the value rides along on the next "position", which always follows.
        try:
            seconds = await self.lyrics.fetch_duration(title, artist)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Duration ERROR: %s", exc)
            return
        if seconds:
            self.smtc.set_external_duration(seconds, "netease")

    async def _fetch_cover(self, title: str, artist: str) -> None:
        # The player already handed us its own artwork, so there is nothing to look up.
        if (self._last.get("music") or {}).get("thumbnail"):
            return
        try:
            cover = await self.lyrics.fetch_cover(title, artist)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.warning("Cover ERROR: %s", exc)
            return
        if cover:
            await self.broadcast({"type": "cover", "url": cover})

    async def _fetch_lyrics(self, title: str, artist: str) -> None:
        try:
            result = await self.lyrics.fetch(title, artist)
        except asyncio.CancelledError:
            raise
        if result:
            source, kind = result
            log.info("Lyrics: OK (%s, %s chars)", kind, len(source))
            if kind == "synced":
                # Lands before the duration lookup often enough to matter, and outranks
                # nothing — the lookup overwrites it as soon as it answers.
                estimate = estimate_duration(source)
                if estimate:
                    self.smtc.set_external_duration(estimate, "lyrics")
            await self.broadcast({"type": "lyrics", "source": source, "kind": kind})
        else:
            await self.broadcast({"type": "lyrics-status", "status": "no-lyrics"})

    async def broadcast(self, event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind:
            self._last[kind] = event
            if kind == "music":
                self._last.pop("lyrics", None)
                self._last.pop("lyrics-status", None)
                self._last.pop("cover", None)
        if not self.clients:
            return
        payload = json.dumps(event, ensure_ascii=False)
        dead: list[WebSocket] = []
        for ws in list(self.clients):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.unregister(ws)

    async def handle_command(self, raw: str) -> None:
        msg = raw.strip()
        if msg.startswith("{") and msg.endswith("}"):
            try:
                data = json.loads(msg)
                msg = str(data.get("action") or data.get("type") or "")
                if msg == "seek":
                    await self.smtc.control("seek", float(data.get("seconds", 0)))
                    return
            except Exception:
                return
        if msg in {"prev", "next", "playpause"}:
            await self.smtc.control(msg)
        elif msg.startswith("seek:"):
            try:
                await self.smtc.control("seek", float(msg[5:]))
            except ValueError:
                pass
        elif msg == "close":
            log.info("Close requested from UI")


hub = Hub()


async def websocket_handler(ws: WebSocket) -> None:
    await hub.register(ws)
    try:
        async for data in ws:
            await hub.handle_command(data)
    finally:
        hub.unregister(ws)


async def http_handler(method: str, path: str, _headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
    if path == "/api/health":
        body = b'{"status":"ok"}'
        return 200, {"Content-Type": "application/json; charset=utf-8"}, body

    if not DIST_DIR.exists():
        return 200, {"Content-Type": "text/html; charset=utf-8"}, FALLBACK_HTML.encode("utf-8")

    rel = path.lstrip("/")
    target = (DIST_DIR / rel).resolve() if rel else DIST_DIR / "index.html"
    if rel and str(target).startswith(str(DIST_DIR.resolve())) and target.is_file():
        return 200, {"Content-Type": _mime(target)}, target.read_bytes()
    index = DIST_DIR / "index.html"
    return 200, {"Content-Type": "text/html; charset=utf-8"}, index.read_bytes()


def _mime(path: Path) -> str:
    return {
        ".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".ico": "image/x-icon",
        ".woff2": "font/woff2",
    }.get(path.suffix.lower(), "application/octet-stream")


async def run(host: str = "127.0.0.1", port: int = 8765) -> None:
    await hub.start()
    try:
        await serve(host, port, http_handler, websocket_handler)
    finally:
        await hub.stop()


if __name__ == "__main__":
    asyncio.run(run())
