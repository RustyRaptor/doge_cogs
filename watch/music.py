"""Conservative music identity matching for public playlist imports."""

import asyncio
import logging
import re
import unicodedata
from collections.abc import Awaitable, Callable
from functools import partial
from typing import TYPE_CHECKING, Any

import httpx

from .errors import PreloadError
from .jellyfin import Jellyfin
from .spotify import SpotifyPublic

if TYPE_CHECKING:
        from .musicbrainz import MusicBrainz

log = logging.getLogger("red.doge_cogs.watch.music")

# A track whose catalogue lookup keeps failing becomes reviewable rather
# than retrying silently forever or wedging every later track.
RETRY_ATTEMPTS = 3

# The requester can act on these differently: the first artist is absent from
# MusicBrainz, so only a YouTube copy (or skipping) can help.
MISSING_ARTIST = "Artist not in MusicBrainz"
NO_ALBUM = "No unique artist/album match"
CREDITS = re.compile(r"\s*(?:,|&|feat\.?|featuring)\s*", re.IGNORECASE)


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


def artist_credits(artist: str) -> set[str]:
        """Split Spotify's credited-artist list into comparable names."""
        return {
                name
                for name in (normalized(part) for part in CREDITS.split(artist))
                if name
        }


def appears_on(artist: str, album: dict[str, Any]) -> bool:
        """Require the album to be credited to the Spotify artist."""
        return normalized(
                album.get("artist", {}).get("artistName", "")
        ) in artist_credits(artist)


# A track often resolves to several release groups: the single it was sold as
# and the album it also appears on. Uncached singles are the ones that stall in
# TorBox and push Lidarr into grabbing other releases, so a studio album is the
# safer target and ranks first.
RELEASE_TYPE_ORDER = {"album": 0, "ep": 1, "single": 2}
DERIVATIVE_RELEASE_TYPES = frozenset(
        {
                "compilation",
                "live",
                "remix",
                "djmix",
                "mixtapestreet",
                "soundtrack",
                "spokenword",
                "interview",
                "audiobook",
                "audiodrama",
        }
)


def release_rank(album: dict[str, Any]) -> tuple[int, int]:
        """Rank release groups so a cacheable album beats a single."""
        kind = normalized(str(album.get("albumType") or ""))
        secondary = {
                normalized(str(value))
                for value in (album.get("secondaryTypes") or [])
        }
        derivative = 1 if secondary & DERIVATIVE_RELEASE_TYPES else 0
        order = RELEASE_TYPE_ORDER.get(kind, len(RELEASE_TYPE_ORDER))
        return (order, derivative)


