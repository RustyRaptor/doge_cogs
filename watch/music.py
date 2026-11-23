"""Conservative music identity matching for public playlist imports."""

import asyncio
import re
import unicodedata
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any

from .errors import PreloadError
from .jellyfin import Jellyfin
from .spotify import SpotifyPublic


def normalized(value: str) -> str:
        """Normalize punctuation and Unicode presentation."""
        return "".join(
                c
                for c in unicodedata.normalize("NFKC", value).casefold()
                if c.isalnum()
        )


def album_title(value: str) -> str:
        """Ignore editions without stripping live/remix identity."""
        value = re.sub(
                r"\s*[([][^)\]]*(?:remaster|deluxe|anniversary|expanded|"
                r"special edition)[^)\]]*[)\]]\s*",
                " ",
                value,
                flags=re.IGNORECASE,
        )
        value = re.sub(
                r"\s+-\s+(?:\d{4}\s+)?remaster(?:ed)?.*$",
                "",
                value,
                flags=re.IGNORECASE,
        )
        return normalized(value)


def choose_album(
        source: dict[str, str], candidates: list[dict[str, Any]]
) -> dict[str, Any] | None:
        """Require one canonical release group with matching artist/title."""
        matches = [
                a
                for a in candidates
                if normalized(a.get("artist", {}).get("artistName", ""))
                == normalized(source["artist"].split(",")[0])
                and album_title(a["title"]) == album_title(source["title"])
        ]
        ids = {a["foreignAlbumId"] for a in matches}
        return matches[0] if len(ids) == 1 else None


def find_audio(
        source: dict[str, Any], items: list[dict[str, Any]]
) -> str | None:
        """Require a unique title/artist/length match in the user's library."""
        duration = source.get("duration", 0)
        if not isinstance(duration, (int, float)) or duration <= 0:
                return None
        matches = [
                item
                for item in items
                if normalized(item.get("Name", ""))
                == normalized(source["title"])
                and normalized(source["artist"].split(",")[0])
                in {normalized(a) for a in item.get("Artists", [])}
                and abs(item.get("RunTimeTicks", 0) / 10000 - duration) <= 3000
        ]
        return str(matches[0]["Id"]) if len(matches) == 1 else None


