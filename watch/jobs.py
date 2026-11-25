"""Persist media subscriptions until background work reaches a result."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Any, cast

import discord
import httpx

from .errors import PreloadError
from .jellyfin import Jellyfin
from .music import MusicImporter
from .musicbrainz import MusicBrainz
from .notify import completion_ping
from .spec import is_finished
from .spotify import SpotifyPublic
from .youtube import Youtube, YoutubeError

if TYPE_CHECKING:
        from collections.abc import AsyncGenerator, Awaitable, Callable

        from media_preload_client.models import PreloadRequest
        from redbot.core import commands

        from .watch import Person, Watch


log = logging.getLogger("red.doge_cogs.watch.jobs")


class Jobs:
        """Keep pending notifications through cog reloads and bot restarts."""

        def __init__(self, cog: Watch) -> None:
                """Use the existing Watch configuration namespace."""
                self.cog = cog
                self.config = cog.config
                self.config.register_global(pending_jobs={})
                self.config.register_user(playlist_links={})
                self.active: set[str] = set()
                self.notify_lock = asyncio.Lock()
                self.limiter = asyncio.Semaphore(3)

        async def register(
                self, ctx: commands.Context, key: str, data: dict[str, Any]
        ) -> str:
                """Subscribe the requester without duplicate jobs."""
                owner = ctx.author.id
                key = f"{owner}:{key}"
                async with self.config.pending_jobs() as jobs:
                        previous = jobs.get(key)
                        if previous and not previous.get("notified"):
                                return key
                        links = await self.config.user_from_id(
                                owner
                        ).playlist_links()
                        link = (
                                links.get(data.get("source_id"))
                                if data.get("kind") == "playlist"
                                else None
                        )
                        carry = (
                                {"playlist_id": link["playlist_id"]}
                                if link
                                and link["user_id"] == data.get("user_id")
                                else {}
                        )
                        jobs[key] = {
                                **carry,
                                "owner": owner,
                                "username": ctx.author.name,
                                "channel": getattr(
                                        getattr(ctx, "channel", None), "id", 0
                                ),
                                "created": datetime.now(UTC).timestamp(),
                                "stage": "waiting",
                                "notified": False,
                                **data,
                        }
                return key

        async def save(self, key: str, job: dict[str, Any]) -> None:
                """Update an extant job; never resurrect deleted user data."""
                async with self.config.pending_jobs() as jobs:
                        if key not in jobs:
                                raise asyncio.CancelledError
                        jobs[key] = job
                        if (
                                job.get("kind") == "playlist"
                                and job.get("playlist_id")
                                and job.get("source_id")
                        ):
                                group = self.config.user_from_id(job["owner"])
                                links = await group.playlist_links()
                                links[job["source_id"]] = {
                                        "playlist_id": job["playlist_id"],
                                        "user_id": job["user_id"],
                                }
                                await group.playlist_links.set(links)

        async def tick(self) -> None:
                """Check each pending subscription once."""
                cutoff = datetime.now(UTC).timestamp() - 7 * 86400
                async with self.config.pending_jobs() as jobs:
                        for key in list(jobs):
                                if (
                                        jobs[key].get("notified")
                                        and jobs[key].get("finished", 0)
                                        < cutoff
                                ):
                                        del jobs[key]
                results = await asyncio.gather(
                        *(
                                self.run_once(key)
                                for key in await self.config.pending_jobs()
                        ),
                        return_exceptions=True,
                )
                for result in results:
                        if isinstance(result, Exception):
                                log.warning(
                                        "Background job check failed (%s)",
                                        type(result).__name__,
                                )

        async def run_once(self, key: str) -> None:
                """Advance one job without overlapping another check of it."""
                if key in self.active:
                        return
                self.active.add(key)
                try:
                        async with self.limiter:
                                await self._advance(key)
                finally:
                        self.active.discard(key)

        async def watch_loop(self) -> None:
                """Resume stored work and check once per minute."""
                await self.cog.bot.wait_until_red_ready()
                while True:
                        await self.tick()
                        await asyncio.sleep(60)

        async def _advance(self, key: str) -> None:
                """Check one extant job and deliver a terminal result."""
                job = (await self.config.pending_jobs()).get(key)
                if not job or job.get("notified"):
                        return
                if (
                        job["stage"]
                        not in {"complete", "attention", "cancelled"}
                        and datetime.now(UTC).timestamp() - job["created"]
                        > 7 * 86400
                ):
                        job["stage"] = "attention"
                        await self.save(key, job)
                if job["stage"] not in {"complete", "attention", "cancelled"}:
                        try:
                                if job["kind"] == "playlist":
                                        await self._music(key, job)
                                else:
                                        await self._media(job)
                        except PermissionError:
                                job["stage"] = "cancelled"
                        except PreloadError as exc:
                                log.warning(
                                        "Background job %s stopped: %s",
                                        key,
                                        exc,
                                )
                                if job.get("mutation"):
                                        job["stage"] = "attention"
                                else:
                                        return
                        await self.save(key, job)
                if job["stage"] in {"complete", "attention", "cancelled"}:
                        await self.finish(key, job)

        @asynccontextmanager
        async def _importer(
                self, key: str, job: dict[str, Any]
        ) -> AsyncGenerator[MusicImporter, None]:
                """Build the album importer for one durable job."""

                async def authorize() -> None:
                        if not await self.cog.can_download(
                                cast("Person", discord.Object(id=job["owner"]))
                        ):
                                msg = "Download permission was revoked."
                                raise PermissionError(msg)

                async def save(state: dict[str, Any]) -> None:
                        await self.save(key, state)

                async def tokens() -> dict[str, str]:
                        return await self.cog.bot.get_shared_api_tokens(
                                "jellyfin"
                        )

                await authorize()
                async with httpx.AsyncClient() as client:
                        yield MusicImporter(
                                partial(self.cog._arr_call, "music"),
                                Jellyfin(tokens),
                                SpotifyPublic(client),
                                save,
                                authorize,
                                lock=self.cog._acquisition_lock,
                                musicbrainz=MusicBrainz(client),
                        )

        async def _music(self, key: str, job: dict[str, Any]) -> None:
                """Run the album importer using the same durable job record."""
                async with self._importer(key, job) as engine:
                        await engine.prepare(job)
                        await engine.refresh(job)

        def youtube(self) -> Youtube:
                """Build the yt-dlp downloader from the deployment settings."""
                return Youtube(
                        binary=os.environ.get("PLAYLIST_YTDLP", "yt-dlp"),
                        folder=os.environ.get(
                                "PLAYLIST_YOUTUBE_DIR", "/music/YouTube"
                        ),
                )

        @staticmethod
        def jellyfin_path(path: str) -> str:
                """Translate the bot path into the path Jellyfin indexes."""
                source = os.environ.get(
                        "PLAYLIST_YOUTUBE_DIR", "/music/YouTube"
                ).rstrip("/")
                target = os.environ.get(
                        "PLAYLIST_YOUTUBE_JELLYFIN_DIR", source
                ).rstrip("/")
                text = str(path)
                if source and text.startswith(source):
                        return target + text[len(source) :]
                return text

        async def fetch_youtube(self, key: str, index: int, url: str) -> None:
                """Download one reviewed song from YouTube and attach it."""
                job = (await self.config.pending_jobs()).get(key)
                if not job or not 0 <= index < len(job.get("tracks", [])):
                        return
                row = job["tracks"][index]
                title = discord.utils.escape_markdown(
                        str(row.get("title", "song"))
                )
                try:
                        async with self._importer(key, job) as engine:
                                path = await self.youtube().download(
                                        url,
                                        str(row.get("artist", "Unknown")),
                                        str(row.get("title", "song")),
                                )
                                target = self.jellyfin_path(str(path))
                                await engine.jellyfin.scan([target])
                                row.update(
                                        youtube_path=target,
                                        youtube_url=url,
                                        resolved=False,
                                )
                                for field in ("review", "attempts", "skipped"):
                                        row.pop(field, None)
                                job["stage"] = "waiting"
                                job["notified"] = False
                                job.pop("finished", None)
                                job["created"] = datetime.now(UTC).timestamp()
                                await self.save(key, job)
                                await self.run_once(key)
                except PermissionError:
                        job["stage"] = "cancelled"
                        await self.save(key, job)
                        return
                except YoutubeError as exc:
                        row.update(
                                resolved=True,
                                skipped=True,
                                review="YouTube download failed",
                        )
                        job["stage"] = "waiting"
                        job["notified"] = False
                        job.pop("finished", None)
                        await self.save(key, job)
                        await completion_ping(
                                self.cog.bot,
                                job.get("channel", 0),
                                job["owner"],
                                f"Couldn't fetch **{title}** from YouTube "
                                f"({exc}). I skipped it for good.",
                        )
                        return
                await completion_ping(
                        self.cog.bot,
                        job.get("channel", 0),
                        job["owner"],
                        f"Fetched **{title}** from YouTube. It joins the "
                        "playlist once Jellyfin indexes it.",
                )

        async def skip(self, key: str, index: int) -> str:
                """Leave one reviewed song out of an import permanently."""
                job = (await self.config.pending_jobs()).get(key)
                if not job:
                        msg = "That playlist import no longer exists."
                        raise PreloadError(msg)
                if job.get("mutation"):
                        msg = "An earlier write needs owner verification."
                        raise PreloadError(msg)
                if not 0 <= index < len(job.get("tracks", [])):
                        msg = "That reviewed song is no longer in the import."
                        raise PreloadError(msg)
                row = job["tracks"][index]
                async with self._importer(key, job) as engine:
                        await engine.skip(job, row)
                job["notified"] = False
                job.pop("finished", None)
                job["created"] = datetime.now(UTC).timestamp()
                await self.save(key, job)
                return str(row.get("title", "song"))

        async def assign_album(
                self, key: str, index: int, match: dict[str, Any]
        ) -> str:
                """Monitor the chosen album and refresh the playlist."""
                job = (await self.config.pending_jobs()).get(key)
                if not job:
                        msg = "That playlist import no longer exists."
                        raise PreloadError(msg)
                if job.get("mutation"):
                        msg = "An earlier write needs owner verification."
                        raise PreloadError(msg)
                if not 0 <= index < len(job.get("tracks", [])):
                        msg = "That reviewed song is no longer in the import."
                        raise PreloadError(msg)
                async with self._importer(key, job) as engine:
                        await engine.assign_album(
                                job, job["tracks"][index], match
                        )
                job["notified"] = False
                job.pop("finished", None)
                job["created"] = datetime.now(UTC).timestamp()
                await self.save(key, job)
                return str(match.get("title", "album"))

        async def _media(self, job: dict[str, Any]) -> None:
                """Check imported files rather than an empty queue."""
                kind = job["kind"]
                if kind == "preload":
                        request = await self.cog.fetch(job["request_id"])
                        if is_finished(request):
                                job["stage"] = "complete"
                                job["result"] = request.status.value
                        return
                field = "tmdbId" if kind == "movie" else "tvdbId"
                title = await self.cog._existing_title(
                        kind, {field: job["foreign_id"]}
                )
                if title is None:
                        return
                if kind == "movie":
                        if title.get("hasFile"):
                                job["stage"] = "complete"
                        return
                episodes = await self.cog._arr_call(
                        "tv", "episode", params={"seriesId": str(title["id"])}
                )
                selected = [
                        e
                        for e in episodes
                        if e.get("seasonNumber") == job["season"]
                        and self._aired(e)
                ]
                if selected and all(e.get("hasFile") for e in selected):
                        job["stage"] = "complete"

        @staticmethod
        def _aired(episode: dict[str, Any]) -> bool:
                """Exclude unknown or future episodes without files."""
                if episode.get("hasFile"):
                        return True
                try:
                        when = datetime.fromisoformat(
                                episode["airDateUtc"].replace("Z", "+00:00")
                        )
                        return when <= datetime.now(UTC)
                except (ValueError, KeyError, TypeError):
                        return False

        @staticmethod
        def message(job: dict[str, Any]) -> str:
                """Describe completion without private service details."""
                if job["kind"] == "playlist":
                        name = discord.utils.escape_markdown(
                                job.get("name", "Playlist")
                        )
                        ready = job.get("ready", 0)
                        count = len(job["tracks"])
                        if job["stage"] == "cancelled":
                                return (
                                        f"Playlist **{name}** import "
                                        f"cancelled: download permission was "
                                        f"removed."
                                )
                        if job["stage"] == "attention":
                                detail = (
                                        " An earlier write needs verification."
                                        if job.get("mutation")
                                        else (
                                                " Check `importplaylist "
                                                "status` for tracks needing "
                                                "review and `importplaylist "
                                                "retry` to try them again."
                                        )
                                )
                                return (
                                        f"Playlist **{name}** import "
                                        f"finished with gaps: "
                                        f"{ready}/{count} songs in "
                                        f"Jellyfin."
                                ) + detail
                        detail = (
                                (
                                        " These are the visible public "
                                        "snapshot songs; Spotify may contain "
                                        "more."
                                )
                                if job.get("partial")
                                else ""
                        )
                        return (
                                f"Your Jellyfin playlist **{name}** is "
                                f"ready ({ready}/{count} songs)."
                        ) + detail
                title = discord.utils.escape_markdown(job.get("title", "Media"))
                if job["stage"] == "attention":
                        return (
                                f"**{title}** still needs attention; it did "
                                f"not finish within seven days."
                        )
                if job["kind"] == "preload":
                        return (
                                f"Preloading **{title}** finished: "
                                f"{job.get('result', 'done')}."
                        )
                season = (
                        f", season {job['season']}"
                        if job["kind"] == "tv"
                        else ""
                )
                return f"**{title}**{season} is downloaded and imported."

        async def finish_preload(
                self,
                key: str,
                request: PreloadRequest,
                send: Callable[..., Awaitable[Any]],
        ) -> None:
                """Notify when an active follower reaches a result."""
                job = (await self.config.pending_jobs()).get(key)
                if job and is_finished(request):
                        job.update(
                                stage="complete", result=request.status.value
                        )
                        await self.save(key, job)
                        await self.finish(key, job, send=send)

        async def finish(
                self,
                key: str,
                job: dict[str, Any],
                *,
                send: Callable[..., Awaitable[Any]] | None = None,
        ) -> None:
                """Persist delivery to prevent repeated pings."""
                async with self.notify_lock:
                        current = (await self.config.pending_jobs()).get(key)
                        if not current or current.get("notified"):
                                return
                        if await completion_ping(
                                self.cog.bot,
                                job.get("channel", 0),
                                job["owner"],
                                self.message(job),
                                send=send,
                        ):
                                job["notified"] = True
                                job["finished"] = datetime.now(UTC).timestamp()
                                await self.save(key, job)
