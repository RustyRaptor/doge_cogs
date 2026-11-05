"""Red cog: a server's D&D alignment chart."""

from redbot.core.bot import Red

from .doge_alignment import AlignmentCog

__red_end_user_data_statement__ = (
        "This cog stores your alignment, display name and avatar URL "
        "for each server's chart, and whether you are an alignment admin "
        "there. `/alignment_remove` takes you off a chart; Red's data "
        "deletion removes you everywhere."
)


async def setup(bot: Red) -> None:
        """Red's entry point."""
        await bot.add_cog(AlignmentCog(bot))
