"""The cog stores charts in Red's Config and deletes user data."""

from collections.abc import Iterator

import pytest
from redbot.core import Config
from redbot.core.bot import Red

from doge_alignment.chart import add_admin, empty_chart, set_user_alignment
from doge_alignment.doge_alignment import AlignmentCog


@pytest.fixture
def cog(red: Red, config: Config) -> Iterator[AlignmentCog]:
        """Return an AlignmentCog on Red's test Config backend."""
        cog = AlignmentCog.__new__(AlignmentCog)
        cog.bot = red
        config.register_guild(users={}, admins=[])
        cog.config = config
        yield cog


async def test_delete_data_for_user_clears_every_guild(
        cog: AlignmentCog,
) -> None:
        """Red's data deletion removes the user from charts and admin lists."""
        chart = add_admin(
                set_user_alignment(
                        set_user_alignment(
                                empty_chart(), "1", "Lawful Good", "a"
                        ),
                        "2",
                        "Chaotic Evil",
                        "b",
                ),
                "1",
        )
        for guild_id in (100, 200):
                await cog.config.guild_from_id(guild_id).set(chart)

        await cog.red_delete_data_for_user(requester="user", user_id=1)

        for guild_id in (100, 200):
                stored = await cog.config.guild_from_id(guild_id).all()
                assert list(stored["users"]) == ["2"]
                assert stored["admins"] == []


async def test_saved_chart_round_trips(cog: AlignmentCog) -> None:
        """What the cog saves is what it reads back."""
        chart = add_admin(
                set_user_alignment(empty_chart(), "7", "True Neutral", "n"), "7"
        )
        await cog.config.guild_from_id(300).set(chart)
        assert await cog.config.guild_from_id(300).all() == chart
