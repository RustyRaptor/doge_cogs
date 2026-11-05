"""Pick a uniform random sample of someone's messages from their whole history.

Discord's guild message search returns an author's messages newest first, 25
per page, reachable by ``offset`` up to 9975, and says how many there are in
total. Rather than download everything and then choose, this picks random
pages across the whole history and keeps a few random messages from each.

Search is rate-limited hard (a limit shared on Discord's side: about one
request per 5 seconds after the first few), so sampling 70 single positions
(70 pages) took over two minutes on a 10,000-message history. A few random
messages from each of a handful of random pages spans the history just as
well, a little clustered within each page, in far fewer requests.

Positions past the offset limit are reached in windows of 10,000: each window
restarts the search before (``max_id``) the oldest message of the previous
one. The walk takes a fetcher, so it doesn't know about Discord.
"""

import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from itertools import groupby
from typing import Generic, Protocol, TypeVar

PAGE_SIZE = 25
MAX_OFFSET = 9975
WINDOW = MAX_OFFSET + PAGE_SIZE


class HasId(Protocol):
        """Anything with a Discord id (a message)."""

        @property
        def id(self) -> str:
                """The id."""
                ...


Item = TypeVar("Item", bound=HasId)


@dataclass(frozen=True)
class Found:
        """A message from the search: its id and text."""

        id: str
        content: str


@dataclass(frozen=True)
class SampledPage(Generic[Item]):
        """A fetched page and where it starts in the history (0 = newest)."""

        position: int
        items: tuple[Item, ...]


@dataclass(frozen=True)
class PageRequest:
        """One page to fetch and which of its slots were sampled."""

        window: int
        offset: int
        slots: tuple[int, ...]


# Fetch the page at ``offset`` of the search restarted before ``max_id``
# (None for the newest window); returns that page, newest first.
FetchPage = Callable[[str | None, int], Awaitable[Sequence[Item]]]


def sample_positions(
        total: int, count: int, rng: random.Random
) -> tuple[int, ...]:
        """Return ``count`` distinct random positions under ``total``."""
        return tuple(sorted(rng.sample(range(total), min(count, total))))


def plan_pages(positions: Sequence[int]) -> tuple[PageRequest, ...]:
        """Group sorted positions into the pages that hold them."""

        def page_of(position: int) -> tuple[int, int]:
                window, within = divmod(position, WINDOW)
                return window, within - within % PAGE_SIZE

        return tuple(
                PageRequest(
                        window,
                        offset,
                        tuple(p % WINDOW - offset for p in group),
                )
                for (window, offset), group in groupby(positions, key=page_of)
        )


def usable(found: Found) -> bool:
        """Whether a message has text to read (not just an attachment)."""
        return bool(found.content.strip())


def choose(
        found: Sequence[Found], count: int, rng: random.Random
) -> tuple[str, ...]:
        """Return the text of ``count`` random usable messages."""
        texts = [f.content for f in found if usable(f)]
        return tuple(rng.sample(texts, min(count, len(texts))))


async def _window_starts(
        fetch: FetchPage[Item], windows: int
) -> dict[int, str | None]:
        """Return ``max_id`` for each window up to ``windows``.

        Window ``w`` starts before the oldest message on the last page of
        window ``w - 1``. Stops early if the history runs out.
        """
        starts: dict[int, str | None] = {0: None}
        for window in range(1, windows + 1):
                last_page = await fetch(starts[window - 1], MAX_OFFSET)
                if not last_page:
                        break
                starts[window] = last_page[-1].id
        return starts


async def sample_pages(
        fetch: FetchPage[Item],
        total: int,
        pages: int,
        rng: random.Random,
) -> tuple[SampledPage[Item], ...]:
        """Fetch ``pages`` pages drawn uniformly from the whole history.

        Reaches past the offset limit too. Pages are returned newest first,
        each with its position, so callers can tell when in the history
        each one is.
        """
        page_count = -(-total // PAGE_SIZE)
        starts = sample_positions(page_count, pages, rng)
        requests = plan_pages([p * PAGE_SIZE for p in starts])
        if not requests:
                return ()
        windows = await _window_starts(fetch, max(r.window for r in requests))
        result: list[SampledPage[Item]] = []
        for request in requests:
                if request.window not in windows:
                        continue
                page = await fetch(windows[request.window], request.offset)
                if page:
                        result.append(
                                SampledPage(
                                        request.window * WINDOW
                                        + request.offset,
                                        tuple(page),
                                )
                        )
        return tuple(result)


async def sample_history(
        fetch: FetchPage[Found],
        total: int,
        pages: int,
        per_page: int,
        rng: random.Random,
) -> tuple[Found, ...]:
        """Fetch random pages; keep ``per_page`` usable messages from each."""
        found: list[Found] = []
        for page in await sample_pages(fetch, total, pages, rng):
                texts = [f for f in page.items if usable(f)]
                found += rng.sample(texts, min(per_page, len(texts)))
        return tuple(found)
