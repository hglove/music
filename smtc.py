from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

from paths import resource_root

log = logging.getLogger("music.smtc")

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]
PS_SCRIPT = resource_root() / "smtc.ps1"
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _ps_cmd(action: str, seconds: float | None = None, duration: float | None = None) -> list[str]:
    cmd = [
        "powershell.exe",
        "-NoProfile",
        "-STA",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(PS_SCRIPT),
        "-Action",
        action,
    ]
    if seconds is not None:
        cmd += ["-Seconds", str(seconds)]
    if duration is not None:
        cmd += ["-Duration", str(duration)]
    return cmd


def _status_name(status: Any) -> str:
    name = str(status or "").split(".")[-1].lower()
    if name in {"playing", "paused", "stopped"}:
        return name
    return "unknown"


class SmtcMonitor:
    """Poll Windows SMTC via PowerShell (no native WinRT wheels required)."""

    # Which source wins when several offer a track length. A lyric-derived estimate is a
    # guess at the tail of the last line; a lookup names the actual track, so it outranks
    # the guess regardless of which request happens to land first.
    _DURATION_RANK = {"lyrics": 1, "netease": 2}
    # Span assumed while nothing has reported a length yet.
    _DURATION_DEFAULT = 240.0

    def __init__(self, emit: EventHandler) -> None:
        self._emit = emit
        self._proc: Optional[asyncio.subprocess.Process] = None
        self._task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None
        self._running = False

        self._last_title = ""
        self._last_artist = ""
        self._last_album = ""
        self._last_thumb = ""
        self._last_status = ""
        self._last_position = -1.0
        self._last_duration = -1.0
        self._last_session_id: Optional[str] = None

        self._sim_start_wall = datetime.now(timezone.utc)
        self._sim_start_pos = 0.0
        self._is_playing = False
        # Track length for players that report none. The SMTC timeline is authoritative
        # whenever it exists, so this only ever fills the window-title fallback, where a
        # hardcoded span used to put every song at exactly 4 minutes.
        self._external_duration = 0.0
        self._external_rank = 0
        # Window-title fallback (网易云音乐): the snapshot status comes from probing whether
        # the process is really rendering audio, which is trustworthy but only reacts after
        # the player has handled a media key. So a play/pause click records the status it
        # expects and holds it until the probe agrees (or the window expires) — without this
        # the button and the estimated position would lag by a poll interval.
        self._is_fallback = False
        self._pending_status = ""
        self._pending_until = 0.0

    async def start(self) -> None:
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *_ps_cmd("poll"),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
        except Exception as exc:
            log.error("SMTC request failed: %s", exc)
            await self._emit({"type": "status", "message": "SMTC 不可用"})
            return
        self._running = True
        self._task = asyncio.create_task(self._read_loop(), name="smtc-poll")
        self._stderr_task = asyncio.create_task(self._drain_stderr(), name="smtc-stderr")

    async def stop(self) -> None:
        self._running = False
        proc = self._proc
        if proc is not None and proc.returncode is None:
            try:
                proc.kill()
            except Exception:
                pass
        # Let the reader tasks reach EOF instead of cancelling them. Cancelling a
        # pending read leaves the pipe transports unclosed and the child unreaped,
        # which surfaces as ResourceWarning noise on interpreter shutdown.
        for task in (self._task, self._stderr_task):
            if task is None:
                continue
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                log.error("SMTC monitor task failed: %s", exc)
        self._task = None
        self._stderr_task = None
        if proc is not None:
            try:
                await proc.wait()
            except Exception:
                pass
            self._proc = None

    def set_external_duration(self, seconds: float, source: str) -> None:
        """Supply a track length for a player whose SMTC timeline gave none."""
        rank = self._DURATION_RANK.get(source, 0)
        if rank == 0 or seconds <= 0 or rank < self._external_rank:
            return
        if rank == self._external_rank and abs(seconds - self._external_duration) < 0.01:
            return
        log.info("Duration: %.0fs (%s)", seconds, source)
        self._external_duration = seconds
        self._external_rank = rank
        # The next poll publishes the new span; without this the change would not surface
        # until the position drifted half a second past the last one sent.
        self._last_position = -1.0

    def _duration(self) -> float:
        if self._last_duration > 0:
            return self._last_duration
        if self._external_duration > 0:
            return self._external_duration
        return self._DURATION_DEFAULT

    async def control(self, action: str, seconds: float | None = None) -> None:
        duration: float | None = None
        if action == "seek" and seconds is not None:
            self._sim_start_pos = seconds
            self._sim_start_wall = datetime.now(timezone.utc)
            self._last_position = -1
            # The PowerShell side turns the target position into a ratio, because the
            # window-title fallback has to click the progress bar of the player instead
            # of asking a session to jump.
            duration = self._duration()
            log.info("Seek: %.1fs (simulated clock synced)", seconds)
        if action == "playpause" and self._is_fallback:
            # The media key is fire-and-forget, and the player is the authority on what
            # happens next only once it has acted. Expect the opposite of the state we last
            # saw — deriving it from the toggle itself would drift out of phase with the
            # player as soon as one click lands while it is already in the target state.
            self._pending_status = "paused" if self._is_playing else "playing"
            self._pending_until = time.monotonic() + 3.0
            log.info("Fallback playpause: expecting %s", self._pending_status)
        try:
            proc = await asyncio.create_subprocess_exec(
                *_ps_cmd(action, seconds, duration),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
            try:
                await asyncio.wait_for(proc.wait(), timeout=8)
            except asyncio.TimeoutError:
                proc.kill()
        except Exception as exc:
            log.warning("Media control '%s' failed: %s", action, exc)

    async def _drain_stderr(self) -> None:
        if not self._proc or not self._proc.stderr:
            return
        while True:
            line = await self._proc.stderr.readline()
            if not line:
                return
            text = line.decode("utf-8", errors="replace").strip()
            if text:
                log.debug("PS: %s", text)

    async def _read_loop(self) -> None:
        assert self._proc and self._proc.stdout
        while True:
            line = await self._proc.stdout.readline()
            if not line:
                if self._running:
                    log.error("SMTC PowerShell exited")
                    await self._emit({"type": "status", "message": "SMTC 不可用"})
                return
            text = line.decode("utf-8", errors="replace").strip()
            if not text or not text.startswith("{"):
                continue
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                continue
            if data.get("error"):
                await self._emit({"type": "status", "message": "SMTC 不可用"})
                continue
            try:
                await self._apply_snapshot(data)
            except Exception as exc:
                log.error("Poll ERROR: %s", exc)

    async def _apply_snapshot(self, data: dict[str, Any]) -> None:
        new_id = data.get("session") or ""
        if new_id != self._last_session_id:
            log.info("Session: %s", "NULL (no app playing)" if not new_id else f"'{new_id}' (NEW)")
            self._last_session_id = new_id
            self._last_title = self._last_artist = self._last_album = self._last_thumb = self._last_status = ""
            self._last_position = self._last_duration = -1
            self._external_duration = 0.0
            self._external_rank = 0
            self._is_playing = False
            self._pending_status = ""
            self._sim_start_pos = 0
            self._sim_start_wall = datetime.now(timezone.utc)
            if not new_id:
                await self._emit({"type": "status", "message": "未检测到正在播放的媒体"})

        if not new_id:
            return

        title = data.get("title") or ""
        artist = data.get("artist") or ""
        album = data.get("album") or ""
        self._is_fallback = bool(data.get("fallback"))
        if title != self._last_title or artist != self._last_artist or album != self._last_album:
            self._sim_start_pos = 0
            self._sim_start_wall = datetime.now(timezone.utc)
            self._last_position = self._last_duration = -1
            self._external_duration = 0.0
            self._external_rank = 0
            if "thumb" in data:
                self._last_thumb = data.get("thumb") or ""
            log.info(
                "Media: Title='%s' | Artist='%s' | Album='%s' | Thumb=%s",
                title, artist, album, "YES" if self._last_thumb else "NO",
            )
            self._last_title, self._last_artist, self._last_album = title, artist, album
            await self._emit(
                {
                    "type": "music",
                    "title": title,
                    "artist": artist,
                    "album": album,
                    "thumbnail": self._last_thumb,
                    "fallback": self._is_fallback,
                }
            )
            if title.strip() and artist.strip():
                await self._emit({"type": "fetch-lyrics", "title": title, "artist": artist})

        status = _status_name(data.get("status"))
        if self._pending_status:
            if status == self._pending_status or time.monotonic() >= self._pending_until:
                self._pending_status = ""   # the player caught up, or gave up waiting
            else:
                status = self._pending_status
        was_playing = self._is_playing
        now_playing = status == "playing"
        if was_playing and not now_playing:
            elapsed = (datetime.now(timezone.utc) - self._sim_start_wall).total_seconds()
            self._sim_start_pos = self._sim_start_pos + elapsed
        elif not was_playing and now_playing:
            self._sim_start_wall = datetime.now(timezone.utc)
        self._is_playing = now_playing
        if status != self._last_status:
            self._last_status = status
            log.info("Playback: Status=%s", status)
            await self._emit({"type": "playback", "status": status})

        dur_raw = data.get("duration") or 0
        try:
            dur_val = float(dur_raw)
        except (TypeError, ValueError):
            dur_val = 0.0
        if dur_val > 0:
            self._last_duration = dur_val

        dur = self._duration()
        if self._is_playing:
            pos = self._sim_start_pos + (datetime.now(timezone.utc) - self._sim_start_wall).total_seconds()
            pos %= dur
        else:
            pos = self._sim_start_pos
        if abs(pos - self._last_position) < 0.5:
            return
        if int(pos / 10) != int(self._last_position / 10):
            log.info("Position: %.1fs / %.0fs (simulated, playing=%s)", pos, dur, self._is_playing)
        self._last_position = pos
        await self._emit({
            "type": "position",
            "pos": round(pos, 2),
            "dur": round(dur, 2),
            "fallback": self._is_fallback,
        })
