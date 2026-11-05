"""The Discord front end: ``[p]alignment`` and ``/alignment`` commands.

Everything here is Discord glue: reading and writing Red's Config, permissions,
fetching avatars, and sending messages. The chart's rules, the YAML format and
the drawing all live in :mod:`.core`.
"""

import asyncio
import io
import logging
from collections import OrderedDict
from collections.abc import Callable, Mapping
from typing import Literal

import aiohttp
import discord
from redbot.core import Config, app_commands, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import pagify

from .core.alignments import AlignmentName, matching, parse_alignment
from .core.chart import (
        Chart,
        Entry,
        add_admin,
        clear,
        forget_user,
        may_manage,
        may_remove,
        may_set,
        place,
        remove_admin,
        replace_users,
        unplace,
        unplace_all,
)
from .core.fetch import avatar_urls, by_user, fetch_images
from .core.storage import (
        ChartFormatError,
        from_data,
        from_yaml,
        to_data,
        to_yaml,
)
from .core.view import (
        Cell,
        Lookup,
        Profile,
        absent,
        cells,
        everyone,
        format_text,
        tile_filename,
)
from .ui import ConfirmView, gallery_view

log = logging.getLogger("red.doge_cogs.doge_alignment")

RequestType = Literal["discord_deleted_user", "owner", "user", "user_strict"]
Style = Literal["image", "gallery", "text"]
STYLES: tuple[Style, ...] = ("image", "gallery", "text")

AVATAR_CACHE_SIZE = 512
MAX_IMPORT_BYTES = 1024 * 1024
NO_MENTIONS = discord.AllowedMentions.none()
BAD_ALIGNMENT = (
        "I don't know the alignment {text!r}. Use a name like "
        "`chaotic good`, or its letters: LG NG CG LN TN CN LE NE CE."
)


def avatar_url(user: discord.User | discord.Member) -> str:
        """Return a 128px PNG of ``user``'s avatar (server avatar if set)."""
        return user.display_avatar.replace(format="png", size=128).url


def member_lookup(guild: discord.Guild) -> Lookup:
        """Return a lookup of live names and avatars in ``guild``."""

        def lookup(user_id: str) -> Profile | None:
                member = guild.get_member(int(user_id))
                if member is None:
                        return None
                return Profile(member.display_name, avatar_url(member))

        return lookup


def remember(
        cache: "OrderedDict[str, bytes]",
        new: Mapping[str, bytes],
        used: frozenset[str],
        limit: int,
) -> "OrderedDict[str, bytes]":
        """Return an LRU ``cache`` with ``new`` added and ``used`` refreshed."""
        merged = OrderedDict(cache)
        merged.update(new)
        for url in used & merged.keys():
                merged.move_to_end(url)
        while len(merged) > limit:
                merged.popitem(last=False)
        return merged


