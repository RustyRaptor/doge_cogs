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
                "suggestions": {},
                "spec": {
                        "title": "Alignment chart",
                        "columns": ["Lawful", "Neutral", "Chaotic"],
                        "rows": ["Good", "Neutral", "Evil"],
                        "labels": {},
                        "colors": {},
                },
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
                "suggest",
                "suggestions",
                "accept",
                "reject",
                "guess",
                "baseline",
                "guessing",
                "chart",
                "admin",
        }
        chart = [o["name"] for o in options["chart"]["options"]]
        assert chart == ["title", "columns", "rows", "label", "color", "reset"]
        suggest = [o["name"] for o in options["suggest"]["options"]]
        assert suggest == ["member", "alignment"]
        set_options = [o["name"] for o in options["set"]["options"]]
        assert set_options == ["alignment", "member"]
        assert options["set"]["options"][0]["autocomplete"] is True
        styles = options["show"]["options"][0]["choices"]
        assert [c["value"] for c in styles] == ["image", "gallery", "text"]
        admin = [o["name"] for o in options["admin"]["options"]]
        assert admin == ["add", "remove", "list"]
        await cog.cog_unload()


def test_parse_suggester() -> None:
        """Mentions, ids and 'guess'."""
        from doge_alignment.cog import parse_suggester  # noqa: PLC0415

        assert parse_suggester("<@123>") == "123"
        assert parse_suggester("<@!123>") == "123"
        assert parse_suggester(" 456 ") == "456"
        assert parse_suggester("Guess") == "guess"
        assert parse_suggester("bob") is None


def test_model_config_from_env_then_tokens() -> None:
        """Environment wins over Red's shared API tokens; unset is None."""
        from doge_alignment.cog import model_config  # noqa: PLC0415

        assert model_config({}, {}) is None
        tokens = {"url": "http://t", "model": "tm", "key": "tk"}
        from_tokens = model_config({}, tokens)
        assert from_tokens is not None
        assert (
                from_tokens.base_url,
                from_tokens.model,
                from_tokens.api_key,
        ) == (
                "http://t",
                "tm",
                "tk",
        )
        env = {"ALIGNMENT_MODEL_URL": "http://e"}
        from_env = model_config(env, tokens)
        assert from_env is not None
        assert (from_env.base_url, from_env.model) == ("http://e", "tm")
        assert from_env.extra == {}
        assert from_env.context_tokens == 32_000
        sized = model_config({**env, "ALIGNMENT_CONTEXT_TOKENS": "65536"}, {})
        assert sized is not None
        assert sized.context_tokens == 65_536
        thinking_off = model_config(
                {**env, "ALIGNMENT_MODEL_REASONING": "none"}, {}
        )
        assert thinking_off is not None
        assert thinking_off.extra == {"reasoning_effort": "none"}


def test_guess_timezone() -> None:
        """Mountain Time unless set; unknown names fall back to UTC."""
        from datetime import UTC  # noqa: PLC0415

        from doge_alignment.cog import guess_timezone  # noqa: PLC0415

        assert str(guess_timezone({}, {})) == "America/Denver"
        assert str(guess_timezone({}, {"timezone": "Europe/Paris"})) == (
                "Europe/Paris"
        )
        assert str(
                guess_timezone({"ALIGNMENT_TIMEZONE": "Asia/Tokyo"}, {})
        ) == ("Asia/Tokyo")
        assert guess_timezone({"ALIGNMENT_TIMEZONE": "Mars/Base"}, {}) is UTC


def test_zone_or_and_baseline_switch() -> None:
        """Unknown or missing zones fall back; the baseline can be off."""
        from datetime import UTC  # noqa: PLC0415

        from doge_alignment.cog import baseline_enabled, zone_or  # noqa: PLC0415

        assert str(zone_or("Asia/Tokyo", UTC)) == "Asia/Tokyo"
        assert zone_or(None, UTC) is UTC
        assert zone_or("Not/AZone", UTC) is UTC
        assert zone_or(5, UTC) is UTC
        assert baseline_enabled({}, {})
        assert not baseline_enabled({"ALIGNMENT_BASELINE": "off"}, {})
        assert not baseline_enabled({}, {"baseline": "False"})


async def test_timezone_from_the_timezone_cog(cog: AlignmentCog) -> None:
        """A zone set with aikaterna's timezone cog wins over the default."""
        from datetime import UTC  # noqa: PLC0415

        zones = Config(
                cog_name="Timezone",
                unique_identifier="278049241001",
                driver=cog.config._driver,
        )
        cog.zones = zones
        await zones.user_from_id(5).set_raw("usertime", value="Asia/Tokyo")
        person = cast("discord.User", SimpleNamespace(id=5))
        nobody = cast("discord.User", SimpleNamespace(id=6))
        assert str(await cog._timezone(person, UTC)) == "Asia/Tokyo"
        assert await cog._timezone(nobody, UTC) is UTC


def test_profile_text_skips_default_and_bot_roles() -> None:
        """Roles come highest first, without @everyone or managed ones."""
        from doge_alignment.cog import profile_text  # noqa: PLC0415

        def role(
                name: str, *, default: bool = False, managed: bool = False
        ) -> SimpleNamespace:
                return SimpleNamespace(
                        name=name,
                        is_default=lambda: default,
                        managed=managed,
                )

        member = SimpleNamespace(
                name="sam",
                display_name="Sammy",
                activities=(discord.CustomActivity("brb"),),
                roles=[
                        role("@everyone", default=True),
                        role("Gamers"),
                        role("SomeBot", managed=True),
                        role("Mods"),
                ],
        )
        text = profile_text(cast("discord.Member", member))
        assert "Custom status: brb" in text
        assert "Roles: Mods, Gamers" in text
