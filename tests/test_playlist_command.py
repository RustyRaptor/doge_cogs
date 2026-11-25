"""Test playlist permissions, confirmation and job scope."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from music_helpers import MusicEdge, music_job
from redbot.core import Config
from test_background_jobs import Notifications
from watch_helpers import FakeContext

from watch.watch import Watch


@dataclass
class PlaylistBot:
        """Discord/service boundaries for command tests."""

        url: str
        channel: Notifications
        owner: bool = True

        async def is_owner(self, _user: object) -> bool:
                """Return the configured owner permission."""
                return self.owner

        async def get_shared_api_tokens(self, _service: str) -> dict[str, str]:
                """Route service calls to the HTTP edge."""
                return {"url": self.url, "api_key": "test-key"}

        def get_channel(self, _channel_id: int) -> Notifications:
                """Return the notification channel."""
                return self.channel


CommandCase = tuple[Watch, FakeContext, MusicEdge, Notifications, PlaylistBot]


@pytest.fixture
async def playlist_command(
        config: Config, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[CommandCase]:
        """Keep configuration and all service traffic real up to HTTP."""

        class Edge(MusicEdge):
                async def http(self, request: web.Request) -> web.Response:
                        if request.path == "/Users":
                                return web.json_response(
                                        [
                                                {
                                                        "Id": "user-1",
                                                        "Name": "doge",
                                                        "Policy": {
                                                                "IsDisabled": False  # noqa: E501
                                                        },
                                                }
                                        ]
                                )
                        return await super().http(request)

        edge = Edge(ready={"Halo", "Enjoy the Silence"})
        app = web.Application()
        app.router.add_route("*", "/{path:.*}", edge.http)
        async with TestServer(app) as server:
                channel = Notifications()
                bot = PlaylistBot(str(server.make_url("/")), channel)
                monkeypatch.setattr(
                        Config, "get_conf", lambda *_a, **_k: config
                )
                cog = Watch(cast("Any", bot))
                ctx = FakeContext()
                ctx.channel = SimpleNamespace(id=10)
                await config.user_from_id(1).jellyfin_user.set("doge")
                yield cog, ctx, edge, channel, bot
                await cog.cog_unload()


async def test_playlist_permission_precedes_service_calls(
        playlist_command: CommandCase,
) -> None:
        """Catch music download permission bypasses."""
        cog, ctx, edge, _channel, bot = playlist_command
        bot.owner = False
        await Watch.playlist.callback(
                cog,
                ctx,
                url="https://open.spotify.com/playlist/7kRUsOsraSiY4fyDcu7Bnb",
        )
        assert "permission" in ctx.texts[0].lower()
        assert edge.playlist == {}


async def test_playlist_requires_a_linked_jellyfin_account(
        playlist_command: CommandCase,
) -> None:
        """Catch silently choosing an admin playlist owner."""
        cog, ctx, edge, _channel, _bot = playlist_command
        await cog.config.user_from_id(1).jellyfin_user.clear()
        await Watch.playlist.callback(
                cog,
                ctx,
                url="https://open.spotify.com/playlist/7kRUsOsraSiY4fyDcu7Bnb",
        )
        assert "watch link" in ctx.texts[0]
        assert edge.playlist == {}


async def test_playlist_has_username_prefix_and_pings_when_ready(
        playlist_command: CommandCase, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch acknowledging without importing."""
        from watch.spotify import SpotifyPublic

        cog, ctx, edge, channel, _bot = playlist_command

        async def snapshot(_self: object, _url: str) -> dict[str, Any]:
                return {
                        "id": "7kRUsOsraSiY4fyDcu7Bnb",
                        "name": "My mix",
                        "tracks": music_job()["tracks"],
                        "partial": False,
                }

        async def confirm(*_args: object) -> str:
                return "import"

        monkeypatch.setattr(SpotifyPublic, "snapshot", snapshot)
        monkeypatch.setattr(cog, "_download_choice", confirm)
        await Watch.playlist.callback(
                cog,
                ctx,
                url="https://open.spotify.com/playlist/7kRUsOsraSiY4fyDcu7Bnb?si=share",
        )
        await asyncio.gather(*cog._pollers)
        assert edge.playlist["Name"] == "doge - My mix"
        assert edge.playlist["IsPublic"] is True
        assert edge.playlist["Ids"] == ["Halo", "Enjoy the Silence", "Halo"]
        assert len(channel.sent) == 1
        assert channel.sent[0][0].startswith("<@1>")
        assert "3/3" in channel.sent[0][0]
        jobs = await cog.config.pending_jobs()
        assert len(jobs) == 1
        assert (
                next(iter(jobs.values()))["source_id"]
                == "7kRUsOsraSiY4fyDcu7Bnb"
        )