class MusicImporter:
        """Resolve albums and fill a durable Jellyfin playlist."""

        def __init__(
                self,
                arr: Callable[..., Awaitable[Any]],
                jellyfin: Jellyfin,
                spotify: SpotifyPublic,
                save: Callable[[dict[str, Any]], Awaitable[None]],
                authorize: Callable[[], Awaitable[None]],
                *,
                lock: asyncio.Lock | None = None,
        ) -> None:
                """Inject service clients, persistence and permissions."""
                self.arr = arr
                self.jellyfin = jellyfin
                self.spotify = spotify
                self.save = save
                self.authorize = authorize
                self.lock = lock if lock is not None else asyncio.Lock()

        @staticmethod
        def _certain(job: dict[str, Any]) -> None:
                """Stop writes whose acknowledgement was lost."""
                if job.get("mutation"):
                        msg = (
                                "A previous write has an uncertain outcome; "
                                "inspect the job before retrying."
                        )
                        raise PreloadError(msg)

        async def _write(
                self,
                job: dict[str, Any],
                action: str,
                operation: Callable[[], Awaitable[Any]],
                *,
                result_key: str | None = None,
        ) -> Any:  # noqa: ANN401 (upstream JSON)
                """Persist write intent until acknowledgement."""
                await self.authorize()
                job["mutation"] = action
                await self.save(job)
                await self.authorize()
                result = await operation()
                if result_key:
                        job[result_key] = (
                                result["id"]
                                if isinstance(result, dict)
                                else result
                        )
                job.pop("mutation", None)
                await self.save(job)
                return result

        async def _album(
                self, job: dict[str, Any], source: dict[str, str]
        ) -> int | None:
                """Monitor one album without changing other choices."""
                candidates = await self.arr(
                        "album/lookup", params={"term": source["title"]}
                )
                match = choose_album(source, candidates)
                if match is None:
                        return None
                async with self.lock:
                        existing = await self.arr(
                                "album",
                                params={
                                        "foreignAlbumId": match[
                                                "foreignAlbumId"
                                        ]
                                },
                        )
                        if existing:
                                album = existing[0]
                                if not album.get("artistId"):
                                        return None
                                if not album["monitored"]:
                                        album["monitored"] = True
                                        await self._write(
                                                job,
                                                "Lidarr album monitoring",
                                                partial(
                                                        self.arr,
                                                        f"album/{album['id']}",
                                                        body=album,
                                                        method="PUT",
                                                ),
                                        )
                                return int(album["id"])
                        roots = await self.arr("rootfolder")
                        root = next(
                                (
                                        r
                                        for r in roots
                                        if r["path"] == "/arr/library/Music"
                                ),
                                None,
                        )
                        if root is None:
                                msg = "The Lidarr music root is not configured."
                                raise PreloadError(msg)
                        artists = await self.arr("artist")
                        known = {a["foreignArtistId"] for a in artists}
                        artist = dict(match["artist"])
                        artist.update(
                                rootFolderPath=root["path"],
                                qualityProfileId=root[
                                        "defaultQualityProfileId"
                                ],
                                metadataProfileId=root[
                                        "defaultMetadataProfileId"
                                ],
                                monitored=True,
                                monitorNewItems="none",
                                addOptions={
                                        "monitor": "none",
                                        "searchForMissingAlbums": False,
                                },
                        )
                        payload = {
                                **match,
                                "artist": artist,
                                "monitored": True,
                                "addOptions": {"searchForNewAlbum": False},
                        }
                        album = await self._write(
                                job,
                                "Lidarr album add",
                                partial(
                                        self.arr,
                                        "album",
                                        body=payload,
                                        method="POST",
                                ),
                        )
                        if artist["foreignArtistId"] not in known:
                                created = await self.arr(
                                        f"artist/{album['artistId']}"
                                )
                                created.update(
                                        monitored=True, monitorNewItems="none"
                                )
                                await self._write(
                                        job,
                                        "Lidarr selected-artist activation",
                                        partial(
                                                self.arr,
                                                f"artist/{created['id']}",
                                                body=created,
                                                method="PUT",
                                        ),
                                )
                        return int(album["id"])

        async def _resolve_row(
                self, job: dict[str, Any], row: dict[str, Any]
        ) -> None:
                """Resolve one source row without losing saved progress."""
                albums = job.setdefault("albums", {})
                metadata = job.setdefault("track_albums", {})
                found = await self.jellyfin.audio(row, job["user_id"])
                if found:
                        row.update(resolved=True, item_id=found)
                        await self.save(job)
                        return
                if not row.get("id"):
                        row.update(
                                resolved=True,
                                review="Unavailable source track",
                        )
                        await self.save(job)
                        return
                try:
                        details = metadata.get(row["id"])
                        if details is None:
                                details = await self.spotify.album_for_track(
                                        row["id"]
                                )
                                metadata[row["id"]] = details
                        sid = details["id"]
                        if sid not in albums:
                                albums[sid] = await self._album(job, details)
                        if albums[sid] is None:
                                row["review"] = "No unique artist/album match"
                        else:
                                row["album_id"] = albums[sid]
                except ValueError:
                        row["review"] = "Public album metadata unavailable"
                row["resolved"] = True
                await self.save(job)

        async def prepare(self, job: dict[str, Any]) -> None:
                """Resolve songs and search only their selected albums."""
                self._certain(job)
                if job["stage"] != "preparing":
                        return
                albums = job.setdefault("albums", {})
                for row in job["tracks"]:
                        if row.get("resolved"):
                                continue
                        await self._resolve_row(job, row)
                ids = sorted({i for i in albums.values() if i is not None})
                if ids and not job.get("search_id"):
                        command = await self._write(
                                job,
                                "Lidarr selected-album search",
                                partial(
                                        self.arr,
                                        "command",
                                        body={
                                                "name": "AlbumSearch",
                                                "albumIds": ids,
                                        },
                                        method="POST",
                                ),
                                result_key="search_id",
                        )
                        job["search_id"] = command["id"]
                job["stage"] = "waiting"
                await self.save(job)

        async def refresh(self, job: dict[str, Any]) -> None:
                """Publish songs and verify complete availability."""
                self._certain(job)
                ids = []
                review = 0
                cache = {}
                for row in job["tracks"]:
                        key = (row.get("id"), row["title"], row["artist"])
                        if key not in cache:
                                cache[key] = await self.jellyfin.audio(
                                        row, job["user_id"]
                                )
                        found = cache[key]
                        if found:
                                ids.append(found)
                        elif row.get("review"):
                                review += 1
                job["ready"] = len(ids)
                if not job.get("playlist_id") and (
                        ids or review < len(job["tracks"])
                ):
                        name = f"{job['username']} - {job['name']}"[:250]
                        job["playlist_id"] = await self._write(
                                job,
                                "Jellyfin playlist create",
                                partial(
                                        self.jellyfin.create_playlist,
                                        name,
                                        job["user_id"],
                                        ids,
                                ),
                                result_key="playlist_id",
                        )
                        job["playlist_items"] = ids
                elif job.get("playlist_id") and ids != job.get(
                        "playlist_items"
                ):
                        await self.authorize()
                        await self.jellyfin.update_playlist(
                                job["playlist_id"], ids
                        )
                        job["playlist_items"] = ids
                if len(ids) == len(job["tracks"]):
                        job["stage"] = "complete"
                elif len(ids) + review == len(job["tracks"]):
                        job["stage"] = "attention"
                else:
                        job["stage"] = "waiting"
                await self.save(job)