class AlignmentCog(commands.Cog):
        """Keep track of everyone's D&D alignment."""

        def __init__(self, bot: Red) -> None:
                """Create the cog."""
                self.bot = bot
                self.config = Config.get_conf(
                        self, identifier=0xA119E5C4A7, force_registration=True
                )
                self.config.register_guild(
                        users={}, admins=[], default_style="image"
                )
                self._locks: dict[int, asyncio.Lock] = {}
                self._avatars: OrderedDict[str, bytes] = OrderedDict()
                self._session: aiohttp.ClientSession | None = None

        async def cog_unload(self) -> None:
                """Close the avatar download session."""
                if self._session is not None:
                        await self._session.close()

        async def red_delete_data_for_user(
                self, *, requester: RequestType, user_id: int
        ) -> None:
                """Take a user off every chart and every admin list."""
                del requester
                for guild_id in await self.config.all_guilds():
                        await self._update_id(
                                guild_id, lambda c: forget_user(c, str(user_id))
                        )

        # Storage --------------------------------------------------------

        async def _load(self, guild: discord.Guild) -> Chart:
                return from_data(await self.config.guild(guild).all())

        async def _update_id(
                self, guild_id: int, change: Callable[[Chart], Chart]
        ) -> Chart:
                """Apply ``change`` to a guild's chart and save the result."""
                lock = self._locks.setdefault(guild_id, asyncio.Lock())
                async with lock:
                        group = self.config.guild_from_id(guild_id)
                        chart = change(from_data(await group.all()))
                        data = to_data(chart)
                        await group.users.set(data["users"])
                        await group.admins.set(data["admins"])
                        return chart

        async def _update(
                self, guild: discord.Guild, change: Callable[[Chart], Chart]
        ) -> Chart:
                return await self._update_id(guild.id, change)

        # Permissions ----------------------------------------------------

        async def _privileged(self, ctx: commands.Context) -> bool:
                """Bot owner, server owner, administrators, Red admins/mods."""
                author = ctx.author
                if await self.bot.is_owner(author):
                        return True
                if not isinstance(author, discord.Member):
                        return False
                return (
                        author.id == author.guild.owner_id
                        or author.guild_permissions.administrator
                        or await self.bot.is_mod(author)
                )

        async def _may_manage(
                self, ctx: commands.Context, chart: Chart
        ) -> bool:
                return may_manage(
                        chart,
                        str(ctx.author.id),
                        privileged=await self._privileged(ctx),
                )

        async def _deny(self, ctx: commands.Context) -> None:
                await ctx.send(
                        "Only alignment admins, server admins and mods can "
                        "do that.",
                        ephemeral=True,
                )

        async def _confirm(self, ctx: commands.Context, question: str) -> bool:
                view = ConfirmView(ctx.author.id, "Yes, do it")
                await ctx.send(question, view=view, ephemeral=True)
                await view.wait()
                return bool(view.confirmed)

        # Pictures -------------------------------------------------------

        async def _avatar_bytes(
                self, chart_cells: tuple[Cell, ...]
        ) -> dict[str, bytes]:
                people = everyone(chart_cells)
                urls = avatar_urls(people)
                if self._session is None or self._session.closed:
                        self._session = aiohttp.ClientSession()
                fetched = await fetch_images(
                        self._session, urls - self._avatars.keys()
                )
                self._avatars = remember(
                        self._avatars, fetched, urls, AVATAR_CACHE_SIZE
                )
                return by_user(people, self._avatars)

        async def _send_picture(
                self,
                ctx: commands.Context,
                style: Style,
                title: str,
                chart_cells: tuple[Cell, ...],
        ) -> bool:
                """Send the chart as pictures; False without ImageMagick."""
                try:
                        from .core import render  # noqa: PLC0415
                except ImportError:
                        log.warning("Wand/ImageMagick missing; showing text")
                        return False
                avatars = await self._avatar_bytes(chart_cells)
                if style == "image":
                        png = await asyncio.to_thread(
                                render.render_chart, chart_cells, avatars
                        )
                        await ctx.send(
                                f"**{title}**",
                                file=discord.File(
                                        io.BytesIO(png), "alignment.png"
                                ),
                                allowed_mentions=NO_MENTIONS,
                        )
                        return True
                tiles = await asyncio.to_thread(
                        render.render_tiles, chart_cells, avatars
                )
                await ctx.send(
                        view=gallery_view(title, chart_cells),
                        files=[
                                discord.File(
                                        io.BytesIO(png), tile_filename(i, c)
                                )
                                for i, (c, png) in enumerate(
                                        zip(chart_cells, tiles, strict=True),
                                        start=1,
                                )
                        ],
                        allowed_mentions=NO_MENTIONS,
                )
                return True

        async def _send_text(
                self, ctx: commands.Context, chart_cells: tuple[Cell, ...]
        ) -> None:
                for page in pagify(format_text(chart_cells)):
                        await ctx.send(page, allowed_mentions=NO_MENTIONS)

        # Commands -------------------------------------------------------

        @commands.guild_only()
        # ty can't match Red's Context to hybrid_group's generic one
        @commands.hybrid_group(  # ty: ignore[invalid-argument-type]
                name="alignment", fallback="show", invoke_without_command=True
        )
        @app_commands.describe(
                style="image: one picture; gallery: nine pictures; text"
        )
        async def alignment(
                self, ctx: commands.Context, style: Style | None = None
        ) -> None:
                """Show this server's alignment chart."""
                guild = ctx.guild
                if guild is None:
                        return
                chart = await self._load(guild)
                if not chart.users:
                        await ctx.send(
                                "Nobody is on the chart yet. Use "
                                f"`{ctx.clean_prefix}alignment set` or "
                                "`/alignment set`."
                        )
                        return
                chosen: Style = (
                        style or await self.config.guild(guild).default_style()
                )
                chart_cells = cells(chart, member_lookup(guild))
                title = f"{guild.name}: alignment chart"
                if chosen != "text":
                        await ctx.defer()
                        if await self._send_picture(
                                ctx, chosen, title, chart_cells
                        ):
                                return
                await self._send_text(ctx, chart_cells)

        @alignment.command(name="set")
        @app_commands.describe(
                alignment="Lawful Good, CG, chaotic neutral, ...",
                member="Whose alignment to set (leave out for your own)",
        )
        async def alignment_set(
                self,
                ctx: commands.Context,
                alignment: str,
                member: discord.Member | None = None,
        ) -> None:
                """Put yourself, or someone else, on the chart."""
                guild = ctx.guild
                if guild is None:
                        return
                parsed = parse_alignment(alignment)
                if parsed is None:
                        await ctx.send(
                                BAD_ALIGNMENT.format(text=alignment),
                                ephemeral=True,
                        )
                        return
                target = member or ctx.author
                if not isinstance(target, discord.Member):
                        return
                await self._set(ctx, guild, target, parsed)

        @alignment_set.autocomplete("alignment")
        async def _alignment_autocomplete(
                self, interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
                del interaction
                return [
                        app_commands.Choice(name=a, value=a)
                        for a in matching(current)
                ]

        async def _set(
                self,
                ctx: commands.Context,
                guild: discord.Guild,
                member: discord.Member,
                alignment: AlignmentName,
        ) -> None:
                invoker_id, target_id = str(ctx.author.id), str(member.id)
                if not may_set(
                        await self._load(guild),
                        invoker_id,
                        target_id,
                        privileged=await self._privileged(ctx),
                ):
                        await ctx.send(
                                f"{member.display_name} is already on the "
                                "chart; only alignment admins can move them.",
                                ephemeral=True,
                                allowed_mentions=NO_MENTIONS,
                        )
                        return
                entry = Entry(
                        alignment, member.display_name, avatar_url(member)
                )
                await self._update(guild, lambda c: place(c, target_id, entry))
                who = (
                        "Your alignment"
                        if target_id == invoker_id
                        else f"Alignment for **{member.display_name}**"
                )
                await ctx.send(
                        f"{who} is now **{alignment}**.",
                        allowed_mentions=NO_MENTIONS,
                )

        @alignment.command(name="remove")
        @app_commands.describe(
                user="Who to take off (leave out for yourself; admins only "
                "for others)"
        )
        async def alignment_remove(
                self, ctx: commands.Context, user: discord.User | None = None
        ) -> None:
                """Take yourself, or someone else, off the chart."""
                guild = ctx.guild
                if guild is None:
                        return
                target = user or ctx.author
                chart = await self._load(guild)
                if not may_remove(
                        chart,
                        str(ctx.author.id),
                        str(target.id),
                        privileged=await self._privileged(ctx),
                ):
                        await self._deny(ctx)
                        return
                if str(target.id) not in chart.users:
                        await ctx.send(
                                "They aren't on the chart.", ephemeral=True
                        )
                        return
                await self._update(guild, lambda c: unplace(c, str(target.id)))
                await ctx.send(
                        "Removed from the chart.",
                        ephemeral=True,
                        allowed_mentions=NO_MENTIONS,
                )

        @alignment.command(name="prune")
        async def alignment_prune(self, ctx: commands.Context) -> None:
                """Take everyone who has left the server off the chart."""
                guild = ctx.guild
                if guild is None:
                        return
                chart = await self._load(guild)
                if not await self._may_manage(ctx, chart):
                        await self._deny(ctx)
                        return
                gone = absent(chart, member_lookup(guild))
                if not gone:
                        await ctx.send("Nobody has left.", ephemeral=True)
                        return
                await self._update(guild, lambda c: unplace_all(c, gone))
                await ctx.send(
                        f"Took {len(gone)} people who left off the chart."
                )

        @alignment.command(name="clear")
        async def alignment_clear(self, ctx: commands.Context) -> None:
                """Take everyone off the chart (admins stay admins)."""
                guild = ctx.guild
                if guild is None:
                        return
                chart = await self._load(guild)
                if not await self._may_manage(ctx, chart):
                        await self._deny(ctx)
                        return
                if not await self._confirm(
                        ctx,
                        f"Take all {len(chart.users)} people off the chart? "
                        "Export it first if you might want it back.",
                ):
                        await ctx.send("Left the chart alone.", ephemeral=True)
                        return
                await self._update(guild, clear)
                await ctx.send("The chart is empty now.")

        @alignment.command(name="style")
        @app_commands.describe(style="How the chart is shown by default")
        async def alignment_style(
                self, ctx: commands.Context, style: Style
        ) -> None:
                """Choose how this server's chart is shown by default."""
                guild = ctx.guild
                if guild is None:
                        return
                if not await self._may_manage(ctx, await self._load(guild)):
                        await self._deny(ctx)
                        return
                await self.config.guild(guild).default_style.set(style)
                await ctx.send(f"The chart now shows as **{style}**.")

        @alignment.command(name="export")
        async def alignment_export(self, ctx: commands.Context) -> None:
                """Download this server's chart as YAML."""
                guild = ctx.guild
                if guild is None:
                        return
                text = to_yaml(await self._load(guild))
                await ctx.send(
                        "Here's the chart. `alignment import` takes it back.",
                        file=discord.File(
                                io.BytesIO(text.encode()),
                                f"alignment-{guild.id}.yaml",
                        ),
                        ephemeral=True,
                )

        @alignment.command(name="import")
        @app_commands.describe(file="A YAML chart from `alignment export`")
        async def alignment_import(
                self, ctx: commands.Context, file: discord.Attachment
        ) -> None:
                """Replace the chart with an exported one (admins stay)."""
                guild = ctx.guild
                if guild is None:
                        return
                if not await self._may_manage(ctx, await self._load(guild)):
                        await self._deny(ctx)
                        return
                if file.size > MAX_IMPORT_BYTES:
                        await ctx.send("That file is too big.", ephemeral=True)
                        return
                try:
                        imported = from_yaml(
                                (await file.read()).decode("utf-8")
                        )
                except (UnicodeDecodeError, ChartFormatError) as error:
                        await ctx.send(
                                f"I can't read that chart: {error}",
                                ephemeral=True,
                        )
                        return
                if not await self._confirm(
                        ctx,
                        f"Replace the chart with the {len(imported.users)} "
                        "people in that file?",
                ):
                        await ctx.send("Left the chart alone.", ephemeral=True)
                        return
                await self._update(guild, lambda c: replace_users(c, imported))
                await ctx.send(
                        f"Imported {len(imported.users)} people. People who "
                        "aren't in this server show as having left."
                )

        # Admins ---------------------------------------------------------

        @alignment.group(name="admin", invoke_without_command=True)
        async def alignment_admin(self, ctx: commands.Context) -> None:
                """Manage who can move anyone on the chart."""
                await ctx.send_help()

        @alignment_admin.command(name="add")
        @app_commands.describe(member="Who may move anyone on the chart")
        async def alignment_admin_add(
                self, ctx: commands.Context, member: discord.Member
        ) -> None:
                """Make someone an alignment admin."""
                await self._change_admin(ctx, member, add=True)

        @alignment_admin.command(name="remove")
        @app_commands.describe(user="Who should stop being an alignment admin")
        async def alignment_admin_remove(
                self, ctx: commands.Context, user: discord.User
        ) -> None:
                """Stop someone being an alignment admin."""
                await self._change_admin(ctx, user, add=False)

        async def _change_admin(
                self,
                ctx: commands.Context,
                user: discord.User | discord.Member,
                *,
                add: bool,
        ) -> None:
                guild = ctx.guild
                if guild is None:
                        return
                if not await self._may_manage(ctx, await self._load(guild)):
                        await self._deny(ctx)
                        return
                uid = str(user.id)
                await self._update(
                        guild,
                        (lambda c: add_admin(c, uid))
                        if add
                        else (lambda c: remove_admin(c, uid)),
                )
                state = "is now" if add else "is no longer"
                await ctx.send(
                        f"{user.mention} {state} an alignment admin.",
                        allowed_mentions=NO_MENTIONS,
                )

        @alignment_admin.command(name="list")
        async def alignment_admin_list(self, ctx: commands.Context) -> None:
                """List the alignment admins."""
                guild = ctx.guild
                if guild is None:
                        return
                admins = (await self._load(guild)).admins
                lines = (
                        "\n".join(f"- <@{uid}>" for uid in admins)
                        if admins
                        else "No alignment admins yet."
                )
                await ctx.send(
                        "Alignment admins (server admins, mods and the "
                        f"server owner can always manage the chart):\n{lines}",
                        ephemeral=True,
                        allowed_mentions=NO_MENTIONS,
                )
