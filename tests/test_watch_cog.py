"""The watch cog against a fake media-preload on a real socket.

Every test goes through the generated client, httpx, httpcore and anyio, so
a dependency mismatch in the bot's environment fails here (see
watch_helpers).
"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
from redbot.core import Config
from watch_helpers import (
        TOKEN,
        FakeAuthor,
        FakeContext,
        FakePreload,
        media_item,
        preload_request,
        serve,
)

from watch.spec import EpisodeSpec
from watch.watch import PreloadError, Watch


class FakeBot:
        """The bot calls the watch cog makes: tokens and permissions."""

        def __init__(self, *, mod: bool = False) -> None:
                """Answer is_mod with ``mod``; nobody is the owner."""
                self.mod = mod

        async def get_shared_api_tokens(self, _service: str) -> dict[str, str]:
                """No tokens set with [p]set api (the env vars are used)."""
                return {}

        async def is_mod(self, _member: object) -> bool:
                """Whether the author is a moderator."""
                return self.mod

        async def is_owner(self, _user: object) -> bool:
                """Nobody owns the test bot."""
                return False


@pytest.fixture
def fake() -> FakePreload:
        """Return an empty fake media-preload."""
        return FakePreload()


@pytest.fixture
async def cog(
        fake: FakePreload, config: Config, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Watch]:
        """Return a Watch cog talking to ``fake`` over HTTP."""
        async with serve(fake) as url:
                monkeypatch.setenv("PRELOAD_API_URL", url)
                monkeypatch.setenv("PRELOAD_BOT_TOKEN", TOKEN)
                cog = Watch.__new__(Watch)
                cog.bot = cast("Any", FakeBot())
                config.register_user(jellyfin_user=None)
                cog.config = config
                cog._pollers = set()
                yield cog
                await asyncio.gather(*cog._pollers)


async def run_watch(cog: Watch, ctx: FakeContext, query: str) -> None:
        """Run ``[p]watch <query>`` and let its progress poller finish."""
        await Watch.watch.callback(cog, cast("Any", ctx), query=query)
        await asyncio.gather(*cog._pollers)


# Library search -------------------------------------------------------


async def test_search_lists_cacheable_items_first(
        cog: Watch, fake: FakePreload
) -> None:
        """Items that can't be cached go to the end, order otherwise kept."""
        fake.search_results = [
                media_item("a", "Alien", cacheable=False),
                media_item("b", "Aliens"),
                media_item("c", "Alien 3"),
        ]

        items = await cog.search("alien", series_only=False)

        assert [i.id for i in items] == ["b", "c", "a"]


async def test_search_for_episodes_asks_for_series_only(
        cog: Watch, fake: FakePreload
) -> None:
        """With an episode spec only series are searched for."""
        fake.search_results = [
                media_item("m", "Dark Matter", kind="movie"),
                media_item("s", "Dark Matter", kind="series", seasons=2),
        ]

        items = await cog.search("Dark Matter", series_only=True)

        assert [i.id for i in items] == ["s"]
        assert fake.calls_to("GET", "/v1/search") == [
                {"q": "Dark Matter", "type": "series", "limit": "10"}
        ]


async def test_api_errors_carry_the_api_message(cog: Watch) -> None:
        """A Problem response becomes a PreloadError with its message."""
        with pytest.raises(PreloadError, match="no such request"):
                await cog.fetch(404)


async def test_wrong_token_is_reported(
        cog: Watch, monkeypatch: pytest.MonkeyPatch
) -> None:
        """A refused token shows the API's reason, not a crash."""
        monkeypatch.setenv("PRELOAD_BOT_TOKEN", "wrong")
        with pytest.raises(PreloadError, match="missing or wrong token"):
                await cog.search("alien", series_only=False)


# Creating requests ----------------------------------------------------


@pytest.mark.parametrize(
        ("kind", "spec", "jellyfin", "sent"),
        [
                (
                        "movie",
                        None,
                        None,
                        {"itemId": "x", "requestedBy": "discord:doge"},
                ),
                # A movie ignores episode specs
                (
                        "movie",
                        EpisodeSpec(2, (8,)),
                        None,
                        {"itemId": "x", "requestedBy": "discord:doge"},
                ),
                (
                        "series",
                        EpisodeSpec(2, (8, 9, 10)),
                        None,
                        {
                                "itemId": "x",
                                "requestedBy": "discord:doge",
                                "season": 2,
                                "episodes": [8, 9, 10],
                        },
                ),
                (
                        "series",
                        EpisodeSpec(season=3),
                        None,
                        {
                                "itemId": "x",
                                "requestedBy": "discord:doge",
                                "season": 3,
                        },
                ),
                (
                        "series",
                        EpisodeSpec(next=4),
                        "doge_jf",
                        {
                                "itemId": "x",
                                "requestedBy": "discord:doge",
                                "next": 4,
                                "jellyfinUser": "doge_jf",
                        },
                ),
        ],
)
async def test_create_sends_what_was_asked_for(  # noqa: PLR0913 (table)
        cog: Watch,
        fake: FakePreload,
        kind: str,
        spec: EpisodeSpec | None,
        jellyfin: str | None,
        sent: dict[str, Any],
) -> None:
        """The request body matches the item type and episode spec."""
        fake.search_results = [media_item("x", "X", kind=kind)]
        (item,) = await cog.search("X", series_only=False)

        await cog.create(item, spec, cast("Any", FakeAuthor()), jellyfin)

        assert fake.calls_to("POST", "/v1/requests") == [sent]


# d!watch ----------------------------------------------------------------


