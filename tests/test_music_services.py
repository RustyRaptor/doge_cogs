"""Jellyfin playlist contracts against a real HTTP edge."""

from typing import Any, cast

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from redbot.core import Config


async def test_playlist_is_public_owned_and_updated_in_source_order() -> None:
        """Catch private, unowned or unordered playlists."""
        from watch.jellyfin import Jellyfin

        created: dict[str, Any] = {}

        async def handler(request: web.Request) -> web.Response:
                if (
                        request.headers.get("Authorization")
                        != 'MediaBrowser Token="test-key"'
                ):
                        return web.Response(status=401)
                if request.path == "/Users":
                        return web.json_response(
                                [
                                        {
                                                "Id": "user-1",
                                                "Name": "doge",
                                                "Policy": {"IsDisabled": False},
                                        }
                                ]
                        )
                if request.path == "/Playlists":
                        created.update(await request.json())
                        return web.json_response({"Id": "playlist-1"})
                if request.path == "/Playlists/playlist-1":
                        created.update(await request.json())
                        return web.Response(status=204)
                return web.Response(status=404)

        app = web.Application()
        app.router.add_route("*", "/{path:.*}", handler)
        async with TestServer(app) as server:

                async def tokens() -> dict[str, str]:
                        return {
                                "url": str(server.make_url("/")),
                                "api_key": "test-key",
                                "user_id": "service-1",
                        }

                api = Jellyfin(tokens)
                uid = await api.user("doge")
                assert uid == "user-1"
                pid = await api.create_playlist(
                        "doge - My mix", uid, ["song-b", "song-a", "song-b"]
                )
                assert pid == "playlist-1"
                assert created == {
                        "Name": "doge - My mix",
                        "UserId": "user-1",
                        "Ids": ["song-b", "song-a", "song-b"],
                        "MediaType": "Audio",
                        "IsPublic": True,
                        "Users": [{"UserId": "service-1", "CanEdit": True}],
                }
                await api.update_playlist(pid, ["song-a", "song-b"])
                assert created["Ids"] == ["song-a", "song-b"]


async def test_private_service_errors_do_not_expose_credentials() -> None:
        """Catch forwarding upstream bodies or token-bearing URLs to Discord."""
        from watch.errors import PreloadError
        from watch.jellyfin import Jellyfin

        async def handler(_request: web.Request) -> web.Response:
                return web.json_response(
                        {"secret": "private-credential"}, status=503
                )

        app = web.Application()
        app.router.add_get("/Users", handler)
        async with TestServer(app) as server:

                async def tokens() -> dict[str, str]:
                        return {
                                "url": str(server.make_url("/")),
                                "api_key": "private-credential",
                        }

                with pytest.raises(PreloadError) as error:
                        await Jellyfin(tokens).user("doge")
                assert "503" in str(error.value)
                assert "private-credential" not in str(error.value)


async def test_lidarr_uses_v1_and_never_discloses_an_api_error_body(
        config: Config,
        monkeypatch: pytest.MonkeyPatch,
) -> None:
        """Catch accidentally using the movie API version for music."""
        from watch.errors import PreloadError
        from watch.watch import Watch

        async def handler(request: web.Request) -> web.Response:
                if request.path != "/api/v1/album":
                        return web.Response(status=404)
                if request.headers.get("X-Api-Key") != "secret-key":
                        return web.Response(status=401)
                return web.json_response([{"id": 7, "title": "Violator"}])

        app = web.Application()
        app.router.add_route("*", "/{path:.*}", handler)
        async with TestServer(app) as server:

                class Bot:
                        async def get_shared_api_tokens(
                                self, service: str
                        ) -> dict[str, str]:
                                return (
                                        {
                                                "url": str(
                                                        server.make_url("/")
                                                ),
                                                "api_key": "secret-key",
                                        }
                                        if service == "lidarr"
                                        else {}
                                )

                monkeypatch.setattr(
                        Config, "get_conf", lambda *_a, **_k: config
                )
                cog = Watch(cast("Any", Bot()))
                assert await cog._arr_call("music", "album") == [
                        {"id": 7, "title": "Violator"}
                ]
                with pytest.raises(PreloadError) as error:
                        await cog._arr_call("music", "private")
                assert "404" in str(error.value)
                assert "secret-key" not in str(error.value)


async def test_audio_search_pages_within_the_linked_user_scope() -> None:
        """Catch incomplete or unscoped library searches."""
        from watch.jellyfin import Jellyfin

        async def handler(request: web.Request) -> web.Response:
                if (
                        request.query.get("userId") != "user-1"
                        or request.query.get("includeItemTypes") != "Audio"
                ):
                        return web.Response(status=403)
                if request.query.get("startIndex") == "0":
                        items = [
                                {
                                        "Id": "wrong",
                                        "Name": "Halo",
                                        "Artists": ["Other"],
                                        "RunTimeTicks": 2700000000,
                                }
                        ] * 100
                else:
                        items = [
                                {
                                        "Id": "right",
                                        "Name": "Halo",
                                        "Artists": ["Depeche Mode"],
                                        "RunTimeTicks": 2700000000,
                                }
                        ]
                return web.json_response(
                        {"Items": items, "TotalRecordCount": 101}
                )

        app = web.Application()
        app.router.add_get("/Items", handler)
        async with TestServer(app) as server:

                async def tokens() -> dict[str, str]:
                        return {
                                "url": str(server.make_url("/")),
                                "api_key": "test-key",
                        }

                result = await Jellyfin(tokens).audio(
                        {
                                "title": "Halo",
                                "artist": "Depeche Mode",
                                "duration": 270186,
                        },
                        "user-1",
                )
                assert result == "right"
