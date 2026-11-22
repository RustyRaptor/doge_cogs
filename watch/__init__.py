"""Red cog: preload movies and episodes into the media cache."""

from redbot.core.bot import Red

from .watch import Watch

__red_end_user_data_statement__ = (
        "This cog stores the Jellyfin user name you link with `watch link`, "
        "to find your next unwatched episodes, plus an owner-managed download "
        "permission per user. `watch unlink` deletes the name; Red user-data "
        "deletion removes both."
)


async def setup(bot: Red) -> None:
        """Red's entry point."""
        await bot.add_cog(Watch(bot))
