"""Red cog: a server's D&D alignment chart.

The cog itself (:mod:`.cog`) is a thin Discord front end on :mod:`.core`, which
knows nothing about Discord. Red is only imported when Red loads the cog, so
``doge_alignment.core`` and the command line (``python -m doge_alignment``)
work without it.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
        from redbot.core.bot import Red

__red_end_user_data_statement__ = (
        "This cog stores your alignment, display name and avatar URL for each "
        "server's chart, whether you are an alignment admin there, pending "
        "alignment suggestions for and by you, and whether you allow guesses. "
        "`alignment guess` sends up to 35 of your recent messages to the bot "
        "owner's own language model to suggest an alignment; they aren't "
        "stored. `alignment guessing false` blocks guesses, `alignment "
        "remove` takes you off a chart, and Red's data deletion removes you "
        "everywhere."
)


async def setup(bot: "Red") -> None:
        """Red's entry point."""
        from .cog import AlignmentCog  # noqa: PLC0415 (keep Red out of core)

        await bot.add_cog(AlignmentCog(bot))
