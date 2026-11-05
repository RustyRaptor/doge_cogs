"""Slash commands for a server's alignment chart."""

from typing import Literal

import discord
from redbot.core import Config, app_commands, commands
from redbot.core.bot import Red

from .chart import (
        ALIGNMENTS,
        AlignmentChart,
        add_admin,
        as_alignment,
        forget_user,
        format_chart,
        may_manage_admins,
        may_set,
        normalize_chart,
        remove_admin,
        remove_user_alignment,
        set_user_alignment,
)

RequestType = Literal["discord_deleted_user", "owner", "user", "user_strict"]

ALIGNMENT_CHOICES = [app_commands.Choice(name=a, value=a) for a in ALIGNMENTS]


async def _reply(interaction: discord.Interaction, text: str) -> None:
        await interaction.response.send_message(text, ephemeral=True)


class AlignmentCog(commands.Cog):
        """Keep track of everyone's D&D alignment."""

        def __init__(self, bot: Red) -> None:
                """Create the cog."""
                self.bot = bot
                self.config = Config.get_conf(
                        self, identifier=0xA119E5C4A7, force_registration=True
                )
                self.config.register_guild(users={}, admins=[])

        async def red_delete_data_for_user(
                self, *, requester: RequestType, user_id: int
        ) -> None:
                """Take a user off every chart and every admin list."""
                del requester
                for guild_id in await self.config.all_guilds():
                        group = self.config.guild_from_id(guild_id)
                        chart = normalize_chart(await group.all())
                        await group.set(forget_user(chart, str(user_id)))

        async def _chart(self, guild: discord.Guild) -> AlignmentChart:
                return normalize_chart(await self.config.guild(guild).all())

        async def _save(
                self, guild: discord.Guild, chart: AlignmentChart
        ) -> None:
                await self.config.guild(guild).set(chart)

        async def _is_bot_owner(
                self, user: discord.User | discord.Member
        ) -> bool:
                return await self.bot.is_owner(user)

        @app_commands.command(
                name="alignment_show",
                description="Show this server's alignment chart.",
        )
        @app_commands.guild_only()
        async def alignment_show(
                self, interaction: discord.Interaction
        ) -> None:
                """Show the chart as text."""
                if interaction.guild is None:
                        return
                chart = await self._chart(interaction.guild)
                if not chart["users"]:
                        await _reply(interaction, "No alignments set yet.")
                        return
                await _reply(interaction, format_chart(chart))

        @app_commands.command(
                name="alignment_set", description="Set your alignment."
        )
        @app_commands.guild_only()
        @app_commands.describe(alignment="Your D&D alignment.")
        @app_commands.choices(alignment=ALIGNMENT_CHOICES)
        async def alignment_set(
                self,
                interaction: discord.Interaction,
                alignment: app_commands.Choice[str],
        ) -> None:
                """Put yourself on the chart."""
                await self._set(interaction, alignment.value, None)

        @app_commands.command(
                name="alignment_set_other",
                description="Set someone's alignment (admins can move anyone).",
        )
        @app_commands.guild_only()
        @app_commands.describe(
                target="Whose alignment to set (blank for your own)",
                alignment="The alignment to set",
        )
        @app_commands.choices(alignment=ALIGNMENT_CHOICES)
        async def alignment_set_other(
                self,
                interaction: discord.Interaction,
                alignment: app_commands.Choice[str],
                target: discord.Member | None = None,
        ) -> None:
                """Place someone on the chart."""
                await self._set(interaction, alignment.value, target)

        async def _set(
                self,
                interaction: discord.Interaction,
                value: str,
                target: discord.Member | None,
        ) -> None:
                guild = interaction.guild
                if guild is None:
                        return
                member = target or interaction.user
                invoker_id = str(interaction.user.id)
                target_id = str(member.id)
                chart = await self._chart(guild)
                privileged = await self._is_bot_owner(interaction.user)
                if not may_set(
                        chart, invoker_id, target_id, privileged=privileged
                ):
                        await _reply(
                                interaction,
                                f"{member.display_name} already has an "
                                "alignment; only alignment admins can change "
                                "it.",
                        )
                        return
                alignment = as_alignment(value)
                await self._save(
                        guild,
                        set_user_alignment(
                                chart,
                                user_id=target_id,
                                alignment=alignment,
                                display_name=member.display_name,
                                avatar_url=member.display_avatar.url,
                        ),
                )
                who = (
                        "Your alignment"
                        if target_id == invoker_id
                        else f"Alignment for **{member.display_name}**"
                )
                await _reply(interaction, f"{who} set to **{alignment}**.")

        @app_commands.command(
                name="alignment_remove",
                description="Take yourself off the alignment chart.",
        )
        @app_commands.guild_only()
        async def alignment_remove(
                self, interaction: discord.Interaction
        ) -> None:
                """Remove your own alignment."""
                if interaction.guild is None:
                        return
                chart = await self._chart(interaction.guild)
                await self._save(
                        interaction.guild,
                        remove_user_alignment(chart, str(interaction.user.id)),
                )
                await _reply(interaction, "Your alignment has been removed.")

        async def _may_manage_admins(
                self, interaction: discord.Interaction, chart: AlignmentChart
        ) -> bool:
                guild = interaction.guild
                privileged = (
                        guild is not None
                        and interaction.user.id == guild.owner_id
                ) or await self._is_bot_owner(interaction.user)
                return may_manage_admins(
                        chart, str(interaction.user.id), privileged=privileged
                )

        @app_commands.command(
                name="alignment_add_admin",
                description="Let someone move anyone on the alignment chart.",
        )
        @app_commands.guild_only()
        async def alignment_add_admin(
                self, interaction: discord.Interaction, user: discord.Member
        ) -> None:
                """Make ``user`` an alignment admin."""
                if interaction.guild is None:
                        return
                chart = await self._chart(interaction.guild)
                if not await self._may_manage_admins(interaction, chart):
                        await _reply(
                                interaction,
                                "Only the server owner and alignment admins "
                                "can change admins.",
                        )
                        return
                await self._save(
                        interaction.guild, add_admin(chart, str(user.id))
                )
                await _reply(
                        interaction,
                        f"{user.display_name} is now an alignment admin.",
                )

        @app_commands.command(
                name="alignment_remove_admin",
                description="Remove someone from the alignment admins.",
        )
        @app_commands.guild_only()
        async def alignment_remove_admin(
                self, interaction: discord.Interaction, user: discord.Member
        ) -> None:
                """Stop ``user`` being an alignment admin."""
                if interaction.guild is None:
                        return
                chart = await self._chart(interaction.guild)
                if not await self._may_manage_admins(interaction, chart):
                        await _reply(
                                interaction,
                                "Only the server owner and alignment admins "
                                "can change admins.",
                        )
                        return
                await self._save(
                        interaction.guild, remove_admin(chart, str(user.id))
                )
                await _reply(
                        interaction,
                        f"{user.display_name} is no longer an alignment admin.",
                )
