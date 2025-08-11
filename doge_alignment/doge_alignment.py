from redbot.core import app_commands, commands
import discord


class AlignmentCog(commands.Cog):
    """Cog for keeping track of alignment charts."""

    def __init__(self, bot) -> None:
        self.bot = bot

    @app_commands.command()
    async def alignment(self, interaction: discord.Interaction):
        """This does stuff!"""
        await interaction.response.send_message("Hello World!", ephemeral=True)
