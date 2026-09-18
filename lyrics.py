from __future__ import annotations

import base64
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

log = logging.getLogger("music.lyrics")

HEADERS = {
    "User-Agent": "XiaoyuMusicController/1.0 (https://github.com/Danchuna/Simple_music)",
    "Accept": "application/json",
}

# 网易云 rejects bare API calls that do not look like they came from its own site.
NET_EXTRA_HEADERS = {"Referer": "https://music.163.com/"}

# A length outside this range is a wrong-track hit rather than a real song, so it is
# dropped instead of being shown as fact.
MIN_TRACK_SECONDS = 30.0
MAX_TRACK_SECONDS = 30 * 60.0

_LRC_LENGTH = re.compile(r"^\[length:\s*(\d+):(\d+(?:\.\d+)?)\s*\]", re.IGNORECASE | re.MULTILINE)
_LRC_LINE = re.compile(r"^\[\s*(\d+):(\d+(?:\.\d+)?)\s*\]\s*(.*)$", re.MULTILINE)

# How much song is assumed to follow the start of the last lyric line. Netease LRCs
# usually stop at the final line rather than marking the end, so the raw timestamp
# lands a few seconds short of the real length.
_TAIL_SECONDS = 3.0


def estimate_duration(lrc: str) -> Optional[float]:
    """Approximate track length, in seconds, from a synced lyric.

    Only used when nothing better is available: an `[length:]` tag gives the exact figure,
    otherwise the last timestamped line is taken as the end.
    """
    if not lrc:
        return None
    tag = _LRC_LENGTH.search(lrc)
    if tag:
        return int(tag.group(1)) * 60 + float(tag.group(2))
    last = None
    for match in _LRC_LINE.finditer(lrc):
        last = match
    if last is None:
        return None
    seconds = int(last.group(1)) * 60 + float(last.group(2))
    # A final timestamp with no words after it marks the end of the track, so it is
    # already the length; only a last *line* needs the tail allowance.
    if last.group(3).strip():
        seconds += _TAIL_SECONDS
    return seconds


def _pick_lrclib_source(data: dict) -> Optional[tuple[str, str]]:
    syn = data.get("syncedLyrics")
    plain = data.get("plainLyrics")
    if isinstance(syn, str) and syn.strip():
        return syn, "synced"
    if isinstance(plain, str) and plain.strip():
        return plain, "plain"
    return None


def _http_get(url: str, extra_headers: dict | None = None, timeout: float = 8.0) -> str:
    headers = dict(HEADERS)
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return ""
        raise


def _cover_data_uri(url: str) -> str:
    """Inline an album cover as a data URI.

    The page samples the cover to pick its theme colour, and drawing a cross-origin image
    onto a canvas taints it, so the bytes are fetched here instead of linking the URL.
    """
    if not url:
        return ""
    sep = "&" if "?" in url else "?"
    try:
        req = urllib.request.Request(f"{url}{sep}param=300y300", headers=HEADERS)
        with urllib.request.urlopen(req, timeout=8) as resp:
            raw = resp.read()
    except Exception as exc:
        log.info("Cover: download failed (%s)", exc)
        return ""
    if not raw or len(raw) > 2000000:
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")


