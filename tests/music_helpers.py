"""HTTP edges for the playlist importer, with persistent server-side state."""

import copy
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import httpx
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from redbot.core import Config


@dataclass
class MusicEdge:
        """Serve Lidarr/Jellyfin HTTP with persistent state."""

        albums: list[dict[str, Any]] = field(default_factory=list)
        artists: list[dict[str, Any]] = field(default_factory=list)
        playlist: dict[str, Any] = field(default_factory=dict)
        searches: list[list[int]] = field(default_factory=list)
        ready: set[str] = field(default_factory=set)
        creations: int = 0
        after_lookup: Any = None

        async def http(self, request: web.Request) -> web.Response:
                """Use the actual API routes and JSON casing."""
                if request.path.startswith("/api/v1/"):
                        if request.headers.get("X-Api-Key") != "test-key":
                                return web.Response(status=401)
                        return await self.arr(request)
                if (
                        request.headers.get("Authorization")
                        != 'MediaBrowser Token="test-key"'
                ):
                        return web.Response(status=401)
                if request.path == "/Items":
                        title = request.query.get("searchTerm", "")
                        items = (
                                [
                                        {
                                                "Id": title,
                                                "Name": title,
                                                "Artists": ["Depeche Mode"],
                                                "RunTimeTicks": 2700000000,
                                        }
                                ]
                                if title in self.ready
                                else []
                        )
                        return web.json_response(
                                {"Items": items, "TotalRecordCount": len(items)}
                        )
                if request.path == "/Playlists":
                        self.creations += 1
                        self.playlist.update(await request.json())
                        return web.json_response({"Id": "playlist-1"})
                if request.path == "/Playlists/playlist-1":
                        self.playlist.update(await request.json())
                        return web.Response(status=204)
                return web.Response(status=404)

        async def arr(self, request: web.Request) -> web.Response:  # noqa: C901
                """Persist albums and targeted searches."""
                path = request.path.removeprefix("/api/v1/")
                body = await request.json() if request.can_read_body else {}
                if path == "album/lookup":
                        if self.after_lookup:
                                await self.after_lookup()
                        return web.json_response(
                                [
                                        {
                                                "title": "Violator",
                                                "foreignAlbumId": "mb-album",
                                                "artist": {
                                                        "artistName": "Depeche Mode",  # noqa: E501
                                                        "foreignArtistId": "mb-artist",  # noqa: E501
                                                },
                                                "releases": [],
                                                "images": [],
                                        }
                                ]
                        )
                if path == "rootfolder":
                        return web.json_response(
                                [
                                        {
                                                "path": "/arr/library/Music",
                                                "defaultQualityProfileId": 5,
                                                "defaultMetadataProfileId": 1,
                                        }
                                ]
                        )
                if path == "artist":
                        return web.json_response(self.artists)
                if path == "artist/8":
                        if request.method == "PUT":
                                self.artists[0] = body
                        return web.json_response(self.artists[0])
                if path == "album" and request.method == "GET":
                        return web.json_response(self.albums)
                if path == "album" and request.method == "POST":
                        self.artists.append(
                                {**body["artist"], "id": 8, "monitored": False}
                        )
                        album = {**body, "id": 7, "artistId": 8}
                        self.albums.append(album)
                        return web.json_response(album, status=201)
                if path == "album/7":
                        if request.method == "PUT":
                                self.albums[0] = body
                        return web.json_response(self.albums[0])
                if path == "command" and request.method == "POST":
                        if body.get("name") != "AlbumSearch":
                                return web.Response(status=400)
                        self.searches.append(body["albumIds"])
                        return web.json_response(
                                {"id": 123, "status": "queued"}, status=201
                        )
                return web.Response(status=404)


def public_page(request: httpx.Request) -> httpx.Response:
        """Both distinct test songs belong to the same album."""
        if request.url.path.startswith("/track/"):
                return httpx.Response(
                        200,
                        text=(
                                '<meta name="music:album" '
                                'content="https://open.spotify.com/album/45YmvYK4hB4CgQgTMuNRm8"/>'
                        ),
                )
        if request.url.path == "/embed/album/45YmvYK4hB4CgQgTMuNRm8":
                data = {
                        "props": {
                                "pageProps": {
                                        "state": {
                                                "data": {
                                                        "entity": {
                                                                "name": "Violator",  # noqa: E501
                                                                "subtitle": "Depeche Mode",  # noqa: E501
                                                        }
                                                }
                                        }
                                }
                        }
                }
                return httpx.Response(
                        200,
                        text='<script id="__NEXT_DATA__">'
                        + json.dumps(data)
                        + "</script>",
                )
        return httpx.Response(404)


def music_job() -> dict[str, Any]:
        """Return ordered songs with duplicate positions."""
        return {
                "stage": "preparing",
                "username": "doge",
                "name": "My mix",
                "user_id": "user-1",
                "partial": False,
                "tracks": [
                        {
                                "id": "6kwEU2hLQpMToj1PHsBrOE",
                                "title": "Halo",
                                "artist": "Depeche Mode",
                                "duration": 270186,
                        },
                        {
                                "id": "3CA4RlbsFJEtHKdsLAMgb4",
                                "title": "Enjoy the Silence",
                                "artist": "Depeche Mode",
                                "duration": 270186,
                        },
                        {
                                "id": "6kwEU2hLQpMToj1PHsBrOE",
                                "title": "Halo",
                                "artist": "Depeche Mode",
                                "duration": 270186,
                        },
                ],
        }


if TYPE_CHECKING:
        from watch.music import MusicImporter


@asynccontextmanager
async def import_environment(
        config: Config, monkeypatch: pytest.MonkeyPatch, edge: MusicEdge
) -> AsyncGenerator[tuple["MusicImporter", dict[str, Any], list[bool]]]:
        """Exercise production code above real HTTP."""
        from watch.jellyfin import Jellyfin
        from watch.music import MusicImporter
        from watch.spotify import SpotifyPublic
        from watch.watch import Watch

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
                permitted = [True]

                async def save(job: dict[str, Any]) -> None:
                        saved.clear()
                        saved.update(copy.deepcopy(job))

                async def authorize() -> None:
                        if not permitted[0]:
                                message = "Download permission was revoked."
                                raise PermissionError(message)

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
                                authorize,
                        )
                        yield engine, saved, permitted
