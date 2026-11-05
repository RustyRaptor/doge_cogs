"""Download avatars over HTTPS with aiohttp.

Generic: it knows URLs, not Discord. Front ends pass their own session (Red's
or a fresh one), and may keep a cache keyed by URL, since avatar URLs change
when the avatar does.
"""

import asyncio
from collections.abc import Iterable, Mapping

import aiohttp

from .images import sniff
from .view import Person

MAX_AVATAR_BYTES = 4 * 1024 * 1024
TIMEOUT = aiohttp.ClientTimeout(total=10)


def avatar_urls(people: Iterable[Person]) -> frozenset[str]:
        """Return the avatar URLs of ``people``, without duplicates."""
        return frozenset(p.avatar_url for p in people if p.avatar_url)


def by_user(
        people: Iterable[Person], images: Mapping[str, bytes]
) -> dict[str, bytes]:
        """Return avatar bytes keyed by user id, from bytes keyed by URL."""
        return {
                p.user_id: images[p.avatar_url]
                for p in people
                if p.avatar_url and p.avatar_url in images
        }


def _allowed(url: str) -> bool:
        return url.startswith("https://")


async def _read_limited(response: aiohttp.ClientResponse) -> bytes | None:
        chunks: list[bytes] = []
        size = 0
        async for chunk in response.content.iter_chunked(64 * 1024):
                size += len(chunk)
                if size > MAX_AVATAR_BYTES:
                        return None
                chunks.append(chunk)
        return b"".join(chunks)


async def fetch_image(session: aiohttp.ClientSession, url: str) -> bytes | None:
        """Return the image at ``url``, or None if it can't be had.

        Only HTTPS, at most :data:`MAX_AVATAR_BYTES`, and only formats
        :func:`.images.sniff` accepts.
        """
        if not _allowed(url):
                return None
        try:
                async with session.get(url, timeout=TIMEOUT) as response:
                        if response.status != 200:  # noqa: PLR2004
                                return None
                        data = await _read_limited(response)
        except (aiohttp.ClientError, TimeoutError):
                return None
        if data is None or sniff(data) is None:
                return None
        return data


async def fetch_images(
        session: aiohttp.ClientSession,
        urls: Iterable[str],
        concurrency: int = 8,
) -> dict[str, bytes]:
        """Return the images at ``urls`` that could be fetched, by URL."""
        limit = asyncio.Semaphore(concurrency)

        async def one(url: str) -> tuple[str, bytes | None]:
                async with limit:
                        return url, await fetch_image(session, url)

        results = await asyncio.gather(*(one(u) for u in set(urls)))
        return {url: data for url, data in results if data is not None}
