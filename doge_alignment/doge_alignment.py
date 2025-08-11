from redbot.core import app_commands, commands


class AlignmentCog(commands.Cog):
    """Cog for keeping track of alignment charts."""

    def __init__(self, bot) -> None:
        self.bot = bot

    @app_commands.command()
    async def alignment(self, ctx):
        """This does stuff!"""
        # Your code will go here
        await ctx.send("I can do stuff!")
