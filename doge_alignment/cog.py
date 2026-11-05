"""The Discord front end: ``[p]alignment`` and ``/alignment`` commands.

Everything here is Discord glue: reading and writing Red's Config, permissions,
fetching avatars and messages, and sending replies. The chart's rules,
suggestions, custom labels, the YAML format, the model prompt and the drawing
all live in :mod:`.core`.
"""

import asyncio
import io
import logging
import os
import re
from collections import OrderedDict
from collections.abc import Callable, Mapping
from typing import Literal

import aiohttp
import discord
from redbot.core import Config, app_commands, commands
from redbot.core.bot import Red
from redbot.core.utils.chat_formatting import pagify

from .core.alignments import ALIGNMENTS, AlignmentName
from .core.chart import (
        Chart,
        Entry,
        add_admin,
        clear,
        forget_user,
        import_chart,
        may_manage,
        may_remove,
        may_set,
        place,
        remove_admin,
        unplace,
        unplace_all,
        with_spec,
)
from .core.fetch import avatar_urls, by_user, fetch_images
from .core.guess import (
        MIN_MESSAGES,
        ModelConfig,
        ModelError,
        ask,
        build_prompt,
        clean_messages,
)
from .core.spec import (
        ChartSpec,
        SpecError,
        label,
        labels,
        matching_cells,
        parse_cell,
        with_color,
        with_columns,
        with_label,
        with_rows,
        with_title,
)
from .core.storage import (
        ChartFormatError,
        from_data,
        from_yaml,
        to_data,
        to_yaml,
)
from .core.suggestions import (
        GUESS,
        Suggestion,
        accept,
        find,
        may_answer,
        may_suggest,
        pending,
        suggest,
        tally,
        withdraw,
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
from .ui import AnswerButton, ConfirmView, Verb, answer_view, gallery_view

log = logging.getLogger("red.doge_cogs.doge_alignment")

RequestType = Literal["discord_deleted_user", "owner", "user", "user_strict"]
Style = Literal["image", "gallery", "text"]
Person = discord.User | discord.Member

AVATAR_CACHE_SIZE = 512
MAX_IMPORT_BYTES = 1024 * 1024
# Where guesses look for someone's messages
GUESS_CHANNELS = 8
GUESS_HISTORY = 200
NO_MENTIONS = discord.AllowedMentions.none()


def avatar_url(user: Person) -> str:
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


def parse_suggester(text: str) -> str | None:
        """Read who made a suggestion: a mention, an id, or ``guess``."""
        if text.strip().casefold() in {"guess", "model", "bot"}:
                return GUESS
        found = re.fullmatch(r"\s*(?:<@!?(\d+)>|(\d+))\s*", text)
        return (found[1] or found[2]) if found else None


def suggester_name(suggester_id: str) -> str:
        """Show a suggester: a mention, or "the model" for guesses."""
        return "the model" if suggester_id == GUESS else f"<@{suggester_id}>"


def bad_cell(text: str, spec: ChartSpec) -> str:
        """Explain that ``text`` isn't a cell on ``spec``'s chart."""
        return (
                f"I don't know the cell {text!r}. Use one of: "
                f"{', '.join(labels(spec))}, or the classic letters "
                "(LG NG CG LN TN CN LE NE CE)."
        )


def spec_summary(spec: ChartSpec) -> str:
        """Show a chart's title, axes and the 3x3 grid of labels."""
        grid = "\n".join(
                " | ".join(
                        label(spec, a) for a in ALIGNMENTS[r * 3 : r * 3 + 3]
                )
                for r in range(3)
        )
        return (
                f"**{spec.title}**\nColumns: {', '.join(spec.columns)}\n"
                f"Rows: {', '.join(spec.rows)}\n```\n{grid}\n```"
        )


def model_config(
        env: Mapping[str, str], tokens: Mapping[str, str]
) -> ModelConfig | None:
        """Return the guessing model's settings, or None if unset.

        From ``ALIGNMENT_MODEL_URL``/``_NAME``/``_KEY``/``_REASONING``, else
        Red's shared API tokens ``alignment_model`` (``url``, ``model``,
        ``key``, ``reasoning``).
        """
        url = env.get("ALIGNMENT_MODEL_URL") or tokens.get("url")
        if not url:
                return None
        reasoning = env.get("ALIGNMENT_MODEL_REASONING") or tokens.get(
                "reasoning"
        )
        return ModelConfig(
                base_url=url,
                model=env.get("ALIGNMENT_MODEL_NAME")
                or tokens.get("model")
                or "default",
                api_key=env.get("ALIGNMENT_MODEL_KEY") or tokens.get("key"),
                # "none" turns thinking off on reasoning models (Ollama)
                extra={"reasoning_effort": reasoning} if reasoning else {},
        )


def readable_by(channel: discord.TextChannel, *members: discord.Member) -> bool:
        """Whether all ``members`` can read ``channel``'s history."""
        return all(
                channel.permissions_for(m).read_message_history
                and channel.permissions_for(m).view_channel
                for m in members
        )


class AlignmentCog(commands.Cog):
        """Keep track of everyone's D&D alignment."""

        def __init__(self, bot: Red) -> None:
                """Create the cog."""
                self.bot = bot
                self.config = Config.get_conf(
                        self, identifier=0xA119E5C4A7, force_registration=True
                )
                self.config.register_guild(
                        users={},
                        admins=[],
                        suggestions={},
                        spec={},
                        default_style="image",
                )
                self.config.register_user(guess_blocked=False)
                self._locks: dict[int, asyncio.Lock] = {}
                self._avatars: OrderedDict[str, bytes] = OrderedDict()
                self._session: aiohttp.ClientSession | None = None

        async def cog_load(self) -> None:
                """Make suggestion buttons on old messages work again."""
                self.bot.add_dynamic_items(AnswerButton)

        async def cog_unload(self) -> None:
                """Close the HTTP session; forget the buttons."""
                self.bot.remove_dynamic_items(AnswerButton)
                if self._session is not None:
                        await self._session.close()

        async def red_delete_data_for_user(
                self, *, requester: RequestType, user_id: int
        ) -> None:
                """Remove a user from every chart, admin list and suggestion."""
                del requester
                for guild_id in await self.config.all_guilds():
                        await self._update_id(
                                guild_id, lambda c: forget_user(c, str(user_id))
                        )
                await self.config.user_from_id(user_id).clear()

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
                        for key in ("users", "admins", "suggestions", "spec"):
                                await group.set_raw(key, value=data[key])
                        return chart

        async def _update(
                self, guild: discord.Guild, change: Callable[[Chart], Chart]
        ) -> Chart:
                return await self._update_id(guild.id, change)

        def _http(self) -> aiohttp.ClientSession:
                if self._session is None or self._session.closed:
                        self._session = aiohttp.ClientSession()
                return self._session

        # Permissions and parsing ----------------------------------------

        async def _is_privileged(self, user: Person) -> bool:
                """Bot owner, server owner, administrators, Red admins/mods."""
                if await self.bot.is_owner(user):
                        return True
                if not isinstance(user, discord.Member):
                        return False
                return (
                        user.id == user.guild.owner_id
                        or user.guild_permissions.administrator
                        or await self.bot.is_mod(user)
                )

        async def _managed_chart(self, ctx: commands.Context) -> Chart | None:
                """Return the chart if the author may manage it, else deny."""
                if ctx.guild is None:
                        return None
                chart = await self._load(ctx.guild)
                if may_manage(
                        chart,
                        str(ctx.author.id),
                        privileged=await self._is_privileged(ctx.author),
                ):
                        return chart
                await ctx.send(
                        "Only alignment admins, server admins and mods can "
                        "do that.",
                        ephemeral=True,
                )
                return None

        async def _confirm(self, ctx: commands.Context, question: str) -> bool:
                view = ConfirmView(ctx.author.id, "Yes, do it")
                await ctx.send(question, view=view, ephemeral=True)
                await view.wait()
                return bool(view.confirmed)

        async def _cell(
                self, ctx: commands.Context, chart: Chart, text: str
        ) -> AlignmentName | None:
                """Parse ``text`` as a cell on ``chart``, or explain why not."""
                cell = parse_cell(text, chart.spec)
                if cell is None:
                        await ctx.send(
                                bad_cell(text, chart.spec),
                                ephemeral=True,
                                allowed_mentions=NO_MENTIONS,
                        )
                return cell

        async def _cell_autocomplete(
                self, interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
                guild = interaction.guild
                spec = (await self._load(guild)).spec if guild else ChartSpec()
                return [
                        app_commands.Choice(name=text, value=text)
                        for _, text in matching_cells(current, spec)
                ]

        # Pictures -------------------------------------------------------

        async def _avatar_bytes(
                self, chart_cells: tuple[Cell, ...]
        ) -> dict[str, bytes]:
                people = everyone(chart_cells)
                urls = avatar_urls(people)
                fetched = await fetch_images(
                        self._http(), urls - self._avatars.keys()
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

        # Showing and placing --------------------------------------------

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
                title = f"{guild.name}: {chart.spec.title}"
                if chosen != "text":
                        await ctx.defer()
                        if await self._send_picture(
                                ctx, chosen, title, chart_cells
                        ):
                                return
                await self._send_text(ctx, chart_cells)

        @alignment.command(name="set")
        @app_commands.describe(
                alignment="A cell on this server's chart (or LG, CG, ...)",
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
                target = member or ctx.author
                if guild is None or not isinstance(target, discord.Member):
                        return
                chart = await self._load(guild)
                cell = await self._cell(ctx, chart, alignment)
                if cell is None:
                        return
                invoker_id, target_id = str(ctx.author.id), str(target.id)
                if not may_set(
                        chart,
                        invoker_id,
                        target_id,
                        privileged=await self._is_privileged(ctx.author),
                ):
                        await ctx.send(
                                f"{target.display_name} is already on the "
                                "chart; only alignment admins can move them. "
                                f"Try `{ctx.clean_prefix}alignment suggest`.",
                                ephemeral=True,
                                allowed_mentions=NO_MENTIONS,
                        )
                        return
                entry = Entry(cell, target.display_name, avatar_url(target))
                await self._update(guild, lambda c: place(c, target_id, entry))
                who = (
                        "Your alignment"
                        if target_id == invoker_id
                        else f"Alignment for **{target.display_name}**"
                )
                await ctx.send(
                        f"{who} is now **{label(chart.spec, cell)}**.",
                        allowed_mentions=NO_MENTIONS,
                )

        @alignment_set.autocomplete("alignment")
        async def _set_autocomplete(
                self, interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
                return await self._cell_autocomplete(interaction, current)

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
                        privileged=await self._is_privileged(ctx.author),
                ):
                        await ctx.send(
                                "Only alignment admins can take others off.",
                                ephemeral=True,
                        )
                        return
                if str(target.id) not in chart.users:
                        await ctx.send(
                                "They aren't on the chart.", ephemeral=True
                        )
                        return
                await self._update(guild, lambda c: unplace(c, str(target.id)))
                await ctx.send("Removed from the chart.", ephemeral=True)

        @alignment.command(name="prune")
        async def alignment_prune(self, ctx: commands.Context) -> None:
                """Take everyone who has left the server off the chart."""
                chart = await self._managed_chart(ctx)
                if chart is None or ctx.guild is None:
                        return
                gone = absent(chart, member_lookup(ctx.guild))
                if not gone:
                        await ctx.send("Nobody has left.", ephemeral=True)
                        return
                await self._update(ctx.guild, lambda c: unplace_all(c, gone))
                await ctx.send(
                        f"Took {len(gone)} people who left off the chart."
                )

        @alignment.command(name="clear")
        async def alignment_clear(self, ctx: commands.Context) -> None:
                """Take everyone off the chart (admins and labels stay)."""
                chart = await self._managed_chart(ctx)
                if chart is None or ctx.guild is None:
                        return
                if not await self._confirm(
                        ctx,
                        f"Take all {len(chart.users)} people off the chart? "
                        "Export it first if you might want it back.",
                ):
                        await ctx.send("Left the chart alone.", ephemeral=True)
                        return
                await self._update(ctx.guild, clear)
                await ctx.send("The chart is empty now.")

        @alignment.command(name="style")
        @app_commands.describe(style="How the chart is shown by default")
        async def alignment_style(
                self, ctx: commands.Context, style: Style
        ) -> None:
                """Choose how this server's chart is shown by default."""
                if await self._managed_chart(ctx) is None or ctx.guild is None:
                        return
                await self.config.guild(ctx.guild).default_style.set(style)
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
                if await self._managed_chart(ctx) is None or ctx.guild is None:
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
                await self._update(
                        ctx.guild, lambda c: import_chart(c, imported)
                )
                await ctx.send(
                        f"Imported {len(imported.users)} people. People who "
                        "aren't in this server show as having left."
                )

        # Suggestions ----------------------------------------------------

        @alignment.command(name="suggest")
        @app_commands.describe(
                member="Who you're suggesting for",
                alignment="Where you think they belong",
        )
        async def alignment_suggest(
                self,
                ctx: commands.Context,
                member: discord.Member,
                *,
                alignment: str,
        ) -> None:
                """Suggest where someone belongs; they can accept or reject."""
                guild = ctx.guild
                if guild is None:
                        return
                if member.bot or not may_suggest(
                        str(ctx.author.id), str(member.id)
                ):
                        await ctx.send(
                                "Suggest for someone else (and not a bot). "
                                "For yourself, use `alignment set`.",
                                ephemeral=True,
                        )
                        return
                chart = await self._load(guild)
                cell = await self._cell(ctx, chart, alignment)
                if cell is None:
                        return
                current = chart.users.get(str(member.id))
                if current is not None and current.alignment == cell:
                        await ctx.send("They're already there.", ephemeral=True)
                        return
                await self._post_suggestion(
                        ctx,
                        chart,
                        Suggestion(str(member.id), str(ctx.author.id), cell),
                        f"{ctx.author.mention} thinks you belong in",
                )

        @alignment_suggest.autocomplete("alignment")
        async def _suggest_autocomplete(
                self, interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
                return await self._cell_autocomplete(interaction, current)

        async def _post_suggestion(
                self,
                ctx: commands.Context,
                chart: Chart,
                suggestion: Suggestion,
                lead: str,
                reason: str = "",
        ) -> None:
                if ctx.guild is None:
                        return
                await self._update(ctx.guild, lambda c: suggest(c, suggestion))
                target = suggestion.target_id
                await ctx.send(
                        f"<@{target}>, {lead} "
                        f"**{label(chart.spec, suggestion.alignment)}**"
                        f"{reason}. Accept or reject below (alignment "
                        "admins can answer too).",
                        view=answer_view(target, suggestion.suggester_id),
                        allowed_mentions=discord.AllowedMentions(
                                users=[discord.Object(int(target))]
                        ),
                )

        @alignment.command(name="suggestions")
        @app_commands.describe(member="Whose suggestions (leave out for yours)")
        async def alignment_suggestions(
                self,
                ctx: commands.Context,
                member: discord.Member | None = None,
        ) -> None:
                """List the suggestions waiting for someone."""
                guild = ctx.guild
                target = member or ctx.author
                if guild is None:
                        return
                chart = await self._load(guild)
                waiting = pending(chart, str(target.id))
                if not waiting:
                        await ctx.send(
                                "No suggestions waiting.", ephemeral=True
                        )
                        return
                counts = ", ".join(
                        f"{label(chart.spec, cell)} ×{n}"
                        for cell, n in tally(chart, str(target.id))
                )
                lines = "\n".join(
                        f"- {suggester_name(s.suggester_id)}: "
                        f"{label(chart.spec, s.alignment)}"
                        for s in waiting
                )
                await ctx.send(
                        f"Suggestions for {target.mention} ({counts}):\n"
                        f"{lines}\nAnswer with `alignment accept <who>` or "
                        "`alignment reject <who>` (`who` is a mention, or "
                        "`guess` for the model).",
                        ephemeral=True,
                        allowed_mentions=NO_MENTIONS,
                )

        @alignment.command(name="accept")
        @app_commands.describe(
                suggester="Whose suggestion: a mention, an id, or `guess`",
                member="For whom (admins; leave out for yourself)",
        )
        async def alignment_accept(
                self,
                ctx: commands.Context,
                suggester: str,
                member: discord.Member | None = None,
        ) -> None:
                """Accept a suggestion: you move where it says."""
                await self._answer_command(ctx, "accept", suggester, member)

        @alignment.command(name="reject")
        @app_commands.describe(
                suggester="Whose suggestion: a mention, an id, or `guess`",
                member="For whom (admins; leave out for yourself)",
        )
        async def alignment_reject(
                self,
                ctx: commands.Context,
                suggester: str,
                member: discord.Member | None = None,
        ) -> None:
                """Reject a suggestion."""
                await self._answer_command(ctx, "reject", suggester, member)

        async def _answer_command(
                self,
                ctx: commands.Context,
                verb: Verb,
                suggester: str,
                member: discord.Member | None,
        ) -> None:
                guild = ctx.guild
                if guild is None or not isinstance(ctx.author, discord.Member):
                        return
                suggester_id = parse_suggester(suggester)
                if suggester_id is None:
                        await ctx.send(
                                "Name the suggester with a mention, an id, "
                                "or `guess`.",
                                ephemeral=True,
                        )
                        return
                target = member or ctx.author
                done, text = await self._answer(
                        guild, ctx.author, verb, str(target.id), suggester_id
                )
                await ctx.send(
                        text, ephemeral=not done, allowed_mentions=NO_MENTIONS
                )

        async def _answer(
                self,
                guild: discord.Guild,
                invoker: discord.Member,
                verb: Verb,
                target_id: str,
                suggester_id: str,
        ) -> tuple[bool, str]:
                """Accept or reject a suggestion; return (done, message)."""
                chart = await self._load(guild)
                suggestion = find(chart, target_id, suggester_id)
                if suggestion is None:
                        return False, "That suggestion isn't waiting any more."
                if not may_answer(
                        chart,
                        str(invoker.id),
                        target_id,
                        privileged=await self._is_privileged(invoker),
                ):
                        return False, (
                                "Only the person it's for, or an alignment "
                                "admin, can answer it."
                        )
                where = label(chart.spec, suggestion.alignment)
                if verb == "reject":
                        await self._update(
                                guild,
                                lambda c: withdraw(c, target_id, suggester_id),
                        )
                        return True, f"Rejected **{where}** for <@{target_id}>."
                entry = self._entry_for(guild, chart, suggestion)
                await self._update(
                        guild, lambda c: accept(c, suggestion, entry)
                )
                return True, f"<@{target_id}> is now **{where}**."

        @staticmethod
        def _entry_for(
                guild: discord.Guild, chart: Chart, suggestion: Suggestion
        ) -> Entry:
                """Return the target's entry, with live details if known."""
                member = guild.get_member(int(suggestion.target_id))
                if member is not None:
                        return Entry(
                                suggestion.alignment,
                                member.display_name,
                                avatar_url(member),
                        )
                stored = chart.users.get(suggestion.target_id)
                return Entry(
                        suggestion.alignment,
                        stored.display_name if stored else suggestion.target_id,
                        stored.avatar_url if stored else None,
                )

        async def answer_from_button(
                self,
                interaction: discord.Interaction,
                verb: Verb,
                target_id: str,
                suggester_id: str,
        ) -> None:
                """Answer a suggestion from its Accept/Reject buttons."""
                guild = interaction.guild
                invoker = interaction.user
                if guild is None or not isinstance(invoker, discord.Member):
                        return
                done, text = await self._answer(
                        guild, invoker, verb, target_id, suggester_id
                )
                if not done:
                        await interaction.response.send_message(
                                text, ephemeral=True
                        )
                        return
                message = interaction.message
                original = message.content if message else ""
                await interaction.response.edit_message(
                        content=f"{original}\n→ {text} ({invoker.mention})",
                        view=None,
                        allowed_mentions=NO_MENTIONS,
                )

        # Guessing -------------------------------------------------------

        async def _model(self) -> ModelConfig | None:
                tokens = await self.bot.get_shared_api_tokens("alignment_model")
                return model_config(os.environ, tokens)

        async def _recent_messages(
                self,
                guild: discord.Guild,
                member: discord.Member,
                viewer: discord.Member,
        ) -> tuple[str, ...]:
                """Return ``member``'s newest messages that ``viewer`` can see.

                Looks through the most recently active channels that the bot
                and the viewer can both read.
                """
                channels = sorted(
                        (
                                c
                                for c in guild.text_channels
                                if readable_by(c, guild.me, viewer)
                        ),
                        key=lambda c: c.last_message_id or 0,
                        reverse=True,
                )[:GUESS_CHANNELS]
                found: list[discord.Message] = []
                for channel in channels:
                        try:
                                found += [
                                        m
                                        async for m in channel.history(
                                                limit=GUESS_HISTORY
                                        )
                                        if m.author.id == member.id
                                        and m.content
                                ]
                        except discord.HTTPException:
                                continue
                newest = sorted(found, key=lambda m: m.created_at, reverse=True)
                return clean_messages([m.content for m in newest])

        @alignment.command(name="guess")
        @commands.cooldown(1, 30, commands.BucketType.user)
        @app_commands.describe(
                member="Whose alignment to guess (yours if left out)"
        )
        async def alignment_guess(
                self,
                ctx: commands.Context,
                member: discord.Member | None = None,
        ) -> None:
                """Let a local model guess an alignment from recent messages.

                It reads up to 35 recent messages in channels you can see, and
                its pick becomes a suggestion to accept or reject.
                """
                guild = ctx.guild
                target = member or ctx.author
                viewer = ctx.author
                if (
                        guild is None
                        or not isinstance(target, discord.Member)
                        or not isinstance(viewer, discord.Member)
                ):
                        return
                config = await self._model()
                if config is None:
                        await ctx.send(
                                "No model is set up for guessing. The bot "
                                "owner can set `ALIGNMENT_MODEL_URL` or "
                                "`[p]set api alignment_model url,<url>`.",
                                ephemeral=True,
                        )
                        return
                if target.bot or await self.config.user(target).guess_blocked():
                        await ctx.send(
                                f"{target.display_name} doesn't want guesses.",
                                ephemeral=True,
                                allowed_mentions=NO_MENTIONS,
                        )
                        return
                await ctx.defer()
                await self._guess(ctx, guild, target, viewer, config)

        async def _guess(
                self,
                ctx: commands.Context,
                guild: discord.Guild,
                target: discord.Member,
                viewer: discord.Member,
                config: ModelConfig,
        ) -> None:
                messages = await self._recent_messages(guild, target, viewer)
                if len(messages) < MIN_MESSAGES:
                        await ctx.send(
                                f"I found only {len(messages)} recent messages "
                                f"from {target.display_name}; I need "
                                f"{MIN_MESSAGES}.",
                                allowed_mentions=NO_MENTIONS,
                        )
                        return
                chart = await self._load(guild)
                prompt = build_prompt(target.display_name, messages, chart.spec)
                try:
                        guessed = await ask(
                                self._http(), config, prompt, chart.spec
                        )
                except ModelError as error:
                        log.warning("Alignment guess failed: %s", error)
                        await ctx.send(f"The model failed: {error}")
                        return
                reason = f" (“{guessed.reason}”)" if guessed.reason else ""
                await self._post_suggestion(
                        ctx,
                        chart,
                        Suggestion(str(target.id), GUESS, guessed.alignment),
                        f"from {len(messages)} recent messages, the model "
                        "thinks you belong in",
                        reason,
                )

        @alignment.command(name="guessing")
        @app_commands.describe(allow="Whether the model may guess for you")
        async def alignment_guessing(
                self,
                ctx: commands.Context,
                allow: bool,  # noqa: FBT001 (a command option)
        ) -> None:
                """Allow or block model guesses from your messages."""
                await self.config.user(ctx.author).guess_blocked.set(not allow)
                await ctx.send(
                        "Guesses are allowed for you."
                        if allow
                        else "Nobody can run guesses on your messages now.",
                        ephemeral=True,
                )

        # Chart labels ---------------------------------------------------

        @alignment.group(name="chart", invoke_without_command=True)
        async def alignment_chart(self, ctx: commands.Context) -> None:
                """Show or change this server's chart labels and colours."""
                if ctx.guild is None:
                        return
                spec = (await self._load(ctx.guild)).spec
                await ctx.send(
                        spec_summary(spec),
                        ephemeral=True,
                        allowed_mentions=NO_MENTIONS,
                )

        async def _change_spec(
                self,
                ctx: commands.Context,
                change: Callable[[ChartSpec], ChartSpec],
                done: str,
        ) -> None:
                chart = await self._managed_chart(ctx)
                if chart is None or ctx.guild is None:
                        return
                try:
                        spec = change(chart.spec)
                except SpecError as error:
                        await ctx.send(
                                f"Can't do that: {error}.", ephemeral=True
                        )
                        return
                await self._update(ctx.guild, lambda c: with_spec(c, spec))
                await ctx.send(
                        f"{done}\n{spec_summary(spec)}",
                        allowed_mentions=NO_MENTIONS,
                )

        @alignment_chart.command(name="title")
        @app_commands.describe(title="The chart's title")
        async def alignment_chart_title(
                self, ctx: commands.Context, *, title: str
        ) -> None:
                """Rename the chart."""
                await self._change_spec(
                        ctx, lambda s: with_title(s, title), "Chart renamed."
                )

        @alignment_chart.command(name="columns")
        @app_commands.describe(
                left="Left column (Lawful)",
                middle="Middle column (Neutral)",
                right="Right column (Chaotic)",
        )
        async def alignment_chart_columns(
                self, ctx: commands.Context, left: str, middle: str, right: str
        ) -> None:
                """Rename the three columns."""
                await self._change_spec(
                        ctx,
                        lambda s: with_columns(s, left, middle, right),
                        "Columns renamed.",
                )

        @alignment_chart.command(name="rows")
        @app_commands.describe(
                top="Top row (Good)",
                middle="Middle row (Neutral)",
                bottom="Bottom row (Evil)",
        )
        async def alignment_chart_rows(
                self, ctx: commands.Context, top: str, middle: str, bottom: str
        ) -> None:
                """Rename the three rows."""
                await self._change_spec(
                        ctx,
                        lambda s: with_rows(s, top, middle, bottom),
                        "Rows renamed.",
                )

        @alignment_chart.command(name="label")
        @app_commands.describe(
                cell="The cell to rename",
                text="Its new name (leave out to go back to the default)",
        )
        async def alignment_chart_label(
                self,
                ctx: commands.Context,
                cell: str,
                *,
                text: str | None = None,
        ) -> None:
                """Rename one cell, or reset its name."""
                if ctx.guild is None:
                        return
                found = await self._cell(ctx, await self._load(ctx.guild), cell)
                if found is not None:
                        await self._change_spec(
                                ctx,
                                lambda s: with_label(s, found, text),
                                "Cell renamed." if text else "Cell name reset.",
                        )

        @alignment_chart_label.autocomplete("cell")
        async def _label_autocomplete(
                self, interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
                return await self._cell_autocomplete(interaction, current)

        @alignment_chart.command(name="color")
        @app_commands.describe(
                cell="The cell to colour",
                color="A colour like #3a7bd5 (leave out for the default)",
        )
        async def alignment_chart_color(
                self,
                ctx: commands.Context,
                cell: str,
                color: str | None = None,
        ) -> None:
                """Recolour one cell, or reset its colour."""
                if ctx.guild is None:
                        return
                found = await self._cell(ctx, await self._load(ctx.guild), cell)
                if found is not None:
                        await self._change_spec(
                                ctx,
                                lambda s: with_color(s, found, color),
                                "Colour set." if color else "Colour reset.",
                        )

        @alignment_chart_color.autocomplete("cell")
        async def _color_autocomplete(
                self, interaction: discord.Interaction, current: str
        ) -> list[app_commands.Choice[str]]:
                return await self._cell_autocomplete(interaction, current)

        @alignment_chart.command(name="reset")
        async def alignment_chart_reset(self, ctx: commands.Context) -> None:
                """Go back to the classic chart's labels and colours."""
                if await self._managed_chart(ctx) is None:
                        return
                if not await self._confirm(
                        ctx, "Reset the title, labels and colours?"
                ):
                        await ctx.send("Left the chart alone.", ephemeral=True)
                        return
                await self._change_spec(
                        ctx,
                        lambda _s: ChartSpec(),
                        "Back to the classic chart.",
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
                self, ctx: commands.Context, user: Person, *, add: bool
        ) -> None:
                if await self._managed_chart(ctx) is None or ctx.guild is None:
                        return
                uid = str(user.id)
                await self._update(
                        ctx.guild,
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
