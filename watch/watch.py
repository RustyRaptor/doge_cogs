"""``d!watch``: preload a movie or episodes into the media cache.

Talks to media-preload (the home lab's preload service) through the
generated ``media_preload_client``. Files are fetched into the VPS media
cache so they stream quickly from webdav.
"""

import asyncio
import contextlib
import logging
import os
from collections.abc import Awaitable, Callable, Coroutine
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Literal

import discord
import httpx
from media_preload_client import AuthenticatedClient
from media_preload_client.api.library import search_library
from media_preload_client.api.requests import (
        cancel_request,
        create_request,
        get_request,
        list_requests,
)
from media_preload_client.models import (
        CreateRequest,
        MediaItem,
        MediaType,
        PreloadRequest,
        Problem,
)
from media_preload_client.types import UNSET, Response
from redbot.core import Config, commands
from redbot.core.bot import Red

from .downloads import describe_downloads, normalized_title
from .errors import PreloadError, SeasonError
from .jellyfin import Jellyfin
from .jobs import Jobs
from .notify import completion_ping
from .retention import SeasonPlan
from .seasonal import SeasonManager
from .spec import (
        DEFAULT_NEXT,
        EpisodeSpec,
        SpecError,
        describe,
        is_finished,
        parse_episodes,
        parse_season,
        split_query,
)
from .spotify import SpotifyPublic, playlist_id
from .youtube import YoutubeError, youtube_url

log = logging.getLogger("red.doge_cogs.watch")

# Inside a rootless podman network the host's LAN address is the container
# itself; podman maps host.containers.internal to the host.
DEFAULT_URL = "http://host.containers.internal:8125"
POLL_SECONDS = 10
POLL_LIMIT_SECONDS = 3 * 3600
VIEW_TIMEOUT = 120

# Who can run a command: a server member, or a user in DMs
Person = discord.User | discord.Member

RequestType = Literal["discord_deleted_user", "owner", "user", "user_strict"]


def _unwrap(response: Response[Any]) -> Any:  # noqa: ANN401 (generated types)
        """Return a response's model, or raise with the API's error message."""
        if isinstance(response.parsed, Problem):
                raise PreloadError(response.parsed.error)
        if response.status_code >= 400 or response.parsed is None:  # noqa: PLR2004
                msg = f"media-preload answered HTTP {response.status_code}"
                raise PreloadError(msg)
        return response.parsed


async def _timed_out(prompt: discord.Message) -> None:
        """Replace an expired prompt's buttons with a note."""
        with contextlib.suppress(discord.HTTPException):
                await prompt.edit(content="Never mind, timed out.", view=None)


def item_label(item: MediaItem) -> str:
        """Return e.g. ``Dark Matter (2024) · series``."""
        year = f" ({item.year})" if isinstance(item.year, int) else ""
        return f"{item.name}{year} · {item.type_.value}"


def catalogue_label(title: dict[str, Any]) -> str:
        """Distinguish catalogue remakes by title and year."""
        return f"{title['title']} ({title.get('year', '?')})"


def album_choice_label(album: dict[str, Any]) -> str:
        """Name a Lidarr album choice for a reviewed song."""
        released = str(album.get("releaseDate") or "")
        year = released[:4] or "?"
        artist = album.get("artist", {}).get("artistName", "?")
        label = f"{album.get('title', 'Album')} ({year}) · {artist}"
        return discord.utils.escape_markdown(label)[:100]


def video_label(video: dict[str, Any]) -> str:
        """Name a YouTube candidate with its channel and duration."""
        seconds = int(video.get("duration") or 0)
        length = f"{seconds // 60}:{seconds % 60:02d}" if seconds else "?"
        label = (
                f"{video.get('title', 'Untitled')} · "
                f"{video.get('uploader', 'unknown')} · {length}"
        )
        return discord.utils.escape_markdown(label)[:100]


def review_summary(
        tracks: list[dict[str, Any]], *, limit: int = 5
) -> list[str]:
        """Group reviewed songs by reason so the requester can act."""
        groups: dict[str, list[str]] = {}
        for row in tracks:
                reason = row.get("review")
                if not reason:
                        continue
                title = discord.utils.escape_markdown(
                        str(row.get("title", "Unavailable track"))
                )
                groups.setdefault(str(reason), []).append(title)
        lines = []
        for reason, titles in groups.items():
                listed = ", ".join(titles[:limit])
                more = len(titles) - limit
                if more > 0:
                        listed += f" and {more} more"
                label = discord.utils.escape_markdown(reason)
                lines.append(
                        f"Needs review · {label} ({len(titles)}): {listed}"
                )
        return lines


class OwnerView(discord.ui.View):
        """A view that only the user who ran the command can use."""

        def __init__(self, author: Person) -> None:
                """Lock the view to ``author``."""
                super().__init__(timeout=VIEW_TIMEOUT)
                self.author = author

        async def interaction_check(
                self, interaction: discord.Interaction
        ) -> bool:
                """Refuse clicks from anyone else."""
                if interaction.user.id == self.author.id:
                        return True
                await interaction.response.send_message(
                        "This isn't your request.", ephemeral=True
                )
                return False


class DownloadSelect(discord.ui.Select["PickDownloadOption"]):
        """A single validated step in the acquisition wizard."""

        def __init__(self, options: list[tuple[str, str]]) -> None:
                """Offer choices plus an explicit cancellation option."""
                super().__init__(
                        placeholder="Choose an option",
                        options=[
                                discord.SelectOption(
                                        label=label[:100], value=value
                                )
                                for value, label in options
                        ]
                        + [
                                discord.SelectOption(
                                        label="Cancel", value="cancel"
                                )
                        ],
                )

        async def callback(self, interaction: discord.Interaction) -> None:
                """Pass a Discord selection back to its view."""
                if self.view is not None:
                        await self.view.picked(interaction, self.values[0])


class PickDownloadOption(OwnerView):
        """One requester-owned choice, consumable only once."""

        def __init__(
                self, author: Person, options: list[tuple[str, str]]
        ) -> None:
                """Build the step without any download side effects."""
                super().__init__(author)
                self.allowed = {key for key, _ in options} | {"cancel"}
                self.chosen: str | None = None
                self.add_item(DownloadSelect(options))

        async def picked(
                self, interaction: discord.Interaction, value: str
        ) -> None:
                """Accept one advertised choice from the requester."""
                if not await self.interaction_check(interaction):
                        return
                if self.is_finished() or value not in self.allowed:
                        await interaction.response.send_message(
                                "That choice has expired or is invalid.",
                                ephemeral=True,
                        )
                        return
                self.chosen = None if value == "cancel" else value
                self.stop()
                await interaction.response.edit_message(
                        content="Cancelled."
                        if self.chosen is None
                        else "Selection saved.",
                        view=None,
                )


class ItemSelect(discord.ui.Select["PickItem"]):
        """The select menu inside :class:`PickItem`."""

        def __init__(self, items: list[MediaItem]) -> None:
                """List up to 25 ``items``."""
                super().__init__(
                        placeholder="Which one?",
                        options=[
                                discord.SelectOption(
                                        label=item_label(item)[:100],
                                        value=item.id,
                                )
                                for item in items[:25]
                        ],
                )

        async def callback(self, interaction: discord.Interaction) -> None:
                """Hand the choice to the view."""
                if self.view is not None:
                        await self.view.picked(interaction, self.values[0])


class PickItem(OwnerView):
        """Choose one search result."""

        def __init__(self, author: Person, items: list[MediaItem]) -> None:
                """Offer ``items`` in a select menu."""
                super().__init__(author)
                self.items = {item.id: item for item in items}
                self.chosen: MediaItem | None = None
                self.add_item(ItemSelect(items))

        async def picked(
                self, interaction: discord.Interaction, item_id: str
        ) -> None:
                """Record the chosen item and close the view."""
                self.chosen = self.items[item_id]
                await interaction.response.edit_message(
                        content=f"Picked **{item_label(self.chosen)}**.",
                        view=None,
                )
                self.stop()


