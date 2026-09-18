from __future__ import annotations

import asyncio
import base64
import hashlib
from collections.abc import AsyncIterator, Awaitable, Callable

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
HttpHandler = Callable[[str, str, dict[str, str]], Awaitable[tuple[int, dict[str, str], bytes]]]
WsHandler = Callable[["WebSocket"], Awaitable[None]]

REASON = {
    200: "OK",
    400: "Bad Request",
    404: "Not Found",
    500: "Internal Server Error",
}


class WebSocket:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._closed = False

    def __aiter__(self) -> AsyncIterator[str]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[str]:
        while not self._closed:
            msg = await self.recv()
            if msg is None:
                return
            yield msg

    async def send_text(self, text: str) -> None:
        payload = text.encode("utf-8")
        await self._send_frame(0x1, payload)

    async def recv(self) -> str | None:
        while True:
            opcode, payload, closed = await self._read_frame()
            if closed:
                self._closed = True
                return None
            if opcode == 0x8:
                self._closed = True
                try:
                    await self._send_frame(0x8, b"")
                except Exception:
                    pass
                return None
            if opcode == 0x9:
                await self._send_frame(0xA, payload)
                continue
            if opcode == 0x1:
                return payload.decode("utf-8", errors="replace")
            if opcode == 0x2:
                return payload.decode("utf-8", errors="replace")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._send_frame(0x8, b"")
        except Exception:
            pass
        try:
            self._writer.close()
            await self._writer.wait_closed()
        except Exception:
            pass

    async def _send_frame(self, opcode: int, payload: bytes) -> None:
        header = bytearray()
        header.append(0x80 | (opcode & 0x0F))
        n = len(payload)
        if n < 126:
            header.append(n)
        elif n < 65536:
            header.append(126)
            header.extend(n.to_bytes(2, "big"))
        else:
            header.append(127)
            header.extend(n.to_bytes(8, "big"))
        self._writer.write(header + payload)
        await self._writer.drain()

    async def _read_frame(self) -> tuple[int, bytes, bool]:
        hdr = await self._reader.readexactly(2)
        opcode = hdr[0] & 0x0F
        masked = bool(hdr[1] & 0x80)
        length = hdr[1] & 0x7F
        if length == 126:
            length = int.from_bytes(await self._reader.readexactly(2), "big")
        elif length == 127:
            length = int.from_bytes(await self._reader.readexactly(8), "big")
        mask = await self._reader.readexactly(4) if masked else b""
        data = await self._reader.readexactly(length) if length else b""
        if masked:
            data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        return opcode, data, False


async def serve(host: str, port: int, http_handler: HttpHandler, ws_handler: WsHandler) -> None:
    server = await asyncio.start_server(
        lambda r, w: _handle(r, w, http_handler, ws_handler),
        host,
        port,
    )
    print(f"[MusicWidget] http://{host}:{port}")
    async with server:
        await server.serve_forever()


async def _handle(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    http_handler: HttpHandler,
    ws_handler: WsHandler,
) -> None:
    try:
        raw = await _read_headers(reader)
        if not raw:
            writer.close()
            return
        request_line, headers = _parse_headers(raw)
        parts = request_line.split()
        method = parts[0] if parts else "GET"
        path = parts[1] if len(parts) > 1 else "/"
        path = path.split("?", 1)[0]

        if headers.get("upgrade", "").lower() == "websocket":
            await _upgrade(writer, headers)
            ws = WebSocket(reader, writer)
            try:
                await ws_handler(ws)
            finally:
                await ws.close()
            return

        try:
            status, resp_headers, body = await http_handler(method, path, headers)
        except Exception as exc:
            status, resp_headers, body = 500, {"Content-Type": "text/plain"}, str(exc).encode()
        await _write_http(writer, status, resp_headers, body)
    except (asyncio.IncompleteReadError, ConnectionResetError, BrokenPipeError):
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def _read_headers(reader: asyncio.StreamReader) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        line = await reader.readline()
        if not line:
            break
        if line == b"\r\n":
            break
        chunks.append(line)
        total += len(line)
        if total > 65536:
            break
    return b"".join(chunks)


def _parse_headers(raw: bytes) -> tuple[str, dict[str, str]]:
    text = raw.decode("iso-8859-1", errors="replace")
    lines = text.split("\r\n")
    request_line = lines[0] if lines else ""
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        headers[k.strip().lower()] = v.strip()
    return request_line, headers


async def _upgrade(writer: asyncio.StreamWriter, headers: dict[str, str]) -> None:
    key = headers.get("sec-websocket-key", "")
    accept = base64.b64encode(hashlib.sha1((key + GUID).encode("ascii")).digest()).decode("ascii")
    resp = (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept}\r\n"
        "\r\n"
    )
    writer.write(resp.encode("ascii"))
    await writer.drain()


async def _write_http(writer: asyncio.StreamWriter, status: int, headers: dict[str, str], body: bytes) -> None:
    reason = REASON.get(status, "OK")
    headers = dict(headers)
    headers.setdefault("Content-Length", str(len(body)))
    headers.setdefault("Connection", "close")
    head = [f"HTTP/1.1 {status} {reason}"]
    for k, v in headers.items():
        head.append(f"{k}: {v}")
    writer.write(("\r\n".join(head) + "\r\n\r\n").encode("ascii") + body)
    await writer.drain()
