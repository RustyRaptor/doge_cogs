"""Avatar downloads: limits, formats, and whole bodies."""

from collections.abc import AsyncIterator

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import BaseTestServer, TestServer

from doge_alignment.core import fetch
from doge_alignment.core.view import Person

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 300_000


async def _png(_request: web.Request) -> web.StreamResponse:
        # Sent in pieces, so a single read() would come back short
        response = web.StreamResponse()
        await response.prepare(_request)
        for i in range(0, len(PNG), 1000):
                await response.write(PNG[i : i + 1000])
        return response


async def _svg(_request: web.Request) -> web.Response:
        return web.Response(body=b"<svg/>", content_type="image/svg+xml")


async def _missing(_request: web.Request) -> web.Response:
        return web.Response(status=404)


@pytest.fixture
async def server() -> AsyncIterator[BaseTestServer]:
        """Serve test images over plain HTTP."""
        app = web.Application()
        app.router.add_get("/a.png", _png)
        app.router.add_get("/a.svg", _svg)
        app.router.add_get("/missing.png", _missing)
        async with TestServer(app) as test_server:
                yield test_server


async def test_fetch_reads_whole_body_and_checks_format(
        server: BaseTestServer, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Chunked bodies arrive whole; non-images and errors are None."""
        monkeypatch.setattr(fetch, "_allowed", lambda _url: True)
        async with aiohttp.ClientSession() as session:
                images = await fetch.fetch_images(
                        session,
                        [
                                str(server.make_url("/a.png")),
                                str(server.make_url("/a.svg")),
                                str(server.make_url("/missing.png")),
                        ],
                )
        assert list(images.values()) == [PNG]


async def test_fetch_enforces_size_limit(
        server: BaseTestServer, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Bodies over the limit are dropped."""
        monkeypatch.setattr(fetch, "_allowed", lambda _url: True)
        monkeypatch.setattr(fetch, "MAX_AVATAR_BYTES", 1000)
        async with aiohttp.ClientSession() as session:
                url = str(server.make_url("/a.png"))
                assert await fetch.fetch_image(session, url) is None


async def test_only_https() -> None:
        """Plain HTTP and other schemes are refused before connecting."""
        async with aiohttp.ClientSession() as session:
                assert (
                        await fetch.fetch_image(session, "http://x/a.png")
                        is None
                )
                assert await fetch.fetch_image(session, "file:///etc") is None


def test_urls_and_by_user() -> None:
        """URLs are de-duplicated and mapped back to users."""
        people = (
                Person("1", "a", "https://x/1.png"),
                Person("2", "b", "https://x/1.png"),
                Person("3", "c", None),
        )
        assert fetch.avatar_urls(people) == {"https://x/1.png"}
        assert fetch.by_user(people, {"https://x/1.png": b"img"}) == {
                "1": b"img",
                "2": b"img",
        }
