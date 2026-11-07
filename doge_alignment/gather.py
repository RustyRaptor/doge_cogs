"""Fetch what a guess needs from Discord: history pages, conversations, avatar.

1. Discord's guild message search (author filter, only channels the viewer
   and the bot can both read) gives the total and random pages of the
   person's history (:func:`.core.sampling.sample_pages`).
2. Around a couple of their messages on each page, the channel history gives
   the conversation they were in, other people included.
3. Everything becomes :class:`.core.messages.Message`; :mod:`.core.dossier`
   does the rest.

Search allows about one request per 5 seconds (a limit shared on Discord's
side), so pages are the slow part; channel history is fast.
"""

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

import discord
from discord.http import Route
from redbot.core.bot import Red

from .core.baseline import time_points
from .core.conversations import conversations_with
from .core.messages import Message, snowflake_at, snowflake_time
from .core.sampling import PAGE_SIZE, SampledPage, sample_pages

log = logging.getLogger("red.doge_cogs.doge_alignment.gather")

# Discord's search takes at most this many channel ids
SEARCH_CHANNELS = 500
# Search returns code 110000 while it indexes a server; retry this often
SEARCH_RETRIES = 3
# Conversations fetched per sampled page, and messages around each
CONTEXTS_PER_PAGE = 2
CONTEXT_LIMIT = 60

Progress = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class History:
        """What was fetched about someone."""

        total: int
        pages: tuple[SampledPage[Message], ...]
        contexts: dict[int, tuple[Message, ...]]


def _int(value: object) -> int:
        return len(value) if isinstance(value, list) else 0


def message_from_json(
        data: Mapping[str, object], channel_names: Mapping[str, str]
) -> Message:
        """Convert a message from Discord's REST API."""
        author = data.get("author")
        author = author if isinstance(author, dict) else {}
        reference = data.get("message_reference")
        reference = reference if isinstance(reference, dict) else {}
        message_id = str(data.get("id", "0"))
        channel_id = str(data.get("channel_id", ""))
        stamp = data.get("timestamp")
        created = (
                datetime.fromisoformat(stamp)
                if isinstance(stamp, str)
                else snowflake_time(message_id)
        )
        return Message(
                id=message_id,
                author_id=str(author.get("id", "")),
                author_name=str(
                        author.get("global_name")
                        or author.get("username")
                        or ""
                ),
                channel_id=channel_id,
                channel_name=channel_names.get(channel_id, "unknown"),
                created=created,
                content=str(data.get("content") or ""),
                reply_to=(
                        str(reference["message_id"])
                        if reference.get("message_id")
                        else None
                ),
                edited=bool(data.get("edited_timestamp")),
                attachments=_int(data.get("attachments")),
                mentions=_int(data.get("mentions")),
                bot=bool(author.get("bot")),
        )


def message_from_discord(message: discord.Message) -> Message:
        """Convert a discord.py message."""
        reference = message.reference
        channel = message.channel
        return Message(
                id=str(message.id),
                author_id=str(message.author.id),
                author_name=message.author.display_name,
                channel_id=str(channel.id),
                channel_name=getattr(channel, "name", None) or "unknown",
                created=message.created_at,
                content=message.content,
                reply_to=(
                        str(reference.message_id)
                        if reference and reference.message_id
                        else None
                ),
                edited=message.edited_at is not None,
                attachments=len(message.attachments),
                mentions=len(message.mentions),
                bot=message.author.bot,
        )


def search_hits(
        body: Mapping[str, object], channel_names: Mapping[str, str]
) -> tuple[Message, ...]:
        """Read a search page: hit groups whose first message is the match."""
        groups = body.get("messages")
        if not isinstance(groups, list):
                return ()
        return tuple(
                message_from_json(group[0], channel_names)
                for group in groups
                if isinstance(group, list)
                and group
                and isinstance(group[0], dict)
        )