class EpisodesModal(discord.ui.Modal, title="Which episodes?"):
        """Ask for a season and, optionally, episode numbers."""

        season: "discord.ui.TextInput[EpisodesModal]" = discord.ui.TextInput(
                label="Season", placeholder="2", max_length=2
        )
        episodes: "discord.ui.TextInput[EpisodesModal]" = discord.ui.TextInput(
                label="Episodes (empty for the whole season)",
                placeholder="8-10, 12",
                required=False,
                max_length=60,
        )

        def __init__(self, view: "PickEpisodes") -> None:
                """Report the answer back to ``view``."""
                super().__init__(timeout=VIEW_TIMEOUT)
                self.view = view

        async def on_submit(self, interaction: discord.Interaction) -> None:
                """Validate the answer and hand it to the view."""
                try:
                        season = parse_season(self.season.value)
                        episodes = (
                                parse_episodes(self.episodes.value)
                                if self.episodes.value.strip()
                                else ()
                        )
                except (ValueError, SpecError) as exc:
                        await interaction.response.send_message(
                                f"That didn't work: {exc}", ephemeral=True
                        )
                        return
                await self.view.finish(
                        interaction, EpisodeSpec(season, episodes)
                )


class PickEpisodes(OwnerView):
        """Choose the next unwatched episodes or a season/episodes."""

        def __init__(
                self,
                author: Person,
                jellyfin_user: str | None,
        ) -> None:
                """Offer "next" only when a Jellyfin user is linked."""
                super().__init__(author)
                self.spec: EpisodeSpec | None = None
                if jellyfin_user is None:
                        self.next_button.disabled = True
                        self.next_button.label = "Next 3 (link first)"

        async def finish(
                self, interaction: discord.Interaction, spec: EpisodeSpec
        ) -> None:
                """Record ``spec`` and close the view."""
                self.spec = spec
                await interaction.response.edit_message(
                        content=f"Preloading {spec.describe()}…", view=None
                )
                self.stop()

        @discord.ui.button(
                label=f"Next {DEFAULT_NEXT} unwatched",
                style=discord.ButtonStyle.primary,
        )
        async def next_button(
                self,
                interaction: discord.Interaction,
                _button: discord.ui.Button["PickEpisodes"],
        ) -> None:
                """Preload the next unwatched episodes."""
                await self.finish(interaction, EpisodeSpec(next=DEFAULT_NEXT))

        @discord.ui.button(label="Season / episodes…")
        async def season_button(
                self,
                interaction: discord.Interaction,
                _button: discord.ui.Button["PickEpisodes"],
        ) -> None:
                """Ask for a season and episodes."""
                await interaction.response.send_modal(EpisodesModal(self))


class CancelView(discord.ui.View):
        """A cancel button under a progress message."""

        def __init__(
                self, cog: "Watch", request_id: int, owner_id: int
        ) -> None:
                """Cancel ``request_id``; allowed for its requester and mods."""
                super().__init__(timeout=None)
                self.cog = cog
                self.request_id = request_id
                self.owner_id = owner_id

        @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
        async def cancel(
                self,
                interaction: discord.Interaction,
                _button: discord.ui.Button["CancelView"],
        ) -> None:
                """Cancel the files that are still queued."""
                user = interaction.user
                allowed = user.id == self.owner_id or (
                        isinstance(user, discord.Member)
                        and user.guild_permissions.manage_messages
                )
                if not allowed:
                        await interaction.response.send_message(
                                "Only the requester or a moderator can cancel.",
                                ephemeral=True,
                        )
                        return
                try:
                        await self.cog.cancel(self.request_id)
                except (PreloadError, httpx.HTTPError) as exc:
                        await interaction.response.send_message(
                                f"Couldn't cancel: {exc}", ephemeral=True
                        )
                        return
                await interaction.response.send_message(
                        "Canceled the files that hadn't started.",
                        ephemeral=True,
                )


