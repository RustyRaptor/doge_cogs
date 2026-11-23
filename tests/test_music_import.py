"""Test playlist acquisition and restart behavior through HTTP."""

import copy
from typing import Any, cast

import httpx
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from music_helpers import MusicEdge, music_job, public_page
from redbot.core import Config

from watch.jellyfin import Jellyfin
from watch.spotify import SpotifyPublic
from watch.watch import Watch


async def test_import_waits_and_resumes_without_duplicates(
        config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch broad searches and duplicate imports."""
        from watch.music import MusicImporter

        edge = MusicEdge()
        app = web.Application()
        app.router.add_route("*", "/{path:.*}", edge.http)
        async with TestServer(app) as server:

                class Bot:
                        async def get_shared_api_tokens(
                                self, _service: str
                        ) -> dict[str, str]:
                                return {
                                        "url": str(server.make_url("/")),
                                        "api_key": "test-key",
                                }

                monkeypatch.setattr(
                        Config, "get_conf", lambda *_a, **_k: config
                )
                bot = Bot()
                cog = Watch(cast("Any", bot))
                saved: dict[str, Any] = {}

                async def save(job: dict[str, Any]) -> None:
                        saved.clear()
                        saved.update(copy.deepcopy(job))

                async def allowed() -> None:
                        return None

                async def arr(path: str, **kwargs: Any) -> Any:  # noqa: ANN401
                        return await cog._arr_call("music", path, **kwargs)

                async def tokens() -> dict[str, str]:
                        return await bot.get_shared_api_tokens("jellyfin")

                async with httpx.AsyncClient(
                        transport=httpx.MockTransport(public_page)
                ) as public:
                        engine = MusicImporter(
                                arr,
                                Jellyfin(tokens),
                                SpotifyPublic(public),
                                save,
                                allowed,
                        )
                        job = music_job()
                        await engine.prepare(job)
                        assert job["stage"] == "waiting"
                        assert len(edge.albums) == 1
                        assert edge.albums[0]["monitored"] is True
                        assert edge.artists[0]["addOptions"] == {
                                "monitor": "none",
                                "searchForMissingAlbums": False,
                        }
                        assert edge.artists[0]["monitorNewItems"] == "none"
                        assert edge.searches == [[7]]
                        edge.ready.add("Halo")
                        await engine.refresh(job)
                        assert job["stage"] == "waiting"
                        assert edge.playlist["Name"] == "doge - My mix"
                        assert edge.playlist["IsPublic"] is True
                        assert edge.playlist["Ids"] == ["Halo", "Halo"]
                        job = copy.deepcopy(saved)
                        resumed = MusicImporter(
                                arr,
                                Jellyfin(tokens),
                                SpotifyPublic(public),
                                save,
                                allowed,
                        )
                        await resumed.prepare(job)
                        edge.ready.add("Enjoy the Silence")
                        await resumed.refresh(job)
                        assert job["stage"] == "complete"
                        assert edge.playlist["Ids"] == [
                                "Halo",
                                "Enjoy the Silence",
                                "Halo",
                        ]
                        assert edge.creations == 1
                        assert edge.searches == [[7]]


async def test_revoked_permission_blocks_the_first_write_after_lookup(
        config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch checking permission only at command entry."""
        import pytest
        from music_helpers import import_environment

        edge = MusicEdge()
        async with import_environment(config, monkeypatch, edge) as (
                engine,
                _saved,
                allowed,
        ):

                async def revoke() -> None:
                        allowed[0] = False

                edge.after_lookup = revoke
                with pytest.raises(PermissionError):
                        await engine.prepare(music_job())
                assert edge.albums == []
                assert edge.searches == []


async def test_uncertain_playlist_creation_is_not_repeated_after_restart(
        config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch duplicates after lost create replies."""
        import pytest
        from music_helpers import import_environment

        from watch.errors import PreloadError

        class LostReply(MusicEdge):
                async def http(self, request: web.Request) -> web.Response:
                        response = await super().http(request)
                        if request.path == "/Playlists":
                                return web.Response(status=503)
                        return response

        edge = LostReply(ready={"Halo", "Enjoy the Silence"})
        async with import_environment(config, monkeypatch, edge) as (
                engine,
                saved,
                _allowed,
        ):
                job = music_job()
                await engine.prepare(job)
                with pytest.raises(PreloadError):
                        await engine.refresh(job)
                with pytest.raises(PreloadError, match="uncertain"):
                        await engine.refresh(copy.deepcopy(saved))
                assert edge.creations == 1


async def test_unresolved_music_does_not_turn_into_an_all_added_result(
        config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch claiming completion when the MusicBrainz match is missing."""
        from music_helpers import import_environment

        class MissingAlbum(MusicEdge):
                async def arr(self, request: web.Request) -> web.Response:
                        if request.path.endswith("/album/lookup"):
                                return web.json_response([])
                        return await super().arr(request)

        edge = MissingAlbum()
        async with import_environment(config, monkeypatch, edge) as (
                engine,
                _saved,
                _allowed,
        ):
                job = music_job()
                await engine.prepare(job)
                await engine.refresh(job)
                assert job["stage"] == "attention"
                assert job["ready"] == 0
                assert edge.searches == []


async def test_create_id_is_saved_atomically_with_acknowledgement(
        config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch losing a created playlist ID after acknowledgement."""
        import asyncio

        import pytest
        from music_helpers import import_environment

        edge = MusicEdge(ready={"Halo", "Enjoy the Silence"})
        async with import_environment(config, monkeypatch, edge) as (
                engine,
                saved,
                _allowed,
        ):
                job = music_job()
                await engine.prepare(job)
                original_save = engine.save
                interrupted = False

                async def interrupted_save(state: dict[str, Any]) -> None:
                        nonlocal interrupted
                        await original_save(state)
                        if (
                                edge.creations
                                and not state.get("mutation")
                                and not interrupted
                        ):
                                interrupted = True
                                raise asyncio.CancelledError

                engine.save = interrupted_save
                with pytest.raises(asyncio.CancelledError):
                        await engine.refresh(job)
                engine.save = original_save
                await engine.refresh(copy.deepcopy(saved))
                assert edge.creations == 1
