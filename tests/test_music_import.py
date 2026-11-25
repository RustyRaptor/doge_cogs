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


async def test_one_failed_lookup_does_not_wedge_later_tracks(
        config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch one poisonous album lookup stopping an entire import."""
        from music_helpers import import_environment

        class Flaky(MusicEdge):
                failures = 1

                async def arr(self, request: web.Request) -> web.Response:
                        if (
                                request.path.endswith("/album/lookup")
                                and self.failures
                        ):
                                self.failures -= 1
                                return web.Response(status=503)
                        return await super().arr(request)

        edge = Flaky()
        async with import_environment(config, monkeypatch, edge) as (
                engine,
                saved,
                _allowed,
        ):
                job = music_job()
                await engine.prepare(job)
                assert job["tracks"][0].get("attempts") == 1
                assert "review" not in job["tracks"][0]
                assert job["tracks"][1]["album_id"] == 7
                assert job["tracks"][2]["album_id"] == 7
                assert len(edge.albums) == 1
                assert saved["tracks"][1]["album_id"] == 7
                await engine.prepare(job)
                assert job["tracks"][0]["album_id"] == 7
                assert "attempts" not in job["tracks"][0]


async def test_repeated_lookup_failures_become_review_tracks(
        config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch endless silent retries that never reach the requester."""
        from music_helpers import import_environment

        edge = MusicEdge(lookup_available=False)
        async with import_environment(config, monkeypatch, edge) as (
                engine,
                saved,
                _allowed,
        ):
                job = music_job()
                for _ in range(3):
                        await engine.prepare(job)
                assert job["stage"] == "waiting"
                for track in job["tracks"]:
                        assert track["resolved"] is True
                        assert track["review"] == "Catalogue lookup failed"
                assert saved["tracks"][0]["review"] == (
                        "Catalogue lookup failed"
                )


async def test_an_artist_missing_from_musicbrainz_is_named(
        config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch blaming album ambiguity for an artist the catalogue lacks."""
        from music_helpers import import_environment

        class UnknownArtist(MusicEdge):
                async def arr(self, request: web.Request) -> web.Response:
                        if request.path.endswith("/album/lookup"):
                                return web.json_response([])
                        return await super().arr(request)

        edge = UnknownArtist(artist_missing=True)
        async with import_environment(config, monkeypatch, edge) as (
                engine,
                _saved,
                _allowed,
        ):
                job = music_job()
                await engine.prepare(job)
                assert {t["review"] for t in job["tracks"]} == {
                        "Artist not in MusicBrainz"
                }
                assert edge.albums == []


async def test_a_known_artist_without_its_album_stays_generic(
        config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch calling every missing album an unknown artist."""
        from music_helpers import import_environment

        class NoAlbum(MusicEdge):
                async def arr(self, request: web.Request) -> web.Response:
                        if request.path.endswith("/album/lookup"):
                                return web.json_response([])
                        return await super().arr(request)

        edge = NoAlbum()
        async with import_environment(config, monkeypatch, edge) as (
                engine,
                _saved,
                _allowed,
        ):
                job = music_job()
                await engine.prepare(job)
                assert {t["review"] for t in job["tracks"]} == {
                        "No unique artist/album match"
                }


async def test_another_artists_album_is_never_monitored(
        config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch adopting a same-titled tribute album for the artist."""
        from music_helpers import import_environment

        class Tribute(MusicEdge):
                async def arr(self, request: web.Request) -> web.Response:
                        if request.path.endswith("/album/lookup"):
                                return web.json_response(
                                        [
                                                {
                                                        "title": "Violator",
                                                        "foreignAlbumId": (
                                                                "tribute"
                                                        ),
                                                        "artist": {
                                                                "artistName": "Tribute",  # noqa: E501
                                                                "foreignArtistId": "other",  # noqa: E501
                                                        },
                                                        "releases": [],
                                                        "images": [],
                                                }
                                        ]
                                )
                        return await super().arr(request)

        edge = Tribute()
        async with import_environment(config, monkeypatch, edge) as (
                engine,
                _saved,
                _allowed,
        ):
                job = music_job()
                await engine.prepare(job)
                assert {t["review"] for t in job["tracks"]} == {
                        "No unique artist/album match"
                }
                assert edge.albums == []
                assert edge.searches == []


async def test_a_skipped_song_settles_the_import(
        config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch skipped songs counting as review forever."""
        from music_helpers import import_environment

        edge = MusicEdge()
        async with import_environment(config, monkeypatch, edge) as (
                engine,
                _saved,
                _allowed,
        ):
                job = music_job()
                job["tracks"] = job["tracks"][:1]
                row = job["tracks"][0]
                row.update(resolved=True, review="Artist not in MusicBrainz")
                await engine.skip(job, row)
                assert row["skipped"] is True
                assert "review" not in row
                await engine.refresh(job)
                assert job["stage"] == "attention"
                assert job["ready"] == 0


async def test_a_youtube_file_is_attached_by_its_jellyfin_item(
        config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch searching by Spotify duration for a longer video file."""
        from music_helpers import import_environment

        path = "/mnt/MediaDisk/Music/YouTube Imports/Depeche Mode/Halo.opus"
        edge = MusicEdge(path_items={path: "yt-1"})
        async with import_environment(config, monkeypatch, edge) as (
                engine,
                saved,
                _allowed,
        ):
                job = music_job()
                job["tracks"] = job["tracks"][:1]
                row = job["tracks"][0]
                row.update(
                        resolved=False,
                        youtube_path=path,
                        review="Artist not in MusicBrainz",
                )
                await engine.prepare(job)
                assert row["item_id"] == "yt-1"
                assert row["resolved"] is True
                assert "review" not in row
                assert saved["tracks"][0]["item_id"] == "yt-1"
                await engine.refresh(job)
                assert job["ready"] == 1
                assert job["stage"] == "complete"
                assert edge.playlist["Ids"] == ["yt-1"]


async def test_a_ready_track_loses_its_review_label(
        config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch stale review titles after the song arrives in Jellyfin."""
        from music_helpers import import_environment

        edge = MusicEdge(ready={"Halo", "Enjoy the Silence"})
        async with import_environment(config, monkeypatch, edge) as (
                engine,
                _saved,
                _allowed,
        ):
                job = music_job()
                job["tracks"][0].update(
                        resolved=True, review="No unique artist/album match"
                )
                await engine.refresh(job)
                assert "review" not in job["tracks"][0]
                assert job["stage"] == "complete"
                assert job["ready"] == 3


async def test_manual_album_choice_adds_and_searches_the_album(
        config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch a reviewed song with no way to choose its album."""
        from music_helpers import import_environment

        edge = MusicEdge()
        async with import_environment(config, monkeypatch, edge) as (
                engine,
                saved,
                _allowed,
        ):
                job = music_job()
                job["tracks"] = job["tracks"][:1]
                row = job["tracks"][0]
                row.update(resolved=True, review="No unique artist/album match")
                album_id = await engine.assign_album(
                        job,
                        row,
                        {
                                "title": "Violator",
                                "foreignAlbumId": "mb-album",
                                "artist": {
                                        "artistName": "Depeche Mode",
                                        "foreignArtistId": "mb-artist",
                                },
                                "releases": [],
                                "images": [],
                        },
                )
                assert album_id == 7
                assert row["album_id"] == 7
                assert "review" not in row
                assert edge.albums[0]["monitored"] is True
                assert edge.artists[0]["monitored"] is True
                assert edge.searches == [[7]]
                assert saved["tracks"][0]["album_id"] == 7


async def test_manual_album_choice_monitors_an_existing_album(
        config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch a second copy of an album Lidarr already knows."""
        from music_helpers import import_environment

        edge = MusicEdge(
                artists=[
                        {
                                "id": 8,
                                "artistName": "Depeche Mode",
                                "foreignArtistId": "mb-artist",
                                "monitored": True,
                                "monitorNewItems": "none",
                        }
                ],
                albums=[
                        {
                                "id": 7,
                                "artistId": 8,
                                "title": "Violator",
                                "foreignAlbumId": "mb-album",
                                "monitored": False,
                        }
                ],
        )
        async with import_environment(config, monkeypatch, edge) as (
                engine,
                _saved,
                _allowed,
        ):
                job = music_job()
                job["tracks"] = job["tracks"][:1]
                row = job["tracks"][0]
                row.update(resolved=True, review="No unique artist/album match")
                album_id = await engine.assign_album(
                        job,
                        row,
                        {
                                "title": "Violator",
                                "foreignAlbumId": "mb-album",
                                "artist": {
                                        "artistName": "Depeche Mode",
                                        "foreignArtistId": "mb-artist",
                                },
                        },
                )
                assert album_id == 7
                assert len(edge.albums) == 1
                assert edge.albums[0]["monitored"] is True
                assert edge.searches == [[7]]


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
