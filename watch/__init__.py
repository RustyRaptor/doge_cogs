"""Red cog: preload movies and episodes into the media cache."""

from redbot.core.bot import Red

from .watch import Watch

__red_end_user_data_statement__ = (
        "Stores Jellyfin names, download grants, TV season choices, "
        "playlist ID mappings, and requester/channel/job metadata. Completed "
        "job details expire after seven days; playlist mappings persist for "
        "safe reimports. Red user-data deletion removes these records without "
        "deleting shared media or public Jellyfin playlists."
)


async def setup(bot: Red) -> None:
        """Red's entry point."""
        await bot.add_cog(Watch(bot))
