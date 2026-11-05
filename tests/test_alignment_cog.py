"""The Discord glue: Config storage, data deletion, caching, views."""

from collections import OrderedDict
from collections.abc import Iterator
from types import SimpleNamespace
from typing import cast

import discord
import pytest
from alignment_helpers import chart_with
from redbot.core import Config
from redbot.core.bot import Red

from doge_alignment.cog import AlignmentCog, member_lookup, remember
from doge_alignment.core.chart import Entry, add_admin, place
from doge_alignment.core.storage import from_data, to_data
from doge_alignment.core.view import Profile, cells
from doge_alignment.ui import gallery_view


@pytest.fixture
def cog(red: Red, config: Config) -> Iterator[AlignmentCog]:
        """Return an AlignmentCog on Red's test Config backend."""
        cog = AlignmentCog.__new__(AlignmentCog)
        cog.bot = red
        config.register_guild(users={}, admins=[], default_style="image")
        cog.config = config
        cog._locks = {}
        yield cog


async def test_update_saves_and_keeps_other_settings(cog: AlignmentCog) -> None:
        """Updates write the chart without touching the style setting."""
        group = cog.config.guild_from_id(1)
        await group.default_style.set("gallery")
        chart = await cog._update_id(
                1,
                lambda c: add_admin(
                        place(c, "5", Entry("Lawful Good", "e")), "5"
                ),
        )
        assert from_data(await group.all()) == chart
        assert await group.default_style() == "gallery"


async def test_delete_data_for_user_clears_every_guild(
        cog: AlignmentCog,
) -> None:
        """Red's data deletion removes the user from charts and admin lists."""
        chart = chart_with(
                ("1", "Lawful Good"), ("2", "Chaotic Evil"), admins=("1",)
        )
        for guild_id in (100, 200):
                await cog._update_id(guild_id, lambda _c: chart)

        await cog.red_delete_data_for_user(requester="user", user_id=1)

        for guild_id in (100, 200):
                stored = from_data(
                        await cog.config.guild_from_id(guild_id).all()
                )
                assert list(stored.users) == ["2"]
                assert stored.admins == ()


def test_saved_data_is_plain() -> None:
        """Config gets plain dicts and lists, not dataclasses."""
        data = to_data(chart_with(("1", "True Neutral"), admins=("1",)))
        assert data == {
                "users": {
                        "1": {
                                "alignment": "True Neutral",
                                "display_name": "user1",
                                "avatar_url": None,
                        }
                },
                "admins": ["1"],
        }


def test_remember_is_an_lru() -> None:
        """New and used URLs stay; the least recently used go first."""
        cache = OrderedDict([("a", b"1"), ("b", b"2")])
        after = remember(cache, {"c": b"3"}, frozenset({"a"}), limit=2)
        assert list(after) == ["c", "a"]
        assert list(cache) == ["a", "b"]


def test_member_lookup() -> None:
        """Members give live profiles; strangers give None."""
        avatar = SimpleNamespace(
                replace=lambda **_kw: SimpleNamespace(url="https://cdn/a.png")
        )
        member = SimpleNamespace(display_name="Alice", display_avatar=avatar)
        guild = SimpleNamespace(get_member=lambda i: member if i == 1 else None)
        lookup = member_lookup(cast("discord.Guild", guild))
        assert lookup("1") == Profile("Alice", "https://cdn/a.png")
        assert lookup("2") is None


def test_gallery_view_has_nine_tiles() -> None:
        """The gallery points at the nine attached tiles, in order."""
        view = gallery_view("Chart", cells(chart_with()))
        gallery = next(
                item
                for item in view.children
                if isinstance(item, discord.ui.MediaGallery)
        )
        urls = [item.media.url for item in gallery.items]
        assert len(urls) == 9
        assert urls[0] == "attachment://1-lg.png"
        assert urls[8] == "attachment://9-ce.png"


async def test_slash_commands_are_valid(red: Red) -> None:
        """discord.py builds the /alignment payload Discord would get."""
        cog = AlignmentCog(red)
        tree = discord.app_commands.CommandTree(
                discord.Client(intents=discord.Intents.none())
        )
        group = cog.alignment.app_command
        assert group is not None
        payload = group.to_dict(tree)
        options = {o["name"]: o for o in payload["options"]}
        assert set(options) == {
                "show",
                "set",
                "remove",
                "prune",
                "clear",
                "style",
                "export",
                "import",
                "admin",
        }
        set_options = [o["name"] for o in options["set"]["options"]]
        assert set_options == ["alignment", "member"]
        assert options["set"]["options"][0]["autocomplete"] is True
        styles = options["show"]["options"][0]["choices"]
        assert [c["value"] for c in styles] == ["image", "gallery", "text"]
        admin = [o["name"] for o in options["admin"]["options"]]
        assert admin == ["add", "remove", "list"]
        await cog.cog_unload()
