"""Stateful Sonarr HTTP edge for season ownership and file deletion tests."""

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web


def series_resource(series_id: int = 7, tvdb: int = 123) -> dict[str, Any]:
        """Build one series with three regular seasons and specials."""
        return {
                "id": series_id,
                "tvdbId": tvdb,
                "title": "Dune",
                "year": 2021,
                "titleSlug": "dune",
                "qualityProfileId": 1,
                "path": "/arr/library/TV/Dune",
                "monitored": True,
                "monitorNewItems": "all",
                "seasonFolder": True,
                "seasons": [
                        {"seasonNumber": n, "monitored": True} for n in range(4)
                ],
        }


def episode_resource(season: int, *, has_file: bool = True) -> dict[str, Any]:
        """Build an aired episode, with an associated file if downloaded."""
        return {
                "id": 700 + season,
                "seriesId": 7,
                "seasonNumber": season,
                "episodeNumber": 1,
                "airDateUtc": "2020-01-01T00:00:00Z",
                "hasFile": has_file,
                "episodeFileId": 100 + season if has_file else 0,
        }


@dataclass
class FakeSonarr:
        """Keep API resources mutable between requests."""

        series: list[dict[str, Any]] = field(default_factory=list)
        episodes: list[dict[str, Any]] = field(default_factory=list)
        files: list[dict[str, Any]] = field(default_factory=list)
        queue: list[dict[str, Any]] = field(default_factory=list)
        calls: list[Any] = field(default_factory=list)
        fail: str = ""
        add_monitor: str = ""
        after_write: Callable[[str, str], Awaitable[None] | None] | None = None

        async def handle(self, request: web.Request) -> web.Response:
                """Route authenticated requests to this fake server."""
                body = await request.json() if request.can_read_body else None
                path = request.path.removeprefix("/api/v3/")
                self.calls.append(
                        (request.method, path, dict(request.query), body)
                )
                if request.headers.get("X-Api-Key") != "test-key":
                        return web.json_response({}, status=401)
                if self.fail == request.method + " " + path:
                        return web.json_response(
                                {"error": "private-details"}, status=503
                        )
                if request.method == "GET":
                        return self.read(path, request.query)
                response = self.write(request.method, path, body)
                if self.after_write:
                        result = self.after_write(request.method, path)
                        if inspect.isawaitable(result):
                                await result
                return response

        def read(self, path: str, params: Any) -> web.Response:  # noqa: ANN401
                """Return real-shaped Sonarr resources."""
                if path == "series/lookup":
                        return web.json_response([series_resource()])
                if path == "series":
                        return web.json_response(self.series)
                if path.startswith("series/"):
                        return web.json_response(
                                next(
                                        s
                                        for s in self.series
                                        if s["id"] == int(path.split("/")[1])
                                )
                        )
                if path in {"episode", "episodefile", "queue/details"}:
                        values = {
                                "episode": self.episodes,
                                "episodefile": self.files,
                                "queue/details": self.queue,
                        }[path]
                        return web.json_response(
                                [
                                        r
                                        for r in values
                                        if r.get("seriesId")
                                        == int(params.get("seriesId", 7))
                                ]
                        )
                values = {
                        "qualityprofile": [
                                {"id": 1, "name": "Doge - 1080p default"},
                                {"id": 3, "name": "Doge - 4K optional"},
                        ],
                        "rootfolder": [
                                {"path": "/arr/library/TV", "accessible": True}
                        ],
                }
                return web.json_response(values.get(path, []))

        def write(self, method: str, path: str, body: Any) -> web.Response:  # noqa: ANN401
                """Apply series changes and scoped file/queue removals."""
                if method == "POST" and path == "series":
                        created = {**body, "id": 7}
                        self.series.append(created)
                        self.add_monitor = body["addOptions"]["monitor"]
                        self.episodes = [
                                episode_resource(n, has_file=False)
                                for n in [1, 2, 3]
                        ]
                        return web.json_response(created, status=201)
                if method == "PUT" and path.startswith("series/"):
                        self.series[:] = [
                                body if s["id"] == body["id"] else s
                                for s in self.series
                        ]
                        return web.json_response(body)
                if method == "DELETE" and path == "episodefile/bulk":
                        ids = set(body["episodeFileIds"])
                        self.files[:] = [
                                f for f in self.files if f["id"] not in ids
                        ]
                        for episode in self.episodes:
                                if episode["episodeFileId"] in ids:
                                        episode.update(
                                                hasFile=False, episodeFileId=0
                                        )
                        return web.Response(status=204)
                if method == "DELETE" and path.startswith("queue/"):
                        row = next(
                                q
                                for q in self.queue
                                if q["id"] == int(path.split("/")[1])
                        )
                        self.queue[:] = [
                                q
                                for q in self.queue
                                if q["downloadId"] != row["downloadId"]
                        ]
                        return web.Response(status=204)
                if method == "POST" and path == "command":
                        return web.json_response({"id": 88, **body}, status=201)
                return web.json_response(
                        {"error": "unexpected write"}, status=400
                )

        def finish_initial_scan(self) -> None:
                """Model Sonarr's documented post-add monitor processing."""
                if self.add_monitor == "none":
                        for series in self.series:
                                for season in series["seasons"]:
                                        season["monitored"] = False
