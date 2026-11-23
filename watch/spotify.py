"""Read public Spotify metadata without audio or account-session access."""

import json
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

import httpx


def playlist_id(value: str) -> str:
        """Validate a public playlist URL; discard sharing parameters."""
        url = urlsplit(value.strip())
        match = re.fullmatch(r"/playlist/([A-Za-z0-9]{22})/?", url.path)
        if (
                url.scheme != "https"
                or url.netloc != "open.spotify.com"
                or match is None
        ):
                msg = "Use a public Spotify playlist URL."
                raise ValueError(msg)
        return match[1]


# Spotify's public embed carries structured metadata. Never inspect web-player
# session tokens or audio preview URLs from the surrounding page.


class _Page(HTMLParser):
        def __init__(self) -> None:
                super().__init__()
                self.reading = False
                self.data: list[str] = []
                self.album = ""

        def handle_starttag(
                self, tag: str, attrs: list[tuple[str, str | None]]
        ) -> None:
                values = dict(attrs)
                if tag == "script" and values.get("id") == "__NEXT_DATA__":
                        self.reading = True
                if tag == "meta" and values.get("name") == "music:album":
                        self.album = values.get("content") or ""

        def handle_endtag(self, tag: str) -> None:
                if tag == "script":
                        self.reading = False

        def handle_data(self, data: str) -> None:
                if self.reading:
                        self.data.append(data)


def _entity(page: str) -> dict[str, Any]:
        parser = _Page()
        parser.feed(page)
        try:
                return json.loads("".join(parser.data))["props"]["pageProps"][
                        "state"
                ]["data"]["entity"]
        except (ValueError, KeyError, TypeError):
                msg = "No readable public Spotify metadata was returned."
                raise ValueError(msg) from None


def parse_playlist(page: str) -> dict[str, Any]:
        """Keep source order/duplicates and disclose the public embed limit."""
        entity = _entity(page)
        if not entity.get("trackList"):
                msg = "This public playlist is empty, private or unavailable."
                raise ValueError(msg)
        tracks = []
        for track in entity["trackList"]:
                match = re.fullmatch(
                        r"spotify:track:([A-Za-z0-9]{22})",
                        str(track.get("uri", "")),
                )
                tracks.append(
                        {
                                "id": match[1] if match else None,
                                "title": str(track.get("title", "Unavailable")),
                                "artist": str(track.get("subtitle", "")),
                                "duration": track.get("duration", 0),
                        }
                )
        return {
                "id": str(entity.get("id", "")),
                "name": str(entity.get("name", "Spotify playlist"))[:200],
                "tracks": tracks,
                "partial": len(tracks) >= 100,
        }


def track_album(page: str) -> str:
        """Read the track's explicit album, ignoring other links."""
        parser = _Page()
        parser.feed(page)
        match = re.fullmatch(
                r"https://open.spotify.com/album/([A-Za-z0-9]{22})",
                parser.album,
        )
        if not match:
                msg = "The public track page does not identify its album."
                raise ValueError(msg)
        return match[1]


def parse_album(page: str) -> dict[str, str]:
        """Read an album's title and credited artist."""
        entity = _entity(page)
        return {"title": entity["name"], "artist": entity["subtitle"]}


class SpotifyPublic:
        """Fetch bounded public metadata from fixed HTTPS routes only."""

        def __init__(self, client: httpx.AsyncClient) -> None:
                """Use a client with no private service credentials attached."""
                self.client = client

        async def _page(self, path: str) -> str:
                try:
                        async with self.client.stream(
                                "GET",
                                "https://open.spotify.com/" + path,
                                follow_redirects=False,
                                timeout=30,
                        ) as response:
                                if response.status_code != 200:
                                        msg = (
                                                "Spotify public metadata is "
                                                "unavailable."
                                        )
                                        raise ValueError(msg)
                                chunks = bytearray()
                                async for chunk in response.aiter_bytes():
                                        chunks.extend(chunk)
                                        if len(chunks) > 4 * 1024 * 1024:
                                                msg = (
                                                        "Spotify metadata page "
                                                        "is too large."
                                                )
                                                raise ValueError(msg)
                except httpx.HTTPError:
                        msg = "Spotify public metadata could not be reached."
                        raise ValueError(msg) from None
                return chunks.decode("utf-8", errors="replace")

        async def snapshot(self, url: str) -> dict[str, Any]:
                """Read a playlist in visible source order."""
                return parse_playlist(
                        await self._page("embed/playlist/" + playlist_id(url))
                )

        async def album_for_track(self, track_id: str) -> dict[str, str]:
                """Resolve the explicitly linked album of a track."""
                if not re.fullmatch(r"[A-Za-z0-9]{22}", track_id):
                        msg = "Invalid public Spotify track identity."
                        raise ValueError(msg)
                album_id = track_album(await self._page("track/" + track_id))
                details = parse_album(
                        await self._page("embed/album/" + album_id)
                )
                return {"id": album_id, **details}