def choose_album(
        source: dict[str, str],
        candidates: list[dict[str, Any]],
        holding: set[str] | None = None,
) -> dict[str, Any] | None:
        """Pick one canonical release group by the credited artist.

        Title and credit must match exactly. When that still leaves several
        release groups, prefer the most cacheable one: album before EP, EP
        before single, and a plain edition before a compilation, live or remix
        edition. A tie between equally good candidates stays ambiguous so the
        requester chooses.
        """
        matches = [
                a
                for a in candidates
                if appears_on(source["artist"], a)
                and album_title(a["title"]) == album_title(source["title"])
        ]
        if holding:
                # A release group MusicBrainz says holds the track beats
                # one that only outranks it on type.
                held = [
                        a for a in matches if a["foreignAlbumId"] in holding
                ]
                if held:
                        matches = held
        unique = {a["foreignAlbumId"]: a for a in matches}
        if not unique:
                return None
        if len(unique) == 1:
                return matches[0]
        best = min(release_rank(a) for a in unique.values())
        winners = [a for a in unique.values() if release_rank(a) == best]
        return winners[0] if len(winners) == 1 else None


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
                musicbrainz: "MusicBrainz | None" = None,
        ) -> None:
                """Inject service clients, persistence and permissions."""
                self.arr = arr
                self.jellyfin = jellyfin
                self.spotify = spotify
                self.save = save
                self.authorize = authorize
                self.lock = lock if lock is not None else asyncio.Lock()
                self.musicbrainz = musicbrainz

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

        async def _artist_known(self, job: dict[str, Any], artist: str) -> bool:
                """Check MusicBrainz once per artist and remember the answer."""
                name = artist.split(",")[0].strip()
                cache = job.setdefault("artist_checks", {})
                key = normalized(name)
                if key not in cache:
                        found = await self.arr(
                                "artist/lookup", params={"term": name}
                        )
                        cache[key] = any(
                                normalized(a.get("artistName", "")) == key
                                for a in found or []
                        )
                        await self.save(job)
                return bool(cache[key])

        async def _groups(
                self, artist: str, title: str
        ) -> list[dict[str, Any]]:
                """Ask MusicBrainz without letting its failure reach the job."""
                if self.musicbrainz is None:
                        return []
                try:
                        groups = await self.musicbrainz.release_groups(
                                artist, title
                        )
                except (httpx.HTTPError, ValueError, RuntimeError):
                        return []
                return [
                        group
                        for group in groups or []
                        if isinstance(group, dict)
                        and isinstance(group.get("foreignAlbumId"), str)
                        and appears_on(artist, group)
                ]

        async def _catalog_album(
                self, groups: list[dict[str, Any]]
        ) -> dict[str, Any] | None:
                """Find the release group MusicBrainz named in the catalog."""
                for group in sorted(groups, key=release_rank):
                        found = await self.arr(
                                "album",
                                params={
                                        "foreignAlbumId": group[
                                                "foreignAlbumId"
                                        ]
                                },
                        )
                        if found and found[0].get("artistId"):
                                return found[0]
                return None

        async def _album(
                self,
                job: dict[str, Any],
                source: dict[str, str],
                track_title: str | None = None,
        ) -> tuple[int | None, str]:
                """Monitor one album, or name why no album was chosen."""
                candidates = await self.arr(
                        "album/lookup", params={"term": source["title"]}
                )
                groups = await self._groups(
                        source["artist"], track_title or source["title"]
                )
                holding = {g["foreignAlbumId"] for g in groups}
                match = choose_album(source, candidates or [], holding)
                if match is None and groups:
                        match = await self._catalog_album(groups)
                if match is not None:
                        return await self._ensure_album(job, match), ""
                if not await self._artist_known(job, source["artist"]):
                        return None, MISSING_ARTIST
                return None, NO_ALBUM

        async def _ensure_album(
                self, job: dict[str, Any], match: dict[str, Any]
        ) -> int | None:
                """Monitor a chosen Lidarr album, adding it when needed."""
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

        async def _search_albums(
                self, job: dict[str, Any], album_ids: list[int]
        ) -> None:
                """Submit one targeted Lidarr album search."""
                if not album_ids:
                        return
                await self._write(
                        job,
                        "Lidarr selected-album search",
                        partial(
                                self.arr,
                                "command",
                                body={
                                        "name": "AlbumSearch",
                                        "albumIds": album_ids,
                                },
                                method="POST",
                        ),
                        result_key="search_id",
                )

        async def assign_album(
                self,
                job: dict[str, Any],
                row: dict[str, Any],
                match: dict[str, Any],
        ) -> int:
                """Monitor a manually chosen album for one reviewed track."""
                self._certain(job)
                album_id = await self._ensure_album(job, match)
                if album_id is None:
                        msg = "The selected album has no usable artist."
                        raise PreloadError(msg)
                row.update(resolved=True, album_id=album_id)
                row.pop("review", None)
                row.pop("attempts", None)
                row.pop("item_id", None)
                job.setdefault("albums", {})[
                        f"manual:{row.get('id') or id(row)}"
                ] = album_id
                await self._search_albums(job, [album_id])
                job["stage"] = "waiting"
                await self.save(job)
                return album_id

        async def _resolve_row(
                self, job: dict[str, Any], row: dict[str, Any]
        ) -> None:
                """Resolve one source row without losing saved progress."""
                albums = job.setdefault("albums", {})
                metadata = job.setdefault("track_albums", {})
                if row.get("youtube_path"):
                        item = await self.jellyfin.audio_at(
                                str(row["youtube_path"]), job["user_id"]
                        )
                        if item:
                                row.update(
                                        resolved=True,
                                        item_id=item,
                                        youtube_item=item,
                                )
                                row.pop("review", None)
                                row.pop("attempts", None)
                                await self.save(job)
                        return
                found = await self.jellyfin.audio(row, job["user_id"])
                if found:
                        row.update(resolved=True, item_id=found)
                        row.pop("review", None)
                        row.pop("attempts", None)
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
                        if sid not in albums or albums[sid] is None:
                                album_id, reason = await self._album(
                                        job, details, row.get("title")
                                )
                                albums[sid] = album_id
                        else:
                                album_id, reason = albums[sid], ""
                        if album_id is None:
                                row["review"] = reason or NO_ALBUM
                        else:
                                row["album_id"] = album_id
                                row.pop("review", None)
                                row.pop("attempts", None)
                except ValueError:
                        row["review"] = "Public album metadata unavailable"
                row["resolved"] = True
                await self.save(job)

        async def skip(self, job: dict[str, Any], row: dict[str, Any]) -> None:
                """Leave one song out permanently, at the requester's choice."""
                self._certain(job)
                row.update(resolved=True, skipped=True)
                for field in (
                        "review",
                        "attempts",
                        "album_id",
                        "item_id",
                        "youtube_path",
                        "youtube_item",
                ):
                        row.pop(field, None)
                job["stage"] = "waiting"
                await self.save(job)

        async def prepare(self, job: dict[str, Any]) -> None:
                """Resolve songs and search only their selected albums.

                Waiting jobs revisit unresolved tracks, so a transient
                catalogue failure heals without a new command.
                """
                self._certain(job)
                if job["stage"] not in {"preparing", "waiting"}:
                        return
                albums = job.setdefault("albums", {})
                for row in job["tracks"]:
                        if row.get("resolved"):
                                continue
                        try:
                                await self._resolve_row(job, row)
                        except PreloadError as exc:
                                await self._lookup_failed(job, row, exc)
                ids = sorted({i for i in albums.values() if i is not None})
                if ids and not job.get("search_id"):
                        await self._search_albums(job, ids)
                job["stage"] = "waiting"
                await self.save(job)

        async def _lookup_failed(
                self,
                job: dict[str, Any],
                row: dict[str, Any],
                exc: PreloadError,
        ) -> None:
                """Isolate one failing track and surface it for review."""
                attempts = int(row.get("attempts", 0)) + 1
                row["attempts"] = attempts
                log.warning(
                        "Playlist track %r lookup failed (%s/%s): %s",
                        row.get("title"),
                        attempts,
                        RETRY_ATTEMPTS,
                        exc,
                )
                if attempts >= RETRY_ATTEMPTS:
                        row.update(
                                resolved=True,
                                review="Catalogue lookup failed",
                        )
                await self.save(job)

        async def _publish(
                self, job: dict[str, Any], ids: list[str], review: int
        ) -> None:
                """Create the playlist or replace its contents in order."""
                if not job.get("playlist_id"):
                        if not ids and review >= len(job["tracks"]):
                                return
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
                        return
                if ids == job.get("playlist_items"):
                        return
                await self.authorize()
                await self.jellyfin.update_playlist(job["playlist_id"], ids)
                job["playlist_items"] = ids

        async def refresh(self, job: dict[str, Any]) -> None:
                """Publish songs and verify complete availability."""
                self._certain(job)
                ids = []
                review = 0
                skipped = 0
                cache: dict[tuple[Any, ...], str | None] = {}
                for row in job["tracks"]:
                        if row.get("youtube_item"):
                                ids.append(str(row["youtube_item"]))
                                continue
                        key = (row.get("id"), row["title"], row["artist"])
                        if key not in cache:
                                cache[key] = await self.jellyfin.audio(
                                        row, job["user_id"]
                                )
                        found = cache[key]
                        if found:
                                ids.append(found)
                                row.pop("review", None)
                                row.pop("attempts", None)
                        elif row.get("skipped"):
                                skipped += 1
                        elif row.get("review"):
                                review += 1
                job["ready"] = len(ids)
                await self._publish(job, ids, review)
                if len(ids) == len(job["tracks"]):
                        job["stage"] = "complete"
                elif len(ids) + review + skipped == len(job["tracks"]):
                        job["stage"] = "attention"
                else:
                        job["stage"] = "waiting"
                await self.save(job)
