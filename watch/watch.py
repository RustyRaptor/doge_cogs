"""``d!watch``: preload a movie or episodes into the media cache.

Talks to media-preload (the home lab's preload service) through the
generated ``media_preload_client``. Files are fetched into the VPS media
cache so they stream quickly from webdav.
"""

import asyncio
import contextlib
import logging
import os
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


class PreloadError(Exception):
        """media-preload refused or failed a call."""


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
                self.config.register_user(jellyfin_user=None)
                self._pollers: set[asyncio.Task[None]] = set()

        async def cog_unload(self) -> None:
                """Stop polling."""
                for task in self._pollers:
                        task.cancel()

        async def red_delete_data_for_user(
                self, *, requester: RequestType, user_id: int
        ) -> None:
                """Forget a user's linked Jellyfin name."""
                del requester
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
                        await ctx.send(f"Couldn't start that: {exc}")
                        return
                message = await ctx.send(
                        embed=self._embed(request),
                        view=None
                        if is_finished(request)
                        else CancelView(self, request.id, ctx.author.id),
                )
                task = asyncio.create_task(self._follow(message, request))
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
                        await ctx.send(
                                f"Nothing cacheable matches **{title}**."
                                if items
                                else f"I couldn't find **{title}**."
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
