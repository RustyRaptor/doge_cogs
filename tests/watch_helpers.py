"""A fake media-preload API on a real socket, and a fake command context.

The fake server speaks the media-preload OpenAPI spec (camelCase JSON,
bearer token), so the watch cog's tests run the real generated client, httpx,
httpcore and anyio. That path is what broke on 2026-11-09, when anyio and
typing_extensions disagreed inside the bot; test-container runs these tests
in the bot's image with requirements installed the way Red installs them.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import discord
from aiohttp import web
from aiohttp.test_utils import TestServer

TOKEN = "test-bot-token"  # noqa: S105 (a fake server's token)


def media_item(  # noqa: PLR0913 (mirrors the API's fields)
        item_id: str,
        name: str,
        *,
        kind: str = "movie",
        cacheable: bool = True,
        year: int | None = 2020,
        seasons: int | None = None,
) -> dict[str, Any]:
        """Return a MediaItem as the API sends it."""
        item: dict[str, Any] = {
                "id": item_id,
                "name": name,
                "type": kind,
                "cacheable": cacheable,
                "library": "Movies" if kind == "movie" else "TV",
        }
        if year is not None:
                item["year"] = year
        if seasons is not None:
                item["seasonCount"] = seasons
        return item


def preload_request(
        request_id: int,
        title: str,
        *,
        status: str = "done",
        requested_by: str = "discord:someone",
        files: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
        """Return a PreloadRequest as the API sends it."""
        return {
                "id": request_id,
                "itemId": f"item-{request_id}",
                "title": title,
                "requestedBy": requested_by,
                "status": status,
                "createdAt": "2026-11-10T12:00:00Z",
                "files": files
                if files is not None
                else [
                        {
                                "id": request_id * 10,
                                "path": f"/media/Videos/Movies/{title}.mkv",
                                "title": title,
                                "status": "cached",
                                "sizeBytes": 2_000_000_000,
                                "fetchedBytes": 2_000_000_000,
                        }
                ],
        }


@dataclass
class FakePreload:
        """The parts of media-preload the bot uses, with what it was sent."""

        search_results: list[dict[str, Any]] = field(default_factory=list)
        requests: dict[int, dict[str, Any]] = field(default_factory=dict)
        # Calls as (method, path, query, json body)
        calls: list[tuple[str, str, dict[str, str], Any]] = field(
                default_factory=list
        )
        next_id: int = 100

        def app(self) -> web.Application:
                """Return the aiohttp app serving the API."""
                app = web.Application(middlewares=[self._auth])
                app.router.add_get("/v1/search", self._search)
                app.router.add_get("/v1/requests", self._list)
                app.router.add_post("/v1/requests", self._create)
                app.router.add_get("/v1/requests/{id}", self._get)
                app.router.add_delete("/v1/requests/{id}", self._cancel)
                return app

        @web.middleware
        async def _auth(self, request: web.Request, handler: Any) -> Any:  # noqa: ANN401
                body = await request.json() if request.can_read_body else None
                self.calls.append(
                        (
                                request.method,
                                request.path,
                                dict(request.query),
                                body,
                        )
                )
                if request.headers.get("Authorization") != f"Bearer {TOKEN}":
                        return web.json_response(
                                {"error": "missing or wrong token"}, status=401
                        )
                return await handler(request)

        async def _search(self, request: web.Request) -> web.Response:
                kind = request.query.get("type")
                found = [
                        i
                        for i in self.search_results
                        if kind is None or i["type"] == kind
                ]
                return web.json_response(found)

        async def _list(self, request: web.Request) -> web.Response:
                limit = int(request.query.get("limit", "20"))
                latest = sorted(self.requests.values(), key=lambda r: -r["id"])
                return web.json_response(latest[:limit])

        async def _create(self, request: web.Request) -> web.Response:
                body = await request.json()
                match = [
                        i
                        for i in self.search_results
                        if i["id"] == body["itemId"]
                ]
                if not match:
                        return web.json_response(
                                {"error": "no such item"}, status=404
                        )
                created = preload_request(
                        self.next_id,
                        match[0]["name"],
                        requested_by=body["requestedBy"],
                )
                self.requests[self.next_id] = created
                self.next_id += 1
                return web.json_response(created, status=201)

        def _find(self, request: web.Request) -> dict[str, Any] | None:
                return self.requests.get(int(request.match_info["id"]))

        async def _get(self, request: web.Request) -> web.Response:
                found = self._find(request)
                if found is None:
                        return web.json_response(
                                {"error": "no such request"}, status=404
                        )
                return web.json_response(found)

        async def _cancel(self, request: web.Request) -> web.Response:
                found = self._find(request)
                if found is None:
                        return web.json_response(
                                {"error": "no such request"}, status=404
                        )
                found["status"] = "canceled"
                return web.json_response(found)

        def calls_to(self, method: str, path: str) -> list[Any]:
                """Return the bodies (queries for GETs) sent to an endpoint."""
                return [
                        body if body is not None else query
                        for m, p, query, body in self.calls
                        if m == method and p == path
                ]


@asynccontextmanager
async def serve(fake: FakePreload) -> AsyncGenerator[str]:
        """Run ``fake`` on a free local port; yield its base URL."""
        server = TestServer(fake.app(), host="127.0.0.1")
        await server.start_server()
        try:
                yield str(server.make_url("")).rstrip("/")
        finally:
                await server.close()


@dataclass
class FakeMessage:
        """A sent message that records edits."""

        content: str | None
        embed: discord.Embed | None
        edits: list[dict[str, Any]] = field(default_factory=list)

        async def edit(self, **changes: Any) -> None:  # noqa: ANN401
                """Record an edit."""
                self.edits.append(changes)


@dataclass
class FakeAuthor:
        """The person running the command."""

        id: int = 1
        name: str = "doge"


class _Typing:
        async def __aenter__(self) -> None:
                return None

        async def __aexit__(self, *_exc: object) -> None:
                return None


@dataclass
class FakeContext:
        """Just enough of commands.Context for the watch commands."""

        author: FakeAuthor = field(default_factory=FakeAuthor)
        clean_prefix: str = "d!"
        sent: list[FakeMessage] = field(default_factory=list)

        async def send(
                self,
                content: str | None = None,
                *,
                embed: discord.Embed | None = None,
                **_kwargs: Any,  # noqa: ANN401 (views, files)
        ) -> FakeMessage:
                """Record what the bot sends."""
                message = FakeMessage(content, embed)
                self.sent.append(message)
                return message

        def typing(self) -> _Typing:
                """Pretend to type."""
                return _Typing()

        @property
        def texts(self) -> list[str]:
                """Message contents, or embed titles for embeds."""
                return [
                        m.content
                        if m.content is not None
                        else str(m.embed.title if m.embed else "")
                        for m in self.sent
                ]
