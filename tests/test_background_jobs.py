"""Durable completion subscriptions around the real Watch service clients."""

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from redbot.core import Config
from watch_helpers import FakeContext, preload_request

from watch.watch import Watch


@dataclass
class Notifications:
        """Record Discord deliveries and their mention policy."""

        sent: list[Any] = field(default_factory=list)

        async def send(self, content: str, **kwargs: Any) -> None:  # noqa: ANN401
                """Record a Discord delivery."""
                self.sent.append((content, kwargs))


async def test_movie_completion_survives_restart_and_is_not_repeated(
        config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch lost, premature or repeated movie notifications."""
        from watch.jobs import Jobs

        ready = [False]

        async def handler(request: web.Request) -> web.Response:
                if request.path == "/api/v3/movie":
                        return web.json_response(
                                [
                                        {
                                                "id": 7,
                                                "tmdbId": 101,
                                                "title": "Dune",
                                                "hasFile": ready[0],
                                        }
                                ]
                        )
                return web.Response(status=404)

        app = web.Application()
        app.router.add_route("*", "/{path:.*}", handler)
        async with TestServer(app) as server:
                channel = Notifications()

                class Bot:
                        async def get_shared_api_tokens(
                                self, _service: str
                        ) -> dict[str, str]:
                                return {
                                        "url": str(server.make_url("/")),
                                        "api_key": "test-key",
                                }

                        def get_channel(self, _id: int) -> Notifications:
                                return channel

                monkeypatch.setattr(
                        Config, "get_conf", lambda *_a, **_k: config
                )
                cog = Watch(cast("Any", Bot()))
                ctx = FakeContext()
                ctx.channel = SimpleNamespace(id=10)
                jobs = Jobs(cog)
                await jobs.register(
                        cast("Any", ctx),
                        "movie:101",
                        {"kind": "movie", "foreign_id": 101, "title": "Dune"},
                )
                await jobs.tick()
                assert channel.sent == []
                ready[0] = True
                resumed = Jobs(cog)
                await resumed.tick()
                assert len(channel.sent) == 1
                assert channel.sent[0][0].startswith("<@1>")
                assert "Dune" in channel.sent[0][0]
                await resumed.tick()
                assert len(channel.sent) == 1


async def test_season_completion_uses_only_the_requested_season(
        config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch using whole-show counts for a season request."""
        from watch.jobs import Jobs

        ready = [False]

        async def handler(request: web.Request) -> web.Response:
                if request.path == "/api/v3/series":
                        return web.json_response(
                                [{"id": 7, "tvdbId": 101, "title": "Show"}]
                        )
                if (
                        request.path == "/api/v3/episode"
                        and request.query.get("seriesId") == "7"
                ):
                        return web.json_response(
                                [
                                        {
                                                "seasonNumber": 1,
                                                "hasFile": False,
                                                "airDateUtc": "2020-01-01T00:00:00Z",  # noqa: E501
                                        },
                                        {
                                                "seasonNumber": 2,
                                                "hasFile": ready[0],
                                                "airDateUtc": "2020-01-01T00:00:00Z",  # noqa: E501
                                        },
                                ]
                        )
                return web.Response(status=404)

        app = web.Application()
        app.router.add_route("*", "/{path:.*}", handler)
        async with TestServer(app) as server:
                channel = Notifications()

                class Bot:
                        async def get_shared_api_tokens(
                                self, _service: str
                        ) -> dict[str, str]:
                                return {
                                        "url": str(server.make_url("/")),
                                        "api_key": "test-key",
                                }

                        def get_channel(self, _id: int) -> Notifications:
                                return channel

                monkeypatch.setattr(
                        Config, "get_conf", lambda *_a, **_k: config
                )
                cog = Watch(cast("Any", Bot()))
                ctx = FakeContext()
                ctx.channel = SimpleNamespace(id=10)
                jobs = Jobs(cog)
                await jobs.register(
                        cast("Any", ctx),
                        "tv:101:2",
                        {
                                "kind": "tv",
                                "foreign_id": 101,
                                "title": "Show",
                                "season": 2,
                        },
                )
                await jobs.tick()
                assert channel.sent == []
                ready[0] = True
                await jobs.tick()
                assert len(channel.sent) == 1
                assert "season 2" in channel.sent[0][0]


async def test_preload_completion_pings_after_its_terminal_result(
        config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch dropping notifications for watch's cache/preload process."""
        from watch.jobs import Jobs

        status = ["queued"]

        async def handler(_request: web.Request) -> web.Response:
                return web.json_response(
                        preload_request(5, "Dune", status=status[0])
                )

        app = web.Application()
        app.router.add_get("/v1/requests/5", handler)
        async with TestServer(app) as server:
                channel = Notifications()

                class Bot:
                        async def get_shared_api_tokens(
                                self, _service: str
                        ) -> dict[str, str]:
                                return {
                                        "url": str(server.make_url("/")),
                                        "token": "test-bot-token",
                                }

                        def get_channel(self, _id: int) -> Notifications:
                                return channel

                monkeypatch.setattr(
                        Config, "get_conf", lambda *_a, **_k: config
                )
                monkeypatch.delenv("PRELOAD_API_URL", raising=False)
                monkeypatch.delenv("PRELOAD_BOT_TOKEN", raising=False)
                cog = Watch(cast("Any", Bot()))
                ctx = FakeContext()
                ctx.channel = SimpleNamespace(id=10)
                jobs = Jobs(cog)
                await jobs.register(
                        cast("Any", ctx),
                        "preload:5",
                        {"kind": "preload", "request_id": 5, "title": "Dune"},
                )
                await jobs.tick()
                assert channel.sent == []
                status[0] = "done"
                await jobs.tick()
                assert len(channel.sent) == 1
                assert "Preloading" in channel.sent[0][0]


async def test_userdata_deletion_removes_only_that_users_jobs(
        config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch retaining identities after data deletion."""
        from watch.jobs import Jobs

        monkeypatch.setattr(Config, "get_conf", lambda *_a, **_k: config)
        cog = Watch(cast("Any", SimpleNamespace()))
        Jobs(cog)
        await config.pending_jobs.set(
                {"one": {"owner": 1}, "two": {"owner": 2}}
        )
        await cog.red_delete_data_for_user(requester="user", user_id=1)
        assert await config.pending_jobs() == {"two": {"owner": 2}}


async def test_playlist_resumes_and_pings_after_all_songs_arrive(
        config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch pinging before all songs become playable."""
        import httpx
        from music_helpers import MusicEdge, music_job, public_page

        import watch.jobs as job_module
        from watch.jobs import Jobs
        from watch.spotify import SpotifyPublic

        edge = MusicEdge()
        app = web.Application()
        app.router.add_route("*", "/{path:.*}", edge.http)
        async with TestServer(app) as server:
                channel = Notifications()

                class Bot:
                        async def get_shared_api_tokens(
                                self, _service: str
                        ) -> dict[str, str]:
                                return {
                                        "url": str(server.make_url("/")),
                                        "api_key": "test-key",
                                }

                        async def is_owner(self, _user: object) -> bool:
                                return True

                        def get_channel(self, _id: int) -> Notifications:
                                return channel

                monkeypatch.setattr(
                        Config, "get_conf", lambda *_a, **_k: config
                )
                cog = Watch(cast("Any", Bot()))
                ctx = FakeContext()
                ctx.channel = SimpleNamespace(id=10)
                jobs = Jobs(cog)
                await jobs.register(
                        cast("Any", ctx),
                        "playlist:test",
                        {"kind": "playlist", **music_job()},
                )
                async with httpx.AsyncClient(
                        transport=httpx.MockTransport(public_page)
                ) as public:
                        monkeypatch.setattr(
                                job_module,
                                "SpotifyPublic",
                                lambda _client: SpotifyPublic(public),
                                raising=False,
                        )
                        await jobs.tick()
                        assert channel.sent == []
                        assert edge.searches == [[7]]
                        edge.ready.add("Halo")
                        await jobs.tick()
                        assert channel.sent == []
                        edge.ready.add("Enjoy the Silence")
                        resumed = Jobs(cog)
                        await resumed.tick()
                        assert len(channel.sent) == 1
                        assert "Jellyfin" in channel.sent[0][0]
                        assert "3/3" in channel.sent[0][0]
                        assert edge.playlist["Name"] == "doge - My mix"
                        assert edge.playlist["IsPublic"] is True
                        await resumed.tick()
                        assert len(channel.sent) == 1


def test_incomplete_playlist_result_does_not_claim_all_music_was_added() -> (
        None
):
        """Catch the completion message treating a review outcome as success."""
        from watch.jobs import Jobs

        text = Jobs.message(
                {
                        "kind": "playlist",
                        "stage": "attention",
                        "name": "My mix",
                        "ready": 2,
                        "tracks": [{}, {}, {}],
                }
        )
        assert "2/3" in text
        assert "gaps" in text.lower() or "review" in text.lower()
        assert "downloaded and imported" not in text


async def test_uncertain_music_write_stops_for_attention(
        config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch uncertain playlist creates being repeated."""
        from watch.jobs import Jobs

        channel = Notifications()

        class Bot:
                async def is_owner(self, _user: object) -> bool:
                        return True

                def get_channel(self, _id: int) -> Notifications:
                        return channel

        monkeypatch.setattr(Config, "get_conf", lambda *_a, **_k: config)
        cog = Watch(cast("Any", Bot()))
        ctx = FakeContext()
        ctx.channel = SimpleNamespace(id=10)
        jobs = Jobs(cog)
        await jobs.register(
                cast("Any", ctx),
                "playlist:test",
                {
                        "kind": "playlist",
                        "stage": "preparing",
                        "name": "Mix",
                        "tracks": [{}],
                        "mutation": "Jellyfin playlist create",
                },
        )
        await jobs.tick()
        assert len(channel.sent) == 1
        state = next(iter((await config.pending_jobs()).values()))
        assert state["stage"] == "attention"
        assert state["mutation"] == "Jellyfin playlist create"
        await jobs.tick()
        assert len(channel.sent) == 1


async def test_stalled_jobs_report_attention_and_old_notifications_are_pruned(
        config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch stuck jobs and stale identity retention."""
        from watch.jobs import Jobs

        channel = Notifications()

        class Bot:
                def get_channel(self, _id: int) -> Notifications:
                        return channel

        monkeypatch.setattr(Config, "get_conf", lambda *_a, **_k: config)
        cog = Watch(cast("Any", Bot()))
        jobs = Jobs(cog)
        await config.pending_jobs.set(
                {
                        "one": {
                                "owner": 1,
                                "channel": 10,
                                "kind": "movie",
                                "title": "Dune",
                                "stage": "waiting",
                                "created": 1,
                                "notified": False,
                        }
                }
        )
        await jobs.tick()
        assert len(channel.sent) == 1
        assert "attention" in channel.sent[0][0].lower()
        state = await config.pending_jobs()
        state["one"]["finished"] = 1
        await config.pending_jobs.set(state)
        await jobs.tick()
        assert await config.pending_jobs() == {}
        assert len(channel.sent) == 1


async def test_cog_load_resumes_notifications_and_unload_stops_the_worker(
        config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch durable records that are never checked after a bot restart."""
        import asyncio
        from datetime import UTC, datetime

        from watch.jobs import Jobs

        delivered = asyncio.Event()

        class Channel(Notifications):
                async def send(self, content: str, **kwargs: Any) -> None:  # noqa: ANN401
                        await super().send(content, **kwargs)
                        delivered.set()

        async def handler(_request: web.Request) -> web.Response:
                return web.json_response(
                        [
                                {
                                        "id": 7,
                                        "tmdbId": 101,
                                        "title": "Dune",
                                        "hasFile": True,
                                }
                        ]
                )

        app = web.Application()
        app.router.add_get("/api/v3/movie", handler)
        async with TestServer(app) as server:
                channel = Channel()

                class Bot:
                        async def get_shared_api_tokens(
                                self, _service: str
                        ) -> dict[str, str]:
                                return {
                                        "url": str(server.make_url("/")),
                                        "api_key": "test-key",
                                }

                        async def wait_until_red_ready(self) -> None:
                                return None

                        def get_channel(self, _id: int) -> Notifications:
                                return channel

                monkeypatch.setattr(
                        Config, "get_conf", lambda *_a, **_k: config
                )
                cog = Watch(cast("Any", Bot()))
                Jobs(cog)
                await config.pending_jobs.set(
                        {
                                "one": {
                                        "owner": 1,
                                        "channel": 10,
                                        "kind": "movie",
                                        "foreign_id": 101,
                                        "title": "Dune",
                                        "stage": "waiting",
                                        "created": datetime.now(
                                                UTC
                                        ).timestamp(),
                                        "notified": False,
                                }
                        }
                )
                await cog.cog_load()
                try:
                        await asyncio.wait_for(delivered.wait(), timeout=2)
                finally:
                        await cog.cog_unload()
                assert len(channel.sent) == 1
                assert not cog._pollers


async def test_slow_music_import_does_not_delay_another_commands_completion(
        config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch slow imports delaying movie notifications."""
        import asyncio

        import httpx
        from music_helpers import MusicEdge, music_job, public_page

        import watch.jobs as job_module
        from watch.jobs import Jobs
        from watch.spotify import SpotifyPublic

        release = asyncio.Event()
        notified = asyncio.Event()

        class Channel(Notifications):
                async def send(self, content: str, **kwargs: Any) -> None:  # noqa: ANN401
                        await super().send(content, **kwargs)
                        if "Dune" in content:
                                notified.set()

        class Edge(MusicEdge):
                async def http(self, request: web.Request) -> web.Response:
                        if request.path == "/api/v3/movie":
                                return web.json_response(
                                        [
                                                {
                                                        "id": 7,
                                                        "tmdbId": 101,
                                                        "title": "Dune",
                                                        "hasFile": True,
                                                }
                                        ]
                                )
                        return await super().http(request)

        async def slow_public(request: httpx.Request) -> httpx.Response:
                await release.wait()
                return public_page(request)

        edge = Edge()
        app = web.Application()
        app.router.add_route("*", "/{path:.*}", edge.http)
        async with TestServer(app) as server:
                channel = Channel()

                class Bot:
                        async def get_shared_api_tokens(
                                self, _service: str
                        ) -> dict[str, str]:
                                return {
                                        "url": str(server.make_url("/")),
                                        "api_key": "test-key",
                                }

                        async def is_owner(self, _user: object) -> bool:
                                return True

                        def get_channel(self, _id: int) -> Notifications:
                                return channel

                monkeypatch.setattr(
                        Config, "get_conf", lambda *_a, **_k: config
                )
                cog = Watch(cast("Any", Bot()))
                ctx = FakeContext()
                ctx.channel = SimpleNamespace(id=10)
                jobs = Jobs(cog)
                await jobs.register(
                        cast("Any", ctx),
                        "playlist:test",
                        {"kind": "playlist", **music_job()},
                )
                await jobs.register(
                        cast("Any", ctx),
                        "movie:101",
                        {"kind": "movie", "foreign_id": 101, "title": "Dune"},
                )
                async with httpx.AsyncClient(
                        transport=httpx.MockTransport(slow_public)
                ) as public:
                        monkeypatch.setattr(
                                job_module,
                                "SpotifyPublic",
                                lambda _client: SpotifyPublic(public),
                                raising=False,
                        )
                        task = asyncio.create_task(jobs.tick())
                        try:
                                await asyncio.wait_for(
                                        notified.wait(), timeout=1
                                )
                        finally:
                                release.set()
                                await task