async def test_public_limit_requires_an_explicit_visible_subset_confirmation(
        playlist_command: CommandCase, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch silently treating a capped public playlist as complete."""
        from watch.spotify import SpotifyPublic

        cog, ctx, edge, _channel, _bot = playlist_command
        prompts = []

        async def snapshot(_self: object, _url: str) -> dict[str, Any]:
                return {
                        "id": "7kRUsOsraSiY4fyDcu7Bnb",
                        "name": "Long mix",
                        "tracks": [music_job()["tracks"][0]] * 100,
                        "partial": True,
                }

        async def cancel(
                _ctx: object, text: str, choices: list[tuple[str, str]]
        ) -> None:
                prompts.append((text, choices))
                return

        monkeypatch.setattr(SpotifyPublic, "snapshot", snapshot)
        monkeypatch.setattr(cog, "_download_choice", cancel)
        await Watch.playlist.callback(
                cog,
                ctx,
                url="https://open.spotify.com/playlist/7kRUsOsraSiY4fyDcu7Bnb",
        )
        assert "100" in prompts[0][0]
        assert "incomplete" in prompts[0][0].lower()
        assert ("visible", "Import visible 100 only") in prompts[0][1]
        assert edge.playlist == {}
        assert await cog.config.pending_jobs() == {}


async def test_revocation_during_confirmation_does_not_queue_an_import(
        playlist_command: CommandCase, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch revoked permission after confirmation."""
        from watch.spotify import SpotifyPublic

        cog, ctx, edge, _channel, bot = playlist_command
        bot.owner = False
        await cog.config.user_from_id(1).download_allowed.set(True)

        async def snapshot(_self: object, _url: str) -> dict[str, Any]:
                return {
                        "id": "7kRUsOsraSiY4fyDcu7Bnb",
                        "name": "My mix",
                        "tracks": music_job()["tracks"],
                        "partial": False,
                }

        async def revoke(*_args: object) -> str:
                await cog.config.user_from_id(1).download_allowed.clear()
                return "import"

        monkeypatch.setattr(SpotifyPublic, "snapshot", snapshot)
        monkeypatch.setattr(cog, "_download_choice", revoke)
        await Watch.playlist.callback(
                cog,
                ctx,
                url="https://open.spotify.com/playlist/7kRUsOsraSiY4fyDcu7Bnb",
        )
        assert edge.playlist == {}
        assert await cog.config.pending_jobs() == {}
        assert any("permission" in text.lower() for text in ctx.texts)


async def test_reimport_updates_the_existing_playlist(
        playlist_command: CommandCase, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch creating another public playlist for the same owner/source."""
        from watch.spotify import SpotifyPublic

        cog, ctx, edge, _channel, _bot = playlist_command

        async def snapshot(_self: object, _url: str) -> dict[str, Any]:
                return {
                        "id": "7kRUsOsraSiY4fyDcu7Bnb",
                        "name": "My mix",
                        "tracks": music_job()["tracks"],
                        "partial": False,
                }

        async def confirm(*_args: object) -> str:
                return "import"

        monkeypatch.setattr(SpotifyPublic, "snapshot", snapshot)
        monkeypatch.setattr(cog, "_download_choice", confirm)
        for _ in range(2):
                await Watch.playlist.callback(
                        cog,
                        ctx,
                        url="https://open.spotify.com/playlist/7kRUsOsraSiY4fyDcu7Bnb",
                )
                await asyncio.gather(*cog._pollers)
        assert edge.creations == 1


async def test_playlist_status_scopes_jobs_and_lists_review_tracks(
        playlist_command: CommandCase,
) -> None:
        """Catch leaking other users' jobs or hiding unresolved song names."""
        cog, ctx, _edge, _channel, _bot = playlist_command
        cog.background()
        await cog.config.pending_jobs.set(
                {
                        "one": {
                                "owner": 1,
                                "kind": "playlist",
                                "name": "Own mix",
                                "stage": "attention",
                                "ready": 1,
                                "tracks": [
                                        {
                                                "title": "Missing song",
                                                "review": "No match",
                                        },
                                        {},
                                ],
                        },
                        "two": {
                                "owner": 2,
                                "kind": "playlist",
                                "name": "Other user job",
                                "stage": "waiting",
                                "ready": 0,
                                "tracks": [],
                        },
                }
        )
        await Watch.playlist_status.callback(cog, ctx)
        assert "Own mix" in ctx.texts[0]
        assert "1/2" in ctx.texts[0]
        assert "Missing song" in ctx.texts[0]
        assert "Other user" not in ctx.texts[0]


async def test_playlist_identity_survives_completed_job_cleanup(
        playlist_command: CommandCase, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch duplicates after completion records expire."""
        from watch.spotify import SpotifyPublic

        cog, ctx, edge, _channel, _bot = playlist_command

        async def snapshot(_self: object, _url: str) -> dict[str, Any]:
                return {
                        "id": "7kRUsOsraSiY4fyDcu7Bnb",
                        "name": "My mix",
                        "tracks": music_job()["tracks"],
                        "partial": False,
                }

        async def confirm(*_args: object) -> str:
                return "import"

        monkeypatch.setattr(SpotifyPublic, "snapshot", snapshot)
        monkeypatch.setattr(cog, "_download_choice", confirm)
        await Watch.playlist.callback(
                cog,
                ctx,
                url="https://open.spotify.com/playlist/7kRUsOsraSiY4fyDcu7Bnb",
        )
        await asyncio.gather(*cog._pollers)
        await cog.config.pending_jobs.set({})
        await Watch.playlist.callback(
                cog,
                ctx,
                url="https://open.spotify.com/playlist/7kRUsOsraSiY4fyDcu7Bnb",
        )
        await asyncio.gather(*cog._pollers)
        assert edge.creations == 1


async def test_uncertain_prior_create_requires_verification_before_reimport(
        playlist_command: CommandCase, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch silently duplicating a prior unacknowledged playlist create."""
        from watch.spotify import SpotifyPublic

        cog, ctx, edge, _channel, _bot = playlist_command
        cog.background()
        await cog.config.pending_jobs.set(
                {
                        "1:playlist:7kRUsOsraSiY4fyDcu7Bnb": {
                                "owner": 1,
                                "notified": True,
                                "stage": "attention",
                                "kind": "playlist",
                                "mutation": "Jellyfin playlist create",
                        }
                }
        )

        async def snapshot(_self: object, _url: str) -> dict[str, Any]:
                return {
                        "id": "7kRUsOsraSiY4fyDcu7Bnb",
                        "name": "Mix",
                        "tracks": music_job()["tracks"],
                        "partial": False,
                }

        async def confirm(*_args: object) -> str:
                return "import"

        monkeypatch.setattr(SpotifyPublic, "snapshot", snapshot)
        monkeypatch.setattr(cog, "_download_choice", confirm)
        await Watch.playlist.callback(
                cog,
                ctx,
                url="https://open.spotify.com/playlist/7kRUsOsraSiY4fyDcu7Bnb",
        )
        await asyncio.gather(*cog._pollers)
        assert edge.creations == 0
        assert any("verification" in text for text in ctx.texts)


async def test_playlist_verify_clears_mutation_and_allows_reimport(
        playlist_command: CommandCase,
) -> None:
        """Catch a dead end after an uncertain playlist write."""
        cog, ctx, _edge, _channel, _bot = playlist_command
        key = "1:playlist:7kRUsOsraSiY4fyDcu7Bnb"
        await cog.config.pending_jobs.set(
                {
                        key: {
                                "owner": 1,
                                "notified": True,
                                "stage": "attention",
                                "kind": "playlist",
                                "mutation": "Jellyfin playlist create",
                        }
                }
        )
        await Watch.playlist_verify.callback(cog, ctx)
        job = (await cog.config.pending_jobs())[key]
        assert "mutation" not in job
        assert any("Verified" in text for text in ctx.texts)


async def test_playlist_status_names_review_reasons_and_retry(
        playlist_command: CommandCase,
) -> None:
        """Catch review output that neither explains nor resolves gaps."""
        cog, ctx, _edge, _channel, _bot = playlist_command
        cog.background()
        await cog.config.pending_jobs.set(
                {
                        "1:playlist:mine": {
                                "owner": 1,
                                "kind": "playlist",
                                "name": "Own mix",
                                "stage": "attention",
                                "ready": 1,
                                "tracks": [
                                        {
                                                "title": "Halo",
                                                "resolved": True,
                                                "review": (
                                                        "No unique "
                                                        "artist/album match"
                                                ),
                                        },
                                        {
                                                "title": "Enjoy the Silence",
                                                "resolved": True,
                                                "review": (
                                                        "No unique "
                                                        "artist/album match"
                                                ),
                                        },
                                        {
                                                "title": "Policy of Truth",
                                                "resolved": True,
                                                "review": "Public album "
                                                "metadata unavailable",
                                        },
                                        {
                                                "title": "Ready one",
                                                "resolved": True,
                                        },
                                ],
                        }
                }
        )
        await Watch.playlist_status.callback(cog, ctx)
        text = ctx.texts[0]
        assert "No unique artist/album match (2)" in text
        assert "Public album metadata unavailable (1)" in text
        assert "Halo" in text and "Policy of Truth" in text
        assert "Ready one" not in text
        assert "importplaylist retry" in text


async def test_playlist_status_keeps_the_retry_hint_on_long_lists(
        playlist_command: CommandCase,
) -> None:
        """Catch truncation cutting off the only instruction that helps."""
        cog, ctx, _edge, _channel, _bot = playlist_command
        cog.background()
        reasons = [
                "No unique artist/album match",
                "Public album metadata unavailable",
                "Catalogue lookup failed",
        ]
        await cog.config.pending_jobs.set(
                {
                        f"1:playlist:{i}": {
                                "owner": 1,
                                "kind": "playlist",
                                "name": f"Long mix {i}",
                                "stage": "attention",
                                "ready": 0,
                                "tracks": [
                                        {
                                                "title": "Reviewed song "
                                                f"{reason[:8]} {n}",
                                                "resolved": True,
                                                "review": reason,
                                        }
                                        for reason in reasons
                                        for n in range(10)
                                ],
                        }
                        for i in range(5)
                }
        )
        await Watch.playlist_status.callback(cog, ctx)
        text = ctx.texts[0]
        assert len(text) <= 1900
        assert text.endswith("to try reviewed songs again.")
        assert "importplaylist retry" in text


async def test_retry_recovers_review_tracks_end_to_end(
        playlist_command: CommandCase, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch a review list with no way to turn it into a finished import."""
        import watch.jobs as job_module
        from watch.spotify import SpotifyPublic

        cog, ctx, edge, channel, _bot = playlist_command
        tracks = music_job()["tracks"][:2]

        async def snapshot(_self: object, _url: str) -> dict[str, Any]:
                return {
                        "id": "7kRUsOsraSiY4fyDcu7Bnb",
                        "name": "My mix",
                        "tracks": tracks,
                        "partial": False,
                }

        async def confirm(*_args: object) -> str:
                return "import"

        class Catalogue:
                def __init__(self, *_args: object, **_kwargs: object) -> None:
                        pass

                async def album_for_track(self, _track: str) -> dict[str, str]:
                        return {
                                "id": "spotify-album",
                                "title": "Violator",
                                "artist": "Depeche Mode",
                        }

        monkeypatch.setattr(SpotifyPublic, "snapshot", snapshot)
        monkeypatch.setattr(job_module, "SpotifyPublic", Catalogue)
        monkeypatch.setattr(cog, "_download_choice", confirm)
        edge.ready = set()
        edge.lookup_available = False
        await Watch.playlist.callback(
                cog,
                ctx,
                url="https://open.spotify.com/playlist/7kRUsOsraSiY4fyDcu7Bnb",
        )
        await asyncio.gather(*cog._pollers)
        jobs = cog.background()
        for _ in range(2):
                await jobs.tick()
        state = next(iter((await cog.config.pending_jobs()).values()))
        assert state["stage"] == "attention"
        assert {track["review"] for track in state["tracks"]} == {
                "Catalogue lookup failed"
        }
        assert len(channel.sent) == 1
        await Watch.playlist_status.callback(cog, ctx)
        assert "Catalogue lookup failed (2)" in ctx.texts[-1]

        edge.lookup_available = True
        await Watch.playlist_retry.callback(cog, ctx)
        await asyncio.gather(*cog._pollers)
        assert edge.searches == [[7]]
        assert edge.creations == 1
        edge.ready = {"Halo", "Enjoy the Silence"}
        await jobs.tick()
        state = next(iter((await cog.config.pending_jobs()).values()))
        assert state["stage"] == "complete"
        assert state["ready"] == 2
        assert all("review" not in track for track in state["tracks"])
        assert edge.playlist["Ids"] == ["Halo", "Enjoy the Silence"]
        assert edge.creations == 1
        assert len(channel.sent) == 2
        assert "2/2" in channel.sent[-1][0]


async def test_match_assigns_the_chosen_album_end_to_end(
        playlist_command: CommandCase, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch an album choice that never reaches the import."""
        from datetime import UTC, datetime

        cog, ctx, edge, channel, _bot = playlist_command
        key = "1:playlist:7kRUsOsraSiY4fyDcu7Bnb"
        await cog.config.pending_jobs.set(
                {
                        key: {
                                "owner": 1,
                                "username": "doge",
                                "channel": 10,
                                "user_id": "user-1",
                                "kind": "playlist",
                                "name": "My mix",
                                "stage": "attention",
                                "ready": 0,
                                "notified": True,
                                "partial": False,
                                "created": datetime.now(UTC).timestamp(),
                                "tracks": [
                                        {
                                                "id": "6kwEU2hLQpMToj1PHsBrOE",
                                                "title": "Halo",
                                                "artist": "Depeche Mode",
                                                "duration": 270186,
                                                "resolved": True,
                                                "review": "No unique "
                                                "artist/album match",
                                        }
                                ],
                        }
                }
        )

        async def choose(
                _ctx: object, _text: str, options: list[tuple[str, str]]
        ) -> str:
                assert ("mb-album", "Violator (?) · Depeche Mode") in options
                return "mb-album"

        monkeypatch.setattr(cog, "_download_choice", choose)
        edge.ready = set()
        await Watch.playlist_match.callback(cog, ctx, song="Halo")
        await asyncio.gather(*cog._pollers)
        job = (await cog.config.pending_jobs())[key]
        assert job["tracks"][0]["album_id"] == 7
        assert "review" not in job["tracks"][0]
        assert job["notified"] is False
        assert edge.albums[0]["monitored"] is True
        assert edge.searches == [[7]]
        edge.ready = {"Halo"}
        await cog.background().tick()
        job = (await cog.config.pending_jobs())[key]
        assert job["stage"] == "complete"
        assert len(channel.sent) == 1
        assert "1/1" in channel.sent[0][0]


async def test_match_ignores_another_users_reviewed_song(
        playlist_command: CommandCase,
) -> None:
        """Catch choosing an album for someone else's import."""
        cog, ctx, edge, _channel, _bot = playlist_command
        key = "2:playlist:7kRUsOsraSiY4fyDcu7Bnb"
        await cog.config.pending_jobs.set(
                {
                        key: {
                                "owner": 2,
                                "kind": "playlist",
                                "name": "Their mix",
                                "stage": "attention",
                                "notified": True,
                                "tracks": [
                                        {
                                                "title": "Halo",
                                                "resolved": True,
                                                "review": "No unique "
                                                "artist/album match",
                                        }
                                ],
                        }
                }
        )
        await Watch.playlist_match.callback(cog, ctx, song="Halo")
        job = (await cog.config.pending_jobs())[key]
        assert "album_id" not in job["tracks"][0]
        assert job["tracks"][0]["review"] == "No unique artist/album match"
        assert edge.albums == []
        assert any("no reviewed song" in text.lower() for text in ctx.texts)


async def test_match_cancelled_album_choice_changes_nothing(
        playlist_command: CommandCase, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch acting after the requester cancels the album menu."""
        cog, ctx, edge, _channel, _bot = playlist_command
        key = "1:playlist:7kRUsOsraSiY4fyDcu7Bnb"
        await cog.config.pending_jobs.set(
                {
                        key: {
                                "owner": 1,
                                "kind": "playlist",
                                "name": "My mix",
                                "stage": "attention",
                                "notified": True,
                                "tracks": [
                                        {
                                                "title": "Halo",
                                                "resolved": True,
                                                "review": "No unique "
                                                "artist/album match",
                                        }
                                ],
                        }
                }
        )

        async def cancel(*_args: object) -> None:
                return None

        monkeypatch.setattr(cog, "_download_choice", cancel)
        await Watch.playlist_match.callback(cog, ctx, song="Halo")
        job = (await cog.config.pending_jobs())[key]
        assert "album_id" not in job["tracks"][0]
        assert job["tracks"][0]["review"] == "No unique artist/album match"
        assert job["notified"] is True
        assert edge.albums == []
        assert edge.searches == []


async def test_retry_only_touches_the_callers_imports(
        playlist_command: CommandCase,
) -> None:
        """Catch one person retrying another person's review tracks."""
        cog, ctx, _edge, _channel, _bot = playlist_command
        key = "2:playlist:7kRUsOsraSiY4fyDcu7Bnb"
        await cog.config.pending_jobs.set(
                {
                        key: {
                                "owner": 2,
                                "kind": "playlist",
                                "name": "Their mix",
                                "stage": "attention",
                                "ready": 0,
                                "notified": True,
                                "tracks": [
                                        {
                                                "title": "Halo",
                                                "resolved": True,
                                                "review": "No unique "
                                                "artist/album match",
                                        }
                                ],
                        }
                }
        )
        await Watch.playlist_retry.callback(cog, ctx)
        job = (await cog.config.pending_jobs())[key]
        assert job["stage"] == "attention"
        assert job["tracks"][0]["review"] == "No unique artist/album match"
        assert any("no " in text.lower() for text in ctx.texts)


async def test_retry_refuses_an_uncertain_write(
        playlist_command: CommandCase,
) -> None:
        """Catch retrying around a create whose acknowledgement was lost."""
        cog, ctx, _edge, _channel, _bot = playlist_command
        key = "1:playlist:7kRUsOsraSiY4fyDcu7Bnb"
        await cog.config.pending_jobs.set(
                {
                        key: {
                                "owner": 1,
                                "kind": "playlist",
                                "name": "My mix",
                                "stage": "attention",
                                "ready": 0,
                                "notified": True,
                                "mutation": "Jellyfin playlist create",
                                "tracks": [
                                        {
                                                "title": "Halo",
                                                "resolved": True,
                                                "review": "No unique "
                                                "artist/album match",
                                        }
                                ],
                        }
                }
        )
        await Watch.playlist_retry.callback(cog, ctx)
        job = (await cog.config.pending_jobs())[key]
        assert job["mutation"] == "Jellyfin playlist create"
        assert job["tracks"][0]["review"] == "No unique artist/album match"
        assert any("verify" in text.lower() for text in ctx.texts)


async def test_playlist_verify_is_owner_only(
        playlist_command: CommandCase,
) -> None:
        """Catch a non-owner clearing an uncertain write."""
        cog, ctx, _edge, _channel, bot = playlist_command
        bot.owner = False
        key = "1:playlist:7kRUsOsraSiY4fyDcu7Bnb"
        await cog.config.pending_jobs.set(
                {
                        key: {
                                "owner": 1,
                                "notified": True,
                                "stage": "attention",
                                "kind": "playlist",
                                "mutation": "Jellyfin playlist create",
                        }
                }
        )
        await Watch.playlist_verify.callback(cog, ctx)
        job = (await cog.config.pending_jobs())[key]
        assert job["mutation"] == "Jellyfin playlist create"
        assert any("Only the bot owner" in text for text in ctx.texts)