async def test_watch_starts_an_exact_match(
        cog: Watch, fake: FakePreload
) -> None:
        """One exact title match is requested without asking."""
        fake.search_results = [
                media_item("m1", "The Matrix", year=1999),
                media_item("m2", "The Matrix Reloaded", year=2003),
        ]
        ctx = FakeContext()

        await run_watch(cog, ctx, "the matrix")

        assert fake.calls_to("POST", "/v1/requests") == [
                {"itemId": "m1", "requestedBy": "discord:doge"}
        ]
        assert ctx.texts == ["Preloading The Matrix"]


@pytest.mark.parametrize(
        ("results", "reply"),
        [
                ([], "I couldn't find **Nothing Here**."),
                (
                        [media_item("x", "Nothing Here", cacheable=False)],
                        "Nothing cacheable matches **Nothing Here**.",
                ),
        ],
)
async def test_watch_explains_when_there_is_nothing_to_preload(
        cog: Watch,
        fake: FakePreload,
        results: list[dict[str, Any]],
        reply: str,
) -> None:
        """No match and no cacheable match get different answers."""
        fake.search_results = results
        ctx = FakeContext()

        await run_watch(cog, ctx, "Nothing Here")

        assert ctx.texts == [reply]
        assert fake.calls_to("POST", "/v1/requests") == []


async def test_watch_next_needs_a_linked_jellyfin_user(
        cog: Watch, fake: FakePreload
) -> None:
        """Asking for "next" without `watch link` explains how to link."""
        fake.search_results = [
                media_item("s", "Dark Matter", kind="series", seasons=2)
        ]
        ctx = FakeContext()

        await run_watch(cog, ctx, "Dark Matter next")

        assert ctx.texts == [
                (
                        "For **next**, tell me your Jellyfin name with "
                        "`d!watch link <name>`."
                )
        ]
        assert fake.calls_to("POST", "/v1/requests") == []


async def test_watch_next_uses_the_linked_jellyfin_user(
        cog: Watch, fake: FakePreload
) -> None:
        """After `watch link`, "next" requests with that Jellyfin name."""
        fake.search_results = [
                media_item("s", "Dark Matter", kind="series", seasons=2)
        ]
        ctx = FakeContext()
        await Watch.watch_link.callback(cog, cast("Any", ctx), name=" doge_jf ")

        await run_watch(cog, ctx, "Dark Matter next 2")

        assert fake.calls_to("POST", "/v1/requests") == [
                {
                        "itemId": "s",
                        "requestedBy": "discord:doge",
                        "next": 2,
                        "jellyfinUser": "doge_jf",
                }
        ]


async def test_watch_reports_a_bad_episode_spec(
        cog: Watch, fake: FakePreload
) -> None:
        """A malformed spec is explained and nothing is searched."""
        ctx = FakeContext()

        await run_watch(cog, ctx, "Dark Matter s2e10-8")

        assert ctx.texts[0].startswith("I didn't get the episodes:")
        assert fake.calls == []


async def test_watch_reports_a_failed_search(
        cog: Watch, monkeypatch: pytest.MonkeyPatch
) -> None:
        """An API failure is shown to the user instead of raising."""
        monkeypatch.setenv("PRELOAD_BOT_TOKEN", "wrong")
        ctx = FakeContext()

        await run_watch(cog, ctx, "The Matrix")

        assert ctx.texts == ["Search failed: missing or wrong token"]


# d!watch status / cancel ------------------------------------------------


async def test_status_lists_the_latest_requests(
        cog: Watch, fake: FakePreload
) -> None:
        """Newest first, with id, title, status and requester."""
        fake.requests = {
                1: preload_request(1, "Alien", requested_by="discord:a"),
                2: preload_request(
                        2, "Dune", status="fetching", requested_by="discord:b"
                ),
        }
        ctx = FakeContext()

        await Watch.watch_status.callback(cog, cast("Any", ctx))

        assert ctx.texts == [
                (
                        "`#2` **Dune** · fetching · discord:b\n"
                        "`#1` **Alien** · done · discord:a"
                )
        ]


async def test_status_with_no_requests(cog: Watch) -> None:
        """An empty history says so."""
        ctx = FakeContext()

        await Watch.watch_status.callback(cog, cast("Any", ctx))

        assert ctx.texts == ["No preloads yet."]


async def test_status_of_a_missing_request(cog: Watch) -> None:
        """An unknown id shows the API's message."""
        ctx = FakeContext()

        await Watch.watch_status.callback(cog, cast("Any", ctx), request_id=9)

        assert ctx.texts == ["Couldn't get that: no such request"]


async def test_only_the_requester_can_cancel(
        cog: Watch, fake: FakePreload
) -> None:
        """Someone else's request is refused and never sent to the API."""
        fake.requests = {
                5: preload_request(5, "Dune", requested_by="discord:other")
        }
        ctx = FakeContext()

        await Watch.watch_cancel.callback(cog, cast("Any", ctx), request_id=5)

        assert ctx.texts == [
                "Only the requester or a moderator can cancel that."
        ]
        assert fake.calls_to("DELETE", "/v1/requests/5") == []


async def test_requester_cancels_their_request(
        cog: Watch, fake: FakePreload
) -> None:
        """The requester's cancel reaches the API and shows the result."""
        fake.requests = {
                5: preload_request(
                        5, "Dune", status="queued", requested_by="discord:doge"
                )
        }
        ctx = FakeContext()

        await Watch.watch_cancel.callback(cog, cast("Any", ctx), request_id=5)

        assert len(fake.calls_to("DELETE", "/v1/requests/5")) == 1
        assert ctx.sent[0].embed is not None
        assert ctx.sent[0].embed.footer.text == "Request #5 · canceled"
