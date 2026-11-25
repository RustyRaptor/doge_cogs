"""Resolve a track or album to the MusicBrainz release groups that hold it.

Lidarr's album lookup matches titles, so it cannot say whether an album really
contains the requested track, and it finds nothing when the streaming title
differs from MusicBrainz's. MusicBrainz answers both: a recording search
returns the release groups that contain that recording, with their release
types and track lists.

The API allows one request per second and answers 429/503 without a
descriptive User-Agent, so calls are spaced and retried, and any failure
returns no groups: an import must never break because MusicBrainz is down.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from .music import artist_credits, normalized

USER_AGENT = "Dogebot/1.0 ( https://github.com/RustyRaptor/doge_cogs )"
SEARCH_URL = "https://musicbrainz.org/ws/2/recording"
MIN_INTERVAL = 1.0
RETRIES = 3


def _quoted(value: str) -> str:
        """Escape a Lucene term the way MusicBrainz expects."""
        return value.replace("\\", "\\\\").replace('"', '\\"')


def build_query(artist: str, title: str) -> dict[str, str]:
        """Ask for recordings of one title by one artist."""
        term = f'recording:"{_quoted(title)}"'
        if artist.strip():
                term += f' AND artist:"{_quoted(artist.split(",")[0].strip())}"'
        return {"query": term, "fmt": "json", "limit": "25"}


def parse_recordings(payload: dict[str, Any]) -> list[dict[str, Any]]:
        """Flatten a recording search into one row per release group."""
        groups: dict[str, dict[str, Any]] = {}
        for recording in payload.get("recordings") or []:
                if not isinstance(recording, dict):
                        continue
                for release in recording.get("releases") or []:
                        if not isinstance(release, dict):
                                continue
                        meta = release.get("release-group") or {}
                        group_id = meta.get("id")
                        if not isinstance(group_id, str) or not group_id:
                                continue
                        credited = release.get("artist-credit") or (
                                recording.get("artist-credit") or []
                        )
                        name = ""
                        if credited and isinstance(credited[0], dict):
                                name = credited[0].get("name") or ""
                        primary = meta.get("primary-type") or ""
                        secondary = list(
                                meta.get("secondary-types") or []
                        )
                        row = groups.setdefault(
                                group_id,
                                {
                                        "id": 0,
                                        "foreignAlbumId": group_id,
                                        "title": meta.get("title")
                                        or release.get("title")
                                        or "",
                                        "albumType": primary,
                                        "secondaryTypes": secondary,
                                        "releaseDate": meta.get(
                                                "first-release-date"
                                        )
                                        or "",
                                        "artist": {"artistName": name},
                                        "tracks": [],
                                },
                        )
                        for medium in release.get("media") or []:
                                for track in medium.get("track") or []:
                                        title = track.get("title")
                                        if isinstance(title, str) and title:
                                                row["tracks"].append(title)
        return list(groups.values())


def credited_groups(
        groups: list[dict[str, Any]], artist: str
) -> list[dict[str, Any]]:
        """Keep the release groups credited to the requested artist."""
        allowed = artist_credits(artist)
        return [
                group
                for group in groups
                if normalized(
                        str(group.get("artist", {}).get("artistName", ""))
                )
                in allowed
        ]


def holds_track(
        groups: list[dict[str, Any]], release_group_id: str, title: str
) -> bool:
        """Say whether one release group lists the requested track."""
        wanted = normalized(title)
        if not wanted:
                return False
        for group in groups:
                if group.get("foreignAlbumId") != release_group_id:
                        continue
                return any(
                        normalized(str(track)) == wanted
                        for track in group.get("tracks") or []
                )
        return False


class MusicBrainz:
        """Query MusicBrainz within its rate limit."""

        def __init__(
                self,
                client: httpx.AsyncClient,
                *,
                sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                clock: Callable[[], float] = time.monotonic,
        ) -> None:
                """Keep the shared client and the throttling seams."""
                self.client = client
                self._sleep = sleep
                self._clock = clock
                self._last: float | None = None

        async def _space(self) -> None:
                """Leave a full second between requests."""
                if self._last is not None:
                        wait = MIN_INTERVAL - (self._clock() - self._last)
                        if wait > 0:
                                await self._sleep(wait)
                self._last = self._clock()

        async def _get(self, params: dict[str, str]) -> dict[str, Any]:
                """Return a payload, retrying the documented failure codes."""
                for attempt in range(RETRIES):
                        await self._space()
                        try:
                                reply = await self.client.get(
                                        SEARCH_URL,
                                        params=params,
                                        headers={
                                                "User-Agent": USER_AGENT,
                                                "Accept": "application/json",
                                        },
                                        timeout=30,
                                )
                        except httpx.HTTPError:
                                await self._sleep(float(attempt + 1))
                                continue
                        if reply.status_code == 200:  # noqa: PLR2004 (HTTP)
                                try:
                                        payload = reply.json()
                                except ValueError:
                                        return {}
                                if isinstance(payload, dict):
                                        return payload
                                return {}
                        if reply.status_code in {429, 503}:
                                await self._sleep(float(attempt + 1))
                                continue
                        return {}
                return {}

        async def release_groups(
                self, artist: str, title: str
        ) -> list[dict[str, Any]]:
                """List credited release groups that hold this recording."""
                if not title.strip():
                        return []
                payload = await self._get(build_query(artist, title))
                if not payload:
                        return []
                return credited_groups(parse_recordings(payload), artist)