class Watch(commands.Cog):
        """Preload movies and episodes so they stream quickly from webdav."""

        def __init__(self, bot: Red) -> None:
                """Create the cog."""
                self.bot = bot
                self.config = Config.get_conf(
                        self, identifier=0x0D06EBA7C4, force_registration=True
                )
                self.config.register_user(
                        jellyfin_user=None,
                        download_allowed=False,
                        tv_seasons={},
                )
                self.config.register_global(season_retention_baseline=None)
                self._acquisition_lock = asyncio.Lock()
                self._background: Jobs | None = None
                self._command_runs: dict[int, tuple[float, int]] = {}
                self._pollers: set[asyncio.Task[None]] = set()

        @commands.Cog.listener("on_command")
        async def command_started(self, ctx: commands.Context) -> None:
                """Remember when a human starts a command in any cog."""
                if ctx.command is not None and not getattr(
                        ctx.author, "bot", False
                ):
                        self._command_runs[ctx.message.id] = (
                                monotonic(),
                                ctx.author.id,
                        )

        @commands.Cog.listener("on_command_completion")
        async def command_finished(self, ctx: commands.Context) -> None:
                """Ping after long commands outside media workflows."""
                await self._command_result(ctx, failed=False)

        @commands.Cog.listener("on_command_error")
        async def command_failed(
                self, ctx: commands.Context, error: commands.CommandError
        ) -> None:
                """Report failure without private exception bodies."""
                del error
                await self._command_result(ctx, failed=True)

        async def _command_result(
                self, ctx: commands.Context, *, failed: bool
        ) -> None:
                """Avoid instant and duplicate completion pings."""
                start = self._command_runs.pop(ctx.message.id, None)
                if (
                        start is None
                        or ctx.command is None
                        or monotonic() - start[0] < 10
                ):
                        return
                name = ctx.command.qualified_name
                if name.split()[0] in {"watch", "request", "importplaylist"}:
                        return
                result = "ended with an error" if failed else "finished"
                await completion_ping(
                        self.bot,
                        ctx.channel.id,
                        ctx.author.id,
                        f"Command `{name}` {result}.",
                        send=ctx.send,
                )

        def background(self) -> Jobs:
                """Initialize durable jobs during cog upgrades."""
                jobs = getattr(self, "_background", None)
                if jobs is None:
                        jobs = Jobs(self)
                        self._background = jobs
                return jobs

        def _spawn(self, coroutine: Coroutine[Any, Any, None]) -> None:
                """Track a background task so unloading cancels it."""
                task = asyncio.create_task(coroutine)
                self._pollers.add(task)
                task.add_done_callback(self._pollers.discard)

        async def cog_load(self) -> None:
                """Resume persisted imports and notifications after startup."""
                self._spawn(self.background().watch_loop())

        async def cog_unload(self) -> None:
                """Stop polling."""
                tasks = list(self._pollers)
                for task in tasks:
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

        async def red_delete_data_for_user(
                self, *, requester: RequestType, user_id: int
        ) -> None:
                """Forget a user's linked Jellyfin name."""
                del requester
                if hasattr(self, "_command_runs"):
                        self._command_runs = {
                                key: run
                                for key, run in self._command_runs.items()
                                if run[1] != user_id
                        }
                self.config.register_global(pending_jobs={})
                async with self.config.pending_jobs() as jobs:
                        for key in list(jobs):
                                if jobs[key].get("owner") == user_id:
                                        del jobs[key]
                        await self.config.user_from_id(user_id).clear()

        async def _client(self) -> AuthenticatedClient:
                tokens = await self.bot.get_shared_api_tokens("media_preload")
                token = os.environ.get("PRELOAD_BOT_TOKEN") or tokens.get(
                        "token"
                )
                if not token:
                        msg = (
                                "no media-preload token: set PRELOAD_BOT_TOKEN "
                                "or `[p]set api media_preload token,...`"
                        )
                        raise PreloadError(msg)
                url = (
                        os.environ.get("PRELOAD_API_URL")
                        or tokens.get("url")
                        or DEFAULT_URL
                )
                return AuthenticatedClient(
                        base_url=url,
                        token=token,
                        timeout=httpx.Timeout(30.0),
                )

        async def search(
                self, query: str, *, series_only: bool
        ) -> list[MediaItem]:
                """Search the library; cacheable items first."""
                client = await self._client()
                async with client as c:
                        items = _unwrap(
                                await search_library.asyncio_detailed(
                                        client=c,
                                        q=query,
                                        type_=MediaType.SERIES
                                        if series_only
                                        else UNSET,
                                        limit=10,
                                )
                        )
                return sorted(items, key=lambda i: not i.cacheable)

        async def create(
                self,
                item: MediaItem,
                spec: EpisodeSpec | None,
                requester: Person,
                jellyfin_user: str | None,
        ) -> PreloadRequest:
                """Ask media-preload to cache ``item``."""
                body = CreateRequest(
                        item_id=item.id,
                        requested_by=f"discord:{requester.name}",
                )
                if item.type_ == MediaType.SERIES and spec is not None:
                        if spec.next is not None:
                                body.next_ = spec.next
                        else:
                                body.season = spec.season or UNSET
                                if spec.episodes:
                                        body.episodes = list(spec.episodes)
                if jellyfin_user:
                        body.jellyfin_user = jellyfin_user
                client = await self._client()
                async with client as c:
                        return _unwrap(
                                await create_request.asyncio_detailed(
                                        client=c, body=body
                                )
                        )

        async def fetch(self, request_id: int) -> PreloadRequest:
                """Return a request with per-file progress."""
                client = await self._client()
                async with client as c:
                        return _unwrap(
                                await get_request.asyncio_detailed(
                                        request_id, client=c
                                )
                        )

        async def cancel(self, request_id: int) -> PreloadRequest:
                """Cancel the files of a request that are still queued."""
                client = await self._client()
                async with client as c:
                        return _unwrap(
                                await cancel_request.asyncio_detailed(
                                        request_id, client=c
                                )
                        )

        def _embed(self, request: PreloadRequest) -> discord.Embed:
                colour = (
                        discord.Colour.green()
                        if request.status.value == "done"
                        else discord.Colour.blurple()
                        if not is_finished(request)
                        else discord.Colour.orange()
                )
                embed = discord.Embed(
                        title=f"Preloading {request.title}",
                        description=describe(request),
                        colour=colour,
                )
                embed.set_footer(
                        text=f"Request #{request.id} · {request.status.value}"
                )
                return embed

        async def _follow(
                self,
                message: discord.Message,
                request: PreloadRequest,
                *,
                job_key: str | None = None,
                send: Callable[..., Awaitable[Any]] | None = None,
        ) -> None:
                """Edit ``message`` with progress until the request finishes."""
                waited = 0
                while not is_finished(request) and waited < POLL_LIMIT_SECONDS:
                        await asyncio.sleep(POLL_SECONDS)
                        waited += POLL_SECONDS
                        try:
                                request = await self.fetch(request.id)
                        except (PreloadError, httpx.HTTPError):
                                log.warning(
                                        "Polling request %s failed",
                                        request.id,
                                        exc_info=True,
                                )
                                continue
                        with contextlib.suppress(discord.HTTPException):
                                await message.edit(embed=self._embed(request))
                with contextlib.suppress(discord.HTTPException):
                        await message.edit(
                                embed=self._embed(request), view=None
                        )
                if job_key and send and is_finished(request):
                        await self.background().finish_preload(
                                job_key, request, send
                        )

        async def _start(
                self,
                ctx: commands.Context,
                item: MediaItem,
                spec: EpisodeSpec | None,
        ) -> None:
                jellyfin_user = await self.config.user(
                        ctx.author
                ).jellyfin_user()
                try:
                        request = await self.create(
                                item, spec, ctx.author, jellyfin_user
                        )
                except (PreloadError, httpx.HTTPError) as exc:
                        if await self._show_download_status(
                                ctx,
                                item.name,
                                series_only=item.type_ == MediaType.SERIES,
                        ):
                                return
                        await ctx.send(f"Couldn't start that: {exc}")
                        return
                job_key = await self.background().register(
                        ctx,
                        f"preload:{request.id}",
                        {
                                "kind": "preload",
                                "request_id": request.id,
                                "title": request.title,
                        },
                )
                message = await ctx.send(
                        embed=self._embed(request),
                        view=None
                        if is_finished(request)
                        else CancelView(self, request.id, ctx.author.id),
                )
                task = asyncio.create_task(
                        self._follow(
                                message, request, job_key=job_key, send=ctx.send
                        )
                )
                self._pollers.add(task)
                task.add_done_callback(self._pollers.discard)

        async def _pick_item(
                self, ctx: commands.Context, items: list[MediaItem], title: str
        ) -> MediaItem | None:
                exact = [i for i in items if i.name.lower() == title.lower()]
                if len(items) == 1 or len(exact) == 1:
                        return exact[0] if exact else items[0]
                view = PickItem(ctx.author, items)
                prompt = await ctx.send(
                        f"I found {len(items)} matches for **{title}**:",
                        view=view,
                )
                if await view.wait():
                        await _timed_out(prompt)
                        return None
                return view.chosen

        @commands.guild_only()
        @commands.cooldown(1, 15, commands.BucketType.user)
        @commands.group(name="watch", invoke_without_command=True)
        async def watch(self, ctx: commands.Context, *, query: str) -> None:
                """Preload something you're about to watch.

                Examples:
                - `[p]watch The Matrix`
                - `[p]watch Dark Matter s2e8-10` (episodes 8 to 10 of season 2)
                - `[p]watch Dark Matter s2` (all of season 2)
                - `[p]watch Dark Matter next 4` (your next 4 unwatched)

                For a series without episodes, you get buttons to choose.
                "next" needs `[p]watch link <jellyfin name>` first.

                """
                try:
                        title, spec = split_query(query)
                except SpecError as exc:
                        await ctx.send(f"I didn't get the episodes: {exc}")
                        return
                if len(title) < 2:  # noqa: PLR2004 (the API's minimum)
                        await ctx.send("Give me a title to look for.")
                        return
                async with ctx.typing():
                        try:
                                items = await self.search(
                                        title, series_only=spec is not None
                                )
                        except (PreloadError, httpx.HTTPError) as exc:
                                await ctx.send(f"Search failed: {exc}")
                                return
                cacheable = [i for i in items if i.cacheable]
                if not cacheable:
                        if await self._show_download_status(
                                ctx, title, series_only=spec is not None
                        ):
                                return
                        hint = f"{ctx.clean_prefix}request {title}"
                        missing = (
                                f"Nothing cacheable matches **{title}**."
                                if items
                                else f"I couldn't find **{title}**."
                        )
                        await ctx.send(
                                missing + f" Request a download with `{hint}` "
                                "(owner permission required)."
                        )
                        return
                item = await self._pick_item(ctx, cacheable, title)
                if item is None:
                        return
                jellyfin_user = await self.config.user(
                        ctx.author
                ).jellyfin_user()
                if (
                        spec is not None
                        and spec.next is not None
                        and not jellyfin_user
                ):
                        await ctx.send(
                                "For **next**, tell me your Jellyfin name with "
                                f"`{ctx.clean_prefix}watch link <name>`."
                        )
                        return
                if item.type_ == MediaType.SERIES and spec is None:
                        view = PickEpisodes(ctx.author, jellyfin_user)
                        prompt = await ctx.send(
                                f"Which episodes of **{item.name}**?", view=view
                        )
                        if await view.wait() or view.spec is None:
                                await _timed_out(prompt)
                                return
                        spec = view.spec
                await self._start(ctx, item, spec)

        async def can_download(self, user: Person) -> bool:
                """Check owner status or an individual download grant."""
                return await self.bot.is_owner(user) or bool(
                        await self.config.user(user).download_allowed()
                )

        async def _arr_call(
                self,
                kind: str,
                path: str,
                *,
                body: dict[str, Any] | None = None,
                params: dict[str, str] | None = None,
                method: str | None = None,
        ) -> Any:  # noqa: ANN401 (ARR JSON resources)
                """Call ARR without exposing private error bodies."""
                service = {
                        "movie": "radarr",
                        "tv": "sonarr",
                        "music": "lidarr",
                }.get(kind)
                if service is None:
                        msg = "Choose movie or TV show."
                        raise PreloadError(msg)
                tokens = await self.bot.get_shared_api_tokens(service)
                if not tokens.get("url") or not tokens.get("api_key"):
                        msg = f"{service} integration is not configured."
                        raise PreloadError(msg)
                lookup = (
                        body is None
                        and method in {None, "GET"}
                        and path
                        in {
                                "movie/lookup",
                                "series/lookup",
                                "album/lookup",
                        }
                )
                timeout = httpx.Timeout(30.0, read=60.0 if lookup else 30.0)
                async with httpx.AsyncClient(
                        base_url=tokens["url"].rstrip("/")
                        + ("/api/v1/" if kind == "music" else "/api/v3/"),
                        headers={"X-Api-Key": tokens["api_key"]},
                        timeout=timeout,
                ) as client:
                        response = await self._arr_response(
                                client,
                                service,
                                path,
                                method or ("GET" if body is None else "POST"),
                                body,
                                params,
                                lookup=lookup,
                        )
                if response.status_code >= 400:  # noqa: PLR2004
                        msg = f"{service} answered HTTP {response.status_code}."
                        raise PreloadError(msg)
                if response.status_code == 204 or not response.content:
                        return None
                try:
                        return response.json()
                except ValueError:
                        msg = f"{service} returned an invalid response."
                        raise PreloadError(msg) from None

        async def _arr_response(
                self,
                client: httpx.AsyncClient,
                service: str,
                path: str,
                method: str,
                body: dict[str, Any] | None,
                params: dict[str, str] | None,
                *,
                lookup: bool,
        ) -> httpx.Response:
                """Retry only a catalogue read, never an uncertain mutation."""
                for attempt in range(2 if lookup else 1):
                        try:
                                return await client.request(
                                        method, path, params=params, json=body
                                )
                        except httpx.ReadTimeout:
                                if lookup and attempt == 0:
                                        await asyncio.sleep(1)
                                        continue
                                if lookup:
                                        msg = f"{service} catalogue lookup "
                                        msg += "timed out. Try again shortly."
                                else:
                                        msg = f"{service} request timed out. "
                                        msg += "Check status before retrying."
                                raise PreloadError(msg) from None
                        except httpx.HTTPError:
                                msg = f"{service} could not be reached."
                                raise PreloadError(msg) from None
                msg = f"{service} catalogue lookup did not finish."
                raise PreloadError(msg)

        async def acquisition_search(
                self, kind: str, query: str
        ) -> list[dict[str, Any]]:
                """Look up titles without triggering any downloads."""
                endpoint = "movie" if kind == "movie" else "series"
                return await self._arr_call(
                        kind, f"{endpoint}/lookup", params={"term": query}
                )

        async def _existing_title(
                self,
                kind: str,
                selected: dict[str, Any],
        ) -> dict[str, Any] | None:
                """Match the selected catalogue ID against managed media."""
                endpoint = "movie" if kind == "movie" else "series"
                key = "tmdbId" if kind == "movie" else "tvdbId"
                titles = await self._arr_call(kind, endpoint)
                return next(
                        (t for t in titles if t.get(key) == selected.get(key)),
                        None,
                )

        async def acquisition_status(
                self, kind: str, title: dict[str, Any]
        ) -> str:
                """Read progress without requiring download permission."""
                key = "movieId" if kind == "movie" else "seriesId"
                params = {key: str(title["id"])}
                if kind == "tv":
                        params["includeEpisode"] = "true"
                try:
                        records = await self._arr_call(
                                kind, "queue/details", params=params
                        )
                except PreloadError:
                        return (
                                "Already requested; download status is "
                                "temporarily unavailable. No new "
                                "download was started."
                        )
                return describe_downloads(kind, title, records)

        async def _show_download_status(
                self,
                ctx: commands.Context,
                query: str,
                *,
                series_only: bool,
        ) -> bool:
                """Find managed titles missing playable library media."""
                kinds = ["tv"] if series_only else ["movie", "tv"]
                found: list[tuple[str, dict[str, Any]]] = []
                term = normalized_title(query)
                for kind in kinds:
                        endpoint = "movie" if kind == "movie" else "series"
                        try:
                                titles = await self._arr_call(kind, endpoint)
                        except PreloadError:
                                continue
                        for title in titles:
                                label = normalized_title(catalogue_label(title))
                                if term and term in label:
                                        found.append((kind, title))
                if not found:
                        return False
                exact = [
                        p
                        for p in found
                        if term
                        in {
                                normalized_title(str(p[1]["title"])),
                                normalized_title(catalogue_label(p[1])),
                        }
                ]
                if len(exact) == 1:
                        found = exact
                if len(found) > 1:
                        found = found[:24]
                        choice = await self._download_choice(
                                ctx,
                                "Which requested title do you mean?",
                                [
                                        (
                                                str(i),
                                                catalogue_label(t)
                                                + f" · {kind}",
                                        )
                                        for i, (kind, t) in enumerate(found)
                                ],
                        )
                        if choice is None:
                                return True
                        kind, title = found[int(choice)]
                else:
                        kind, title = found[0]
                await ctx.send(
                        await self.acquisition_status(kind, title),
                        allowed_mentions=discord.AllowedMentions.none(),
                )
                return True

        async def acquire(
                self,
                user: Person,
                kind: str,
                title: dict[str, Any],
                quality: str,
                *,
                season: int | None = None,
                expected_delete: set[int] | None = None,
        ) -> str:
                """Check permission and add the chosen title or season."""
                if kind == "tv":
                        return await SeasonManager(self).acquire(
                                user, title, quality, season, expected_delete
                        )
                if not await self.can_download(user):
                        msg = "Download permission is required."
                        raise PermissionError(msg)
                profile_name = {
                        "1080p": "Doge - 1080p default",
                        "4k": "Doge - 4K optional",
                }.get(quality)
                if profile_name is None:
                        msg = "Choose 1080p or 4K."
                        raise PreloadError(msg)
                async with self._acquisition_lock:
                        endpoint = "movie" if kind == "movie" else "series"
                        id_key = "tmdbId" if kind == "movie" else "tvdbId"
                        if (
                                not isinstance(title.get(id_key), int)
                                or title[id_key] <= 0
                        ):
                                msg = "The selected title has no catalogue ID."
                                raise PreloadError(msg)
                        existing = await self._existing_title(kind, title)
                        if existing is not None:
                                return await self.acquisition_status(
                                        kind, existing
                                )
                        profiles = await self._arr_call(kind, "qualityprofile")
                        profile = next(
                                (
                                        p
                                        for p in profiles
                                        if p["name"] == profile_name
                                ),
                                None,
                        )
                        root = (
                                "/arr/library/Movies"
                                if kind == "movie"
                                else "/arr/library/TV"
                        )
                        roots = await self._arr_call(kind, "rootfolder")
                        if profile is None or not any(
                                r["path"] == root and r.get("accessible", True)
                                for r in roots
                        ):
                                msg = (
                                        "The selected quality profile "
                                        "or library root is "
                                        "unavailable."
                                )
                                raise PreloadError(msg)
                        body = dict(title)
                        body.pop("id", None)
                        body.update(
                                qualityProfileId=profile["id"],
                                rootFolderPath=root,
                                monitored=True,
                        )
                        body.update(
                                minimumAvailability="released",
                                addOptions={"searchForMovie": True},
                        )
                        # Recheck after HTTP calls in case access was revoked.
                        if not await self.can_download(user):
                                msg = "Download permission was revoked."
                                raise PermissionError(msg)
                        await self._arr_call(kind, endpoint, body=body)
                name = discord.utils.escape_markdown(str(title["title"]))
                return (
                        f"Requested **{name}** in **{quality.upper()}**. "
                        "ARR will search; availability is not guaranteed. "
                        "Imports appear in Jellyfin after processing."
                )

        async def _download_choice(
                self,
                ctx: commands.Context,
                text: str,
                options: list[tuple[str, str]],
        ) -> str | None:
                view = PickDownloadOption(ctx.author, options)
                prompt = await ctx.send(
                        text,
                        view=view,
                        allowed_mentions=discord.AllowedMentions.none(),
                )
                if await view.wait():
                        await _timed_out(prompt)
                        return None
                return view.chosen

        async def _choose_requested_season(
                self,
                ctx: commands.Context,
                title: dict[str, Any],
        ) -> int | None:
                """Ask for one regular season, paginating long-running shows."""
                numbers = sorted(
                        {
                                s["seasonNumber"]
                                for s in title.get("seasons", [])
                                if s["seasonNumber"] > 0
                        }
                )
                if not numbers:
                        await ctx.send(
                                "No regular seasons are listed for this show."
                        )
                        return None
                page = 0
                while True:
                        options = [
                                (str(n), f"Season {n}")
                                for n in numbers[page : page + 22]
                        ]
                        if page:
                                options.append(("previous", "Previous seasons"))
                        if page + 22 < len(numbers):
                                options.append(("next", "More seasons"))
                        chosen = await self._download_choice(
                                ctx,
                                "Which season would you like to keep?",
                                options,
                        )
                        if chosen is None:
                                return None
                        if chosen == "next":
                                page += 22
                        elif chosen == "previous":
                                page -= 22
                        else:
                                return int(chosen)

        async def _season_request_plan(
                self,
                ctx: commands.Context,
                title: dict[str, Any],
        ) -> tuple[int | None, SeasonPlan | None, str]:
                """Preview a season switch before quality confirmation."""
                season = await self._choose_requested_season(ctx, title)
                if season is None:
                        return None, None, ""
                preview = await SeasonManager(self).preview(
                        ctx.author, title, season
                )
                scope = f"Request season {season}. "
                if preview.protected:
                        scope += (
                                "Complete archive: existing seasons are"
                                " protected. "
                        )
                elif preview.remove:
                        removed = ", ".join(
                                f"season {n}" for n in sorted(preview.remove)
                        )
                        scope += (
                                f"This will delete {removed} "
                                "if nobody else requests them. "
                        )
                scope += "TV quality applies to this show. "
                return season, preview, scope

        async def _request_allowed(
                self, ctx: commands.Context, query: str
        ) -> bool:
                """Check permission and title before looking anything up."""
                if not await self.can_download(ctx.author):
                        await ctx.send(
                                "Download permission is "
                                "required. Ask the bot owner to"
                                " grant it."
                        )
                        return False
                if len(query.strip()) < 2:  # noqa: PLR2004
                        await ctx.send(
                                f"Use `{ctx.clean_prefix}request <title>`."
                        )
                        return False
                return True

        @commands.group(name="request", invoke_without_command=True)
        async def request(
                self, ctx: commands.Context, *, query: str = ""
        ) -> None:
                """Request a movie/show: type, title, then quality.

                Owner permission is required. TV asks for one season per user;
                seasons selected by other users remain available.
                """
                if not await self._request_allowed(ctx, query):
                        return
                kind = await self._download_choice(
                        ctx,
                        "Is this a movie or TV show?",
                        [("movie", "Movie"), ("tv", "TV show")],
                )
                if kind is None:
                        return
                try:
                        async with ctx.typing():
                                titles = await self.acquisition_search(
                                        kind, query.strip()
                                )
                        titles = [
                                t
                                for t in titles
                                if t.get(
                                        "tmdbId"
                                        if kind == "movie"
                                        else "tvdbId"
                                )
                        ][:24]
                        if not titles:
                                await ctx.send(
                                        "No matching titles. "
                                        "Try a more specific "
                                        "name or year."
                                )
                                return
                        chosen = await self._download_choice(
                                ctx,
                                (
                                        "Choose the matching title "
                                        "(search again with a year if "
                                        "needed):"
                                ),
                                [
                                        (
                                                str(i),
                                                catalogue_label(t),
                                        )
                                        for i, t in enumerate(titles)
                                ],
                        )
                        if chosen is None:
                                return
                        title = titles[int(chosen)]
                        existing = await self._existing_title(kind, title)
                        if existing is not None and kind == "movie":
                                await self._subscribe_request(
                                        ctx, kind, existing, None
                                )
                                await ctx.send(
                                        await self.acquisition_status(
                                                kind, existing
                                        ),
                                        allowed_mentions=discord.AllowedMentions.none(),
                                )
                                return
                        season = None
                        preview = None
                        scope = ""
                        if kind == "tv":
                                (
                                        season,
                                        preview,
                                        scope,
                                ) = await self._season_request_plan(ctx, title)
                                if season is None:
                                        return
                        label = discord.utils.escape_markdown(
                                catalogue_label(title)
                        )
                        quality = await self._download_choice(
                                ctx,
                                f"**{label}** — "
                                + scope
                                + (
                                        "Choose a quality to confirm "
                                        "the download request:"
                                ),
                                [("1080p", "1080p (default)"), ("4k", "4K")],
                        )
                        if quality is None:
                                return
                        async with ctx.typing():
                                result = await self.acquire(
                                        ctx.author,
                                        kind,
                                        title,
                                        quality,
                                        season=season,
                                        expected_delete=set(preview.remove)
                                        if preview
                                        else None,
                                )
                        await self._subscribe_request(ctx, kind, title, season)
                        await ctx.send(
                                result,
                                allowed_mentions=discord.AllowedMentions.none(),
                        )
                except (PreloadError, PermissionError, SeasonError) as exc:
                        await ctx.send(f"Request not submitted: {exc}")

        async def _require_download(self, user: Person) -> None:
                """Recheck download permission immediately before submission."""
                if not await self.can_download(user):
                        message = "Download permission was removed."
                        raise PermissionError(message)

        async def _subscribe_request(
                self,
                ctx: commands.Context,
                kind: str,
                title: dict[str, Any],
                season: int | None,
        ) -> None:
                """Track requested titles and seasons until import."""
                foreign_id = title["tmdbId" if kind == "movie" else "tvdbId"]
                data = {
                        "kind": kind,
                        "foreign_id": foreign_id,
                        "title": title["title"],
                }
                if kind == "tv":
                        data["season"] = season
                await self.background().register(
                        ctx, f"{kind}:{foreign_id}:{season}", data
                )

        @request.command(name="allow")
        @commands.is_owner()
        async def request_allow(
                self, ctx: commands.Context, user: discord.User
        ) -> None:
                """Bot owner: grant a person download permission."""
                if not await self.bot.is_owner(ctx.author):
                        await ctx.send(
                                "Only the bot owner can change "
                                "download permissions."
                        )
                        return
                await self.config.user(user).download_allowed.set(True)
                await ctx.send(
                        f"Download permission granted to user `{user.id}`."
                )

        @request.command(name="revoke")
        @commands.is_owner()
        async def request_revoke(
                self, ctx: commands.Context, user: discord.User
        ) -> None:
                """Bot owner: remove a person's explicit grant."""
                if not await self.bot.is_owner(ctx.author):
                        await ctx.send(
                                "Only the bot owner can change "
                                "download permissions."
                        )
                        return
                await self.config.user(user).download_allowed.clear()
                await ctx.send(
                        f"Download permission revoked for user `{user.id}`. "
                        "Bot owners retain access."
                )

        @commands.group(
                name="importplaylist",
                aliases=["spotifyimport"],
                invoke_without_command=True,
        )
        @commands.cooldown(1, 15, commands.BucketType.user)
        async def playlist(
                self, ctx: commands.Context, *, url: str = ""
        ) -> None:
                """Import a public Spotify playlist via Lidarr.

                Link your Jellyfin account first. Download permission matches
                owner-managed permission as `request`. Public snapshots may stop
                at100songs, in which case the visible subset needs confirmation.
                """
                jobs = self.background()
                if not await self.can_download(ctx.author):
                        await ctx.send(
                                "Download permission is required; ask "
                                "the owner to use `request allow`."
                        )
                        return
                linked = await self.config.user(ctx.author).jellyfin_user()
                if not linked:
                        await ctx.send(
                                "First link your Jellyfin account with "
                                "`watch link <name>`."
                        )
                        return
                try:
                        source_id = playlist_id(url)
                        key = f"{ctx.author.id}:playlist:{source_id}"
                        previous = (await self.config.pending_jobs()).get(key)
                        if previous and previous.get("mutation"):
                                await ctx.send(
                                        "An earlier write needs owner "
                                        "verification before reimporting. "
                                        "Use `importplaylist status`."
                                )
                                return
                        if previous and not previous.get("notified"):
                                await ctx.send(
                                        "That playlist import is "
                                        "already running. Use "
                                        "`importplaylist status`."
                                )
                                return

                        async def tokens() -> dict[str, str]:
                                return await self.bot.get_shared_api_tokens(
                                        "jellyfin"
                                )

                        user_id = await Jellyfin(tokens).user(linked)
                        async with httpx.AsyncClient() as client:
                                snapshot = await SpotifyPublic(client).snapshot(
                                        url
                                )
                        name = discord.utils.escape_markdown(snapshot["name"])
                        username = discord.utils.escape_markdown(
                                ctx.author.name
                        )
                        count = len(snapshot["tracks"])
                        partial = snapshot["partial"]
                        warning = (
                                (
                                        f"Spotify exposes only the visible "
                                        f"{count} songs; this playlist may be "
                                        f"incomplete. "
                                )
                                if partial
                                else ""
                        )
                        option = "visible" if partial else "import"
                        choice = await self._download_choice(
                                ctx,
                                warning
                                + (
                                        f"Import **{name}** ({count} songs)? "
                                        f"Missing songs request their albums "
                                        f"in Lidarr. The Jellyfin playlist "
                                        f"will be public and named "
                                        f"**{username} - {name}**."
                                ),
                                [
                                        (
                                                option,
                                                f"Import visible {count} only"
                                                if partial
                                                else "Import playlist",
                                        )
                                ],
                        )
                        if choice != option:
                                return
                        await self._require_download(ctx.author)
                        key = await jobs.register(
                                ctx,
                                f"playlist:{source_id}",
                                {
                                        "kind": "playlist",
                                        "stage": "preparing",
                                        "source_id": source_id,
                                        "name": snapshot["name"],
                                        "tracks": snapshot["tracks"],
                                        "partial": partial,
                                        "user_id": user_id,
                                },
                        )
                        await ctx.send(
                                "Playlist import started. I'll ping "
                                "you when the songs are in Jellyfin; "
                                "`importplaylist status` shows "
                                "progress."
                        )
                        self._spawn(jobs.run_once(key))
                except (PreloadError, ValueError, PermissionError) as exc:
                        await ctx.send(
                                f"Playlist import not started: {exc}",
                                allowed_mentions=discord.AllowedMentions.none(),
                        )

        @playlist.command(name="status")
        async def playlist_status(self, ctx: commands.Context) -> None:
                """Show your imports and songs needing review."""
                self.background()
                own = [
                        j
                        for j in (await self.config.pending_jobs()).values()
                        if j.get("owner") == ctx.author.id
                        and j.get("kind") == "playlist"
                ]
                if not own:
                        await ctx.send("You have no playlist import jobs.")
                        return
                lines = []
                needs_retry = False
                missing = False
                for job in own[-5:]:
                        name = discord.utils.escape_markdown(job["name"])
                        ready = job.get("ready", 0)
                        count = len(job["tracks"])
                        lines.append(
                                f"**{name}** · {job['stage']} · "
                                f"{ready}/{count} songs ready"
                        )
                        reviewed = [
                                r
                                for r in job["tracks"]
                                if r.get("review") and not r.get("skipped")
                        ]
                        skipped = sum(
                                1 for r in job["tracks"] if r.get("skipped")
                        )
                        lines.extend(review_summary(reviewed))
                        if skipped:
                                lines.append(
                                        f"{skipped} song(s) skipped at your "
                                        "request."
                                )
                        if job.get("mutation"):
                                lines.append(
                                        "An earlier write needs owner "
                                        "verification before retrying."
                                )
                        if reviewed:
                                needs_retry = True
                                missing = any(
                                        r.get("review") == "Artist not in "
                                        "MusicBrainz"
                                        for r in reviewed
                                )
                prefix = ctx.clean_prefix
                hints = []
                if needs_retry and missing:
                        hints.append(
                                f"For artists MusicBrainz lacks, use "
                                f"`{prefix}importplaylist youtube <song>` or "
                                f"`{prefix}importplaylist ignore <song>`."
                        )
                if needs_retry:
                        hints.append(
                                f"Use `{prefix}importplaylist retry <name>` "
                                "to try reviewed songs again."
                        )
                hint = "\n".join(hints)
                body = "\n".join(lines)
                if hint:
                        body = body[: 1900 - len(hint) - 1]
                await ctx.send(
                        "\n".join(filter(None, (body, hint)))[:1900],
                        allowed_mentions=discord.AllowedMentions.none(),
                )

        @staticmethod
        def _reviewed_tracks(
                user_id: int,
                query: str,
                pending: dict[str, Any],
        ) -> list[tuple[str, int, dict[str, Any], dict[str, Any]]]:
                """Find the caller's reviewed songs matching a query."""
                found = []
                for key, job in pending.items():
                        if (
                                job.get("owner") != user_id
                                or job.get("kind") != "playlist"
                        ):
                                continue
                        for index, track in enumerate(job.get("tracks", [])):
                                haystack = (
                                        f"{track.get('title', '')} "
                                        f"{track.get('artist', '')}"
                                ).casefold()
                                if (
                                        track.get("review")
                                        and not track.get("skipped")
                                        and query in haystack
                                ):
                                        found.append((key, index, job, track))
                return found

        @staticmethod
        def _review_label(job: dict[str, Any], track: dict[str, Any]) -> str:
                """Describe one reviewed song in a menu."""
                title = track.get("title", "?")
                artist = track.get("artist", "?")
                name = job.get("name", "playlist")
                return f"{title} · {artist} · {name}"

        async def _reviewed_choice(
                self, ctx: commands.Context, query: str
        ) -> tuple[str, int, dict[str, Any], dict[str, Any]] | None:
                """Choose one of the caller's reviewed songs by ``query``."""
                found = self._reviewed_tracks(
                        ctx.author.id,
                        query,
                        await self.config.pending_jobs(),
                )
                if not found:
                        await ctx.send(f"No reviewed song matches `{query}`.")
                        return None
                if len(found) == 1:
                        return found[0]
                pick = await self._download_choice(
                        ctx,
                        f"{len(found)} reviewed songs match:",
                        [
                                (str(i), self._review_label(job, track))
                                for i, (_key, _index, job, track) in (
                                        enumerate(found[:24])
                                )
                        ],
                )
                return None if pick is None else found[int(pick)]

        async def _youtube_choice(
                self, ctx: commands.Context, jobs: Jobs, track: dict[str, Any]
        ) -> str | None:
                """Let the requester choose from matching YouTube videos."""
                title = discord.utils.escape_markdown(
                        str(track.get("title", "song"))
                )
                try:
                        results = await jobs.youtube().search(
                                f"{track.get('title', '')} "
                                f"{track.get('artist', '')}".strip()
                        )
                except YoutubeError as exc:
                        await ctx.send(f"YouTube search failed: {exc}")
                        return None
                if not results:
                        await ctx.send(
                                f"YouTube found no video for **{title}**. "
                                f"Paste a link with "
                                f"`{ctx.clean_prefix}importplaylist youtube "
                                f"{title} <url>`.",
                                allowed_mentions=discord.AllowedMentions.none(),
                        )
                        return None
                return await self._download_choice(
                        ctx,
                        f"Which video is **{title}**?",
                        [
                                (str(video["url"]), video_label(video))
                                for video in results
                        ],
                )

        async def _lidarr_album(
                self,
                ctx: commands.Context,
                track: dict[str, Any],
                album_query: str = "",
        ) -> dict[str, Any] | None:
                """Ask the requester which Lidarr album a song belongs to."""
                title = discord.utils.escape_markdown(
                        str(track.get("title", "song"))
                )
                term = (
                        album_query
                        or (
                                f"{track.get('title', '')} "
                                f"{track.get('artist', '')}"
                        ).strip()
                )
                try:
                        candidates = await self._arr_call(
                                "music", "album/lookup", params={"term": term}
                        )
                except PreloadError as exc:
                        await ctx.send(
                                f"Lidarr lookup failed: {exc}",
                                allowed_mentions=(
                                        discord.AllowedMentions.none()
                                ),
                        )
                        return None
                albums: dict[str, dict[str, Any]] = {}
                for album in candidates or []:
                        if isinstance(album, dict) and album.get(
                                "foreignAlbumId"
                        ):
                                albums.setdefault(
                                        album["foreignAlbumId"], album
                                )
                if not albums:
                        await ctx.send(
                                f"Lidarr has no album match for **{title}**.",
                                allowed_mentions=(
                                        discord.AllowedMentions.none()
                                ),
                        )
                        return None
                choice = await self._download_choice(
                        ctx,
                        f"Which album contains **{title}**?",
                        [
                                (album_id, album_choice_label(album))
                                for album_id, album in list(albums.items())[:24]
                        ],
                )
                return None if choice is None else albums[choice]

        @playlist.command(name="match")
        async def playlist_match(
                self, ctx: commands.Context, *, song: str = ""
        ) -> None:
                """Choose a Lidarr album with an optional album search term."""
                if not await self.can_download(ctx.author):
                        await ctx.send(
                                "Download permission is required; ask "
                                "the owner to use `request allow`."
                        )
                        return
                track_query, separator, album_query = song.partition(" | ")
                query = track_query.strip().casefold()
                album_query = album_query.strip()
                if len(query) < 2 or (separator and not album_query):  # noqa: PLR2004
                        await ctx.send(
                                f"Use `{ctx.clean_prefix}importplaylist "
                                "match <song title> [ | <album and artist>]`."
                        )
                        return
                jobs = self.background()
                choice = await self._reviewed_choice(ctx, query)
                if choice is None:
                        return
                key, index, job, track = choice
                if job.get("mutation"):
                        await ctx.send(
                                "An earlier write needs owner verification "
                                "before matching. Use `importplaylist verify`."
                        )
                        return
                if key in jobs.active:
                        await ctx.send(
                                "That import is still being checked; try "
                                "again in a minute."
                        )
                        return
                album = await self._lidarr_album(ctx, track, album_query)
                if album is None:
                        return
                title = discord.utils.escape_markdown(
                        str(track.get("title", "song"))
                )
                try:
                        name = await jobs.assign_album(key, index, album)
                except (PreloadError, PermissionError) as exc:
                        await ctx.send(f"Couldn't assign that album: {exc}")
                        return
                await ctx.send(
                        f"Monitoring **{discord.utils.escape_markdown(name)}** "
                        f"in Lidarr. **{title}** will join the playlist when "
                        "it downloads; `importplaylist status` tracks it.",
                        allowed_mentions=discord.AllowedMentions.none(),
                )
                self._spawn(jobs.run_once(key))

        @playlist.command(name="youtube")
        async def playlist_youtube(
                self, ctx: commands.Context, *, song: str = ""
        ) -> None:
                """Fetch a reviewed song from YouTube (or a link you paste)."""
                if not await self.can_download(ctx.author):
                        await ctx.send(
                                "Download permission is required; ask "
                                "the owner to use `request allow`."
                        )
                        return
                url = youtube_url(song)
                query = song.replace(url, " ") if url else song
                query = query.strip().casefold()
                if len(query) < 2:  # noqa: PLR2004 (a useful search)
                        await ctx.send(
                                f"Use `{ctx.clean_prefix}importplaylist "
                                "youtube <song>` (`youtube <song> <link>` to "
                                "choose the video yourself)."
                        )
                        return
                jobs = self.background()
                choice = await self._reviewed_choice(ctx, query)
                if choice is None:
                        return
                key, index, job, track = choice
                if job.get("mutation"):
                        await ctx.send(
                                "An earlier write needs owner verification "
                                "before fetching. Use `importplaylist verify`."
                        )
                        return
                if key in jobs.active:
                        await ctx.send(
                                "That import is still being checked; try "
                                "again in a minute."
                        )
                        return
                title = discord.utils.escape_markdown(
                        str(track.get("title", "song"))
                )
                problem = await jobs.youtube().ready()
                if problem is not None:
                        await ctx.send(
                                "The YouTube fallback is not available: "
                                f"{problem}. The song stays in review."
                        )
                        return
                if url is None:
                        picked = await self._youtube_choice(ctx, jobs, track)
                        if picked is None:
                                return
                        url = picked
                await ctx.send(
                        f"Downloading **{title}** from YouTube...",
                        allowed_mentions=discord.AllowedMentions.none(),
                )
                self._spawn(jobs.fetch_youtube(key, index, url))

        @playlist.command(name="ignore")
        async def playlist_ignore(
                self, ctx: commands.Context, *, song: str = ""
        ) -> None:
                """Skip a reviewed song permanently, without retrying it."""
                if not await self.can_download(ctx.author):
                        await ctx.send(
                                "Download permission is required; ask "
                                "the owner to use `request allow`."
                        )
                        return
                query = song.strip().casefold()
                if len(query) < 2:  # noqa: PLR2004 (a useful search)
                        await ctx.send(
                                f"Use `{ctx.clean_prefix}importplaylist "
                                "ignore <song>`."
                        )
                        return
                jobs = self.background()
                choice = await self._reviewed_choice(ctx, query)
                if choice is None:
                        return
                key, index, _job, track = choice
                try:
                        name = await jobs.skip(key, index)
                except (PreloadError, PermissionError) as exc:
                        await ctx.send(f"Couldn't skip that song: {exc}")
                        return
                await ctx.send(
                        f"Skipped **{discord.utils.escape_markdown(name)}**; "
                        "it won't be retried or counted again.",
                        allowed_mentions=discord.AllowedMentions.none(),
                )
                self._spawn(jobs.run_once(key))

        @playlist.command(name="retry")
        async def playlist_retry(
                self, ctx: commands.Context, *, target: str = ""
        ) -> None:
                """Retry songs from your imports that needed review.

                Reviewed or unresolved songs are looked up again; songs whose
                albums are already matched keep their progress.
                """
                if not await self.can_download(ctx.author):
                        await ctx.send(
                                "Download permission is required; ask "
                                "the owner to use `request allow`."
                        )
                        return
                jobs = self.background()
                needle = target.strip().casefold()
                reply = ""
                started = False
                async with self.config.pending_jobs() as pending:
                        own = [
                                (key, job)
                                for key, job in pending.items()
                                if job.get("owner") == ctx.author.id
                                and job.get("kind") == "playlist"
                                and any(
                                        (
                                                not track.get("resolved")
                                                or track.get("review")
                                        )
                                        and not track.get("skipped")
                                        for track in job.get("tracks", [])
                                )
                                and (
                                        not needle
                                        or needle
                                        in str(job.get("name", "")).casefold()
                                )
                        ]
                        if not own:
                                reply = "No songs in your imports need review."
                        elif len(own) > 1:
                                names = [
                                        discord.utils.escape_markdown(
                                                str(job.get("name", key))
                                        )
                                        for key, job in own[:10]
                                ]
                                listing = "\n".join(
                                        f"- **{name}**" for name in names
                                )
                                reply = (
                                        "Which import? Name one: "
                                        f"`{ctx.clean_prefix}importplaylist "
                                        f"retry <name>`\n{listing}"
                                )
                        else:
                                key, job = own[0]
                                if job.get("mutation"):
                                        reply = (
                                                "An earlier write needs owner "
                                                "verification before "
                                                "retrying. Use "
                                                "`importplaylist verify`."
                                        )
                                elif key in jobs.active:
                                        reply = (
                                                "That import is still being "
                                                "checked; try again in a "
                                                "minute."
                                        )
                                else:
                                        rows = [
                                                track
                                                for track in job["tracks"]
                                                if (
                                                        not track.get(
                                                                "resolved"
                                                        )
                                                        or track.get("review")
                                                )
                                                and not track.get("skipped")
                                        ]
                                        for row in rows:
                                                for field in (
                                                        "review",
                                                        "resolved",
                                                        "album_id",
                                                        "item_id",
                                                        "attempts",
                                                ):
                                                        row.pop(field, None)
                                        job["albums"] = {
                                                album: match
                                                for album, match in (
                                                        job.get("albums") or {}
                                                ).items()
                                                if match is not None
                                        }
                                        job["stage"] = "preparing"
                                        job["notified"] = False
                                        job.pop("finished", None)
                                        job["created"] = datetime.now(
                                                UTC
                                        ).timestamp()
                                        name = discord.utils.escape_markdown(
                                                str(
                                                        job.get(
                                                                "name",
                                                                "playlist",
                                                        )
                                                )
                                        )
                                        reply = (
                                                f"Retrying {len(rows)} song(s) "
                                                f"in **{name}**..."
                                        )
                                        started = True
                await ctx.send(
                        reply,
                        allowed_mentions=discord.AllowedMentions.none(),
                )
                if started:
                        self._spawn(jobs.run_once(key))

        @playlist.command(name="verify")
        @commands.is_owner()
        async def playlist_verify(
                self, ctx: commands.Context, *, target: str = ""
        ) -> None:
                """Bot owner: clear an uncertain playlist write."""
                if not await self.bot.is_owner(ctx.author):
                        await ctx.send(
                                "Only the bot owner can verify playlist writes."
                        )
                        return
                async with self.config.pending_jobs() as jobs:
                        flagged = [
                                (key, job)
                                for key, job in jobs.items()
                                if job.get("kind") == "playlist"
                                and job.get("mutation")
                        ]
                        if not flagged:
                                await ctx.send(
                                        "No playlist jobs need owner "
                                        "verification."
                                )
                                return
                        needle = target.strip().lower()
                        matches = [
                                (key, job)
                                for key, job in flagged
                                if not needle
                                or needle in key.lower()
                                or needle in str(job.get("name", "")).lower()
                        ]
                        if not matches:
                                await ctx.send(
                                        "No flagged playlist job matched "
                                        f"`{target.strip()}`."
                                )
                                return
                        if len(matches) > 1:
                                listing = "\n".join(
                                        f"- `{key}` "
                                        f"({job.get('name', 'unknown')}): "
                                        f"{job.get('mutation')}"
                                        for key, job in matches[:10]
                                )
                                await ctx.send(
                                        "Multiple playlist jobs need "
                                        "verification; name one:\n"
                                        f"{listing}",
                                        allowed_mentions=(
                                                discord.AllowedMentions.none()
                                        ),
                                )
                                return
                        key, job = matches[0]
                        action = job.pop("mutation", None)
                        name = discord.utils.escape_markdown(
                                str(job.get("name", key))
                        )
                await ctx.send(
                        f"Verified **{name}**: cleared `{action}`. "
                        "You can reimport or resume the playlist."
                )

        @watch.command(name="link")
        async def watch_link(self, ctx: commands.Context, *, name: str) -> None:
                """Link your Jellyfin user name, for "next" requests."""
                await self.config.user(ctx.author).jellyfin_user.set(
                        name.strip()
                )
                await ctx.send(
                        f"Linked to Jellyfin user **{name.strip()}**. "
                        f"`{ctx.clean_prefix}watch <show> next` now uses your "
                        "watch progress."
                )

        @watch.command(name="unlink")
        async def watch_unlink(self, ctx: commands.Context) -> None:
                """Forget your Jellyfin user name."""
                await self.config.user(ctx.author).jellyfin_user.clear()
                await ctx.send("Unlinked.")

        @watch.command(name="status")
        async def watch_status(
                self, ctx: commands.Context, request_id: int | None = None
        ) -> None:
                """Show one request, or the latest few."""
                try:
                        if request_id is not None:
                                request = await self.fetch(request_id)
                                await ctx.send(embed=self._embed(request))
                                return
                        client = await self._client()
                        async with client as c:
                                requests = _unwrap(
                                        await list_requests.asyncio_detailed(
                                                client=c, limit=5
                                        )
                                )
                except (PreloadError, httpx.HTTPError) as exc:
                        await ctx.send(f"Couldn't get that: {exc}")
                        return
                if not requests:
                        await ctx.send("No preloads yet.")
                        return
                lines = [
                        f"`#{r.id}` **{r.title}** · {r.status.value} · "
                        f"{r.requested_by}"
                        for r in requests
                ]
                await ctx.send("\n".join(lines))

        @watch.command(name="cancel")
        async def watch_cancel(
                self, ctx: commands.Context, request_id: int
        ) -> None:
                """Cancel the queued files of a request."""
                try:
                        request = await self.fetch(request_id)
                        mine = (
                                request.requested_by
                                == f"discord:{ctx.author.name}"
                        )
                        is_mod = isinstance(
                                ctx.author, discord.Member
                        ) and await self.bot.is_mod(ctx.author)
                        if not (
                                mine
                                or is_mod
                                or await self.bot.is_owner(ctx.author)
                        ):
                                await ctx.send(
                                        "Only the requester or a moderator can "
                                        "cancel that."
                                )
                                return
                        request = await self.cancel(request_id)
                except (PreloadError, httpx.HTTPError) as exc:
                        await ctx.send(f"Couldn't cancel: {exc}")
                        return
                await ctx.send(embed=self._embed(request))