def total_from_search(body: Mapping[str, object]) -> int:
        """Return how many messages the search matched."""
        total = body.get("total_results")
        return total if isinstance(total, int) else 0


def readable_by(
        channel: discord.abc.GuildChannel, *members: discord.Member
) -> bool:
        """Whether all ``members`` can read ``channel``'s history."""
        return all(
                channel.permissions_for(m).read_message_history
                and channel.permissions_for(m).view_channel
                for m in members
        )


def shared_channels(
        guild: discord.Guild, viewer: discord.Member
) -> list[discord.TextChannel]:
        """Text channels both the bot and the viewer can read."""
        return [
                c
                for c in guild.text_channels
                if readable_by(c, guild.me, viewer)
        ][:SEARCH_CHANNELS]


def public_channels(guild: discord.Guild) -> list[discord.TextChannel]:
        """Text channels everyone in the server, and the bot, can read."""
        return [
                c
                for c in guild.text_channels
                if readable_by(c, guild.me)
                and c.permissions_for(guild.default_role).view_channel
                and c.permissions_for(guild.default_role).read_message_history
        ][:SEARCH_CHANNELS]


def fresh(hits: tuple[Message, ...], seen: set[str]) -> tuple[Message, ...]:
        """Return ``hits`` not in ``seen``, and remember them."""
        new = tuple(h for h in hits if h.id not in seen)
        seen.update(h.id for h in new)
        return new


