"""Red cog: preload movies and episodes into the media cache."""

from redbot.core.bot import Red

from .watch import Watch

__red_end_user_data_statement__ = (
        "This cog stores the Jellyfin user name you link with `watch link`, "
        "to find your next unwatched episodes. `watch unlink` deletes it."
)


async def setup(bot: Red) -> None:
        """Red's entry point."""
        await bot.add_cog(Watch(bot))
