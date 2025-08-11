from __future__ import annotations

from pathlib import Path

import discord
from redbot.core import app_commands, commands

from playground.alignment import (
    load_yaml_file,
    parse_alignment_chart,
    remove_user_alignment,
    save_yaml_file,
    serialize_alignment_chart,
    set_user_alignment,
)


class AlignmentCog(commands.Cog):
    """Cog for keeping track of alignment charts."""

    def __init__(self, bot) -> None:
        self.bot = bot
        self.data_dir = Path(__file__).parent / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _get_server_file(self, guild_id: int) -> Path:
        return self.data_dir / f"{guild_id}.yaml"

    @app_commands.command(
        name="alignment_show",
        description="Display current alignment chart data.",
    )
    async def alignment_show(self, interaction: discord.Interaction):
        """Display current alignment chart (text form for now)."""
        guild_id = interaction.guild_id
        if guild_id is None:
            await interaction.response.send_message(
                "This command must be used in a server.",
                ephemeral=True,
            )
            return

        file_path = self._get_server_file(guild_id)
        raw_data = load_yaml_file(file_path)
        chart = parse_alignment_chart(raw_data)

        if not chart["users"]:
            await interaction.response.send_message(
                "No alignments set yet.", ephemeral=True
            )
            return

        lines = []
        for uid, entry in chart["users"].items():
            lines.append(f"**{entry['display_name']}** — {entry['alignment']}")
        text_output = "\n".join(lines)

        await interaction.response.send_message(text_output, ephemeral=True)

    @app_commands.command(name="alignment_set", description="Set your alignment.")
    @app_commands.describe(alignment="Choose your D&D style alignment.")
    @app_commands.choices(
        alignment=[
            app_commands.Choice(name=a, value=a)
            for a in (
                "Lawful Good",
                "Neutral Good",
                "Chaotic Good",
                "Lawful Neutral",
                "True Neutral",
                "Chaotic Neutral",
                "Lawful Evil",
                "Neutral Evil",
                "Chaotic Evil",
            )
        ]
    )
    async def alignment_set(
        self,
        interaction: discord.Interaction,
        alignment: app_commands.Choice[str],
    ):
        guild_id = interaction.guild_id
        if guild_id is None or interaction.user is None:
            await interaction.response.send_message(
                "This command must be used in a server.",
                ephemeral=True,
            )
            return

        file_path = self._get_server_file(guild_id)
        raw_data = load_yaml_file(file_path)
        chart = parse_alignment_chart(raw_data)

        updated_chart = set_user_alignment(
            chart,
            user_id=str(interaction.user.id),
            alignment=alignment.value,  # type: ignore
            display_name=interaction.user.display_name,
            avatar_url=(
                interaction.user.display_avatar.url
                if interaction.user.display_avatar
                else None
            ),
        )

        save_yaml_file(file_path, serialize_alignment_chart(updated_chart))
        await interaction.response.send_message(
            f"Alignment set to **{alignment.value}**.",
            ephemeral=True,
        )

    @app_commands.command(
        name="alignment_remove",
        description="Remove your alignment from the chart.",
    )
    async def alignment_remove(self, interaction: discord.Interaction):
        guild_id = interaction.guild_id
        if guild_id is None or interaction.user is None:
            await interaction.response.send_message(
                "This command must be used in a server.",
                ephemeral=True,
            )
            return

        file_path = self._get_server_file(guild_id)
        raw_data = load_yaml_file(file_path)
        chart = parse_alignment_chart(raw_data)

        updated_chart = remove_user_alignment(chart, str(interaction.user.id))
        save_yaml_file(file_path, serialize_alignment_chart(updated_chart))

        await interaction.response.send_message(
            "Your alignment has been removed.", ephemeral=True
        )


if __name__ == "__main__":
    print("hello")