def anchor_ids(
        page: SampledPage[Message],
        target_id: str,
        rng: random.Random,
        count: int = CONTEXTS_PER_PAGE,
) -> tuple[Message, ...]:
        """Pick messages on ``page`` to fetch conversations around.

        One from each of up to ``count`` random separate stretches of the
        page (different channels or times), the middle message of each.
        """
        stretches = conversations_with(list(page.items), target_id, context=0)
        picked = rng.sample(stretches, min(count, len(stretches)))
        return tuple(
                stretch[len(stretch) // 2].messages[0] for stretch in picked
        )


class Gatherer:
        """Fetches a person's history for one guess."""

        def __init__(self, bot: Red, guild: discord.Guild) -> None:
                """Gather in ``guild``."""
                self.bot = bot
                self.guild = guild
                self.names = {str(c.id): c.name for c in guild.channels}

        async def search(
                self, params: list[tuple[str, str]]
        ) -> Mapping[str, object]:
                """Run one guild message search, waiting out indexing."""
                route = Route(
                        "GET",
                        "/guilds/{guild_id}/messages/search",
                        guild_id=self.guild.id,
                )
                for _ in range(SEARCH_RETRIES):
                        body = await self.bot.http.request(route, params=params)
                        if body.get("code") != 110000:  # noqa: PLR2004
                                return body
                        wait = body.get("retry_after") or 2
                        await asyncio.sleep(min(float(wait), 10))
                return {}

        async def history(
                self,
                target: discord.Member,
                viewer: discord.Member,
                pages: int,
                progress: Progress,
        ) -> History:
                """Fetch random pages of ``target``'s history, and context.

                Raises:
                        discord.HTTPException: if search isn't available.

                """
                channels = shared_channels(self.guild, viewer)
                if not channels:
                        return History(0, (), {})
                base = [
                        ("author_id", str(target.id)),
                        *(("channel_id", str(c.id)) for c in channels),
                ]
                total = total_from_search(
                        await self.search([*base, ("limit", "1")])
                )
                done = 0

                async def fetch(
                        max_id: str | None, offset: int
                ) -> tuple[Message, ...]:
                        nonlocal done
                        params = [
                                *base,
                                ("limit", str(PAGE_SIZE)),
                                ("sort_by", "timestamp"),
                                ("sort_order", "desc"),
                                ("offset", str(offset)),
                                *((("max_id", max_id),) if max_id else ()),
                        ]
                        hits = search_hits(
                                await self.search(params), self.names
                        )
                        done += 1
                        await progress(
                                f"Reading {target.display_name}'s history "
                                f"({done} of about {pages} pages)…"
                        )
                        return hits

                rng = random.SystemRandom()
                sampled = await sample_pages(fetch, total, pages, rng)
                await progress("Reading the conversations around them…")
                contexts = {
                        i: await self.context(page, str(target.id), rng)
                        for i, page in enumerate(sampled)
                }
                return History(total, sampled, contexts)

        async def context(
                self,
                page: SampledPage[Message],
                target_id: str,
                rng: random.Random,
        ) -> tuple[Message, ...]:
                """Fetch the conversations around a few messages on ``page``."""
                found: list[Message] = []
                for anchor in anchor_ids(page, target_id, rng):
                        found += await self.around(anchor)
                return tuple(found)

        async def server_sample(
                self, pages: int, progress: Progress
        ) -> tuple[int, tuple[tuple[Message, ...], ...]]:
                """Sample the whole server's public channels at random times.

                Returns the server's message count and, for each random
                moment, the page of messages before it plus the channel
                conversation around one of them.

                Raises:
                        discord.HTTPException: if search isn't available.

                """
                channels = public_channels(self.guild)
                if not channels:
                        return 0, ()
                base = [
                        ("author_type", "user"),
                        *(("channel_id", str(c.id)) for c in channels),
                ]
                total = total_from_search(
                        await self.search([*base, ("limit", "1")])
                )
                rng = random.SystemRandom()
                moments = time_points(
                        self.guild.created_at, datetime.now(UTC), pages, rng
                )
                seen: set[str] = set()
                groups: list[tuple[Message, ...]] = []
                for done, moment in enumerate(moments, 1):
                        await progress(
                                "Getting to know the server "
                                f"({done} of {pages})…"
                        )
                        body = await self.search(
                                [
                                        *base,
                                        ("limit", str(PAGE_SIZE)),
                                        ("sort_by", "timestamp"),
                                        ("sort_order", "desc"),
                                        ("max_id", str(snowflake_at(moment))),
                                ]
                        )
                        hits = fresh(search_hits(body, self.names), seen)
                        if not hits:
                                continue
                        around = await self.around(rng.choice(hits))
                        groups.append((*hits, *fresh(around, seen)))
                return total, tuple(groups)

        async def around(self, anchor: Message) -> tuple[Message, ...]:
                """Fetch the channel's messages around ``anchor``."""
                channel = self.guild.get_channel_or_thread(
                        int(anchor.channel_id)
                )
                if not isinstance(
                        channel, discord.TextChannel | discord.Thread
                ):
                        return ()
                try:
                        return tuple(
                                [
                                        message_from_discord(m)
                                        async for m in channel.history(
                                                around=discord.Object(
                                                        int(anchor.id)
                                                ),
                                                limit=CONTEXT_LIMIT,
                                        )
                                ]
                        )
                except discord.HTTPException as error:
                        log.debug("No context in %s: %s", channel, error)
                        return ()

        async def scan(
                self,
                target: discord.Member,
                viewer: discord.Member,
                channels: int = 8,
                limit: int = 200,
        ) -> History:
                """Fallback without search: recent history of busy channels."""
                busiest = sorted(
                        shared_channels(self.guild, viewer),
                        key=lambda c: c.last_message_id or 0,
                        reverse=True,
                )[:channels]
                pages: list[SampledPage[Message]] = []
                contexts: dict[int, tuple[Message, ...]] = {}
                for channel in busiest:
                        try:
                                recent = [
                                        message_from_discord(m)
                                        async for m in channel.history(
                                                limit=limit
                                        )
                                ]
                        except discord.HTTPException:
                                continue
                        own = tuple(
                                m
                                for m in recent
                                if m.author_id == str(target.id)
                        )
                        if own:
                                contexts[len(pages)] = tuple(recent)
                                pages.append(SampledPage(0, own))
                total = sum(len(p.items) for p in pages)
                return History(total, tuple(pages), contexts)