class LyricsFetcher:
    async def aclose(self) -> None:
        return None

    async def fetch(self, title: str, artist: str) -> Optional[tuple[str, str]]:
        import asyncio

        return await asyncio.to_thread(self._fetch_sync, title, artist)

    async def fetch_cover(self, title: str, artist: str) -> str:
        import asyncio

        return await asyncio.to_thread(self._cover_sync, title, artist)

    def _cover_sync(self, title: str, artist: str) -> str:
        """Album art for the track, as a data URI ("" when there is none).

        网易云 is the only usable source: a player that publishes no SMTC session also
        hands us no artwork, and its window title carries none.
        """
        try:
            song_id = self._netease_song_id(title, artist)
            if song_id is None:
                return ""
            return self._netease_cover(song_id, NET_EXTRA_HEADERS)
        except Exception as exc:
            log.info("Cover: lookup failed (%s)", exc)
            return ""

    def _fetch_sync(self, title: str, artist: str) -> Optional[tuple[str, str]]:
        for name, fn in (
            ("lrclib-get", self._lrclib_get),
            ("lrclib-search", self._lrclib_search),
            ("netease", self._netease),
        ):
            try:
                result = fn(title, artist)
                if result:
                    return result
            except Exception as exc:
                log.warning("Lyrics[%s] ERROR: %s", name, exc)
        log.info("Lyrics: all sources failed")
        return None

    def _lrclib_get(self, title: str, artist: str) -> Optional[tuple[str, str]]:
        log.info("Lyrics[lrclib-get]: %s - %s", artist, title)
        qs = urllib.parse.urlencode({"track_name": title, "artist_name": artist})
        raw = _http_get(f"https://lrclib.net/api/get?{qs}")
        if not raw:
            return None
        picked = _pick_lrclib_source(json.loads(raw))
        if not picked:
            log.info("Lyrics: empty result")
        return picked

    def _lrclib_search(self, title: str, artist: str) -> Optional[tuple[str, str]]:
        log.info("Lyrics[lrclib-search]: %s %s", title, artist)
        qs = urllib.parse.urlencode({"q": f"{title} {artist}"})
        raw = _http_get(f"https://lrclib.net/api/search?{qs}")
        if not raw:
            return None
        items = json.loads(raw)
        if not isinstance(items, list) or not items:
            log.info("Lyrics[lrclib-search]: no results")
            return None

        best = None
        best_score = -1
        for item in items:
            t = item.get("trackName")
            if not t:
                continue
            score = 100 if str(t).lower() == title.lower() else 0
            if score > best_score:
                best_score = score
                best = item
        if best is None:
            return None

        lyric_id = best.get("id")
        log.info("Lyrics[lrclib-search]: found id=%s", lyric_id)
        get_raw = _http_get(f"https://lrclib.net/api/get/{lyric_id}")
        if not get_raw:
            return None
        return _pick_lrclib_source(json.loads(get_raw))

    def _netease_search_song(self, title: str, artist: str) -> Optional[dict]:
        log.info("Lyrics[netease-search]: %s %s", title, artist)
        qs = urllib.parse.urlencode({"s": f"{title} {artist}", "type": 1, "limit": 5})
        raw = _http_get(f"https://music.163.com/api/search/get?{qs}", NET_EXTRA_HEADERS)
        payload = json.loads(raw) if raw else {}
        if payload.get("code") != 200:
            log.info("Lyrics[netease-search]: code=%s", payload.get("code"))
            return None
        songs = (payload.get("result") or {}).get("songs") or []
        if not songs:
            log.info("Lyrics[netease-search]: no songs")
            return None
        return songs[0]

    def _netease_song_id(self, title: str, artist: str) -> Optional[int]:
        song = self._netease_search_song(title, artist)
        return song.get("id") if song else None

    async def fetch_duration(self, title: str, artist: str) -> Optional[float]:
        import asyncio

        return await asyncio.to_thread(self._duration_sync, title, artist)

    def _duration_sync(self, title: str, artist: str) -> Optional[float]:
        """Track length in seconds, or None when no source can name it.

        Netease is the only source asked, and it costs no extra request of its own: the
        same search already backs the lyric and cover lookups. This is what fills the gap
        for a player that publishes no SMTC timeline, where the length used to be assumed.
        """
        try:
            song = self._netease_search_song(title, artist)
            ms = song.get("duration") if song else 0
            seconds = float(ms) / 1000.0
        except Exception as exc:
            log.info("Duration[netease]: lookup failed (%s)", exc)
            return None
        if not MIN_TRACK_SECONDS <= seconds <= MAX_TRACK_SECONDS:
            log.info("Duration[netease]: implausible (%.1fs), ignoring", seconds)
            return None
        log.info("Duration[netease]: %.1fs", seconds)
        return seconds

    def _netease(self, title: str, artist: str) -> Optional[tuple[str, str]]:
        song_id = self._netease_song_id(title, artist)
        if song_id is None:
            return None
        log.info("Lyrics[netease]: found song id=%s", song_id)
        lqs = urllib.parse.urlencode({"id": song_id, "lv": 1, "kv": 1, "tv": -1})
        ly_raw = _http_get(f"https://music.163.com/api/song/lyric?{lqs}", NET_EXTRA_HEADERS)
        lyric_payload = json.loads(ly_raw) if ly_raw else {}
        if lyric_payload.get("code") != 200:
            log.info("Lyrics[netease-lyric]: code=%s", lyric_payload.get("code"))
            return None
        lrc = ((lyric_payload.get("lrc") or {}).get("lyric") or "").strip()
        if not lrc:
            log.info("Lyrics[netease]: empty lrc")
            return None
        log.info("Lyrics[netease]: OK (%s chars)", len(lrc))
        return lrc, "synced"

    def _netease_cover(self, song_id: int, extra: dict) -> str:
        try:
            raw = _http_get(f"https://music.163.com/api/song/detail?ids=[{song_id}]", extra)
            songs = (json.loads(raw).get("songs") or []) if raw else []
            pic_url = ((songs[0].get("album") or {}).get("picUrl") or "") if songs else ""
        except Exception as exc:
            log.info("Cover: detail failed (%s)", exc)
            return ""
        cover = _cover_data_uri(pic_url)
        log.info("Cover: %s", f"{len(cover)} bytes (b64)" if cover else "none")
        return cover
