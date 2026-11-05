"""Random samples from a whole message history, a few pages at a time."""

import random
from collections import Counter

from doge_alignment.core.sampling import (
        MAX_OFFSET,
        PAGE_SIZE,
        WINDOW,
        Found,
        PageRequest,
        choose,
        plan_pages,
        sample_history,
        sample_positions,
)


class FakeHistory:
        """A newest-first history of ``n`` messages, searched like Discord.

        Message ids count down from ``n`` (newest) to 1; ``max_id`` restarts
        the search before that id, and offsets past MAX_OFFSET are refused.
        """

        def __init__(self, n: int, *, blank_every: int = 0) -> None:
                """Build the history."""
                self.ids = list(range(n, 0, -1))
                self.blank_every = blank_every
                self.requests: list[tuple[str | None, int]] = []

        def content(self, message_id: int) -> str:
                """Text of a message; some are blank (attachments)."""
                if self.blank_every and message_id % self.blank_every == 0:
                        return ""
                return f"m{message_id}"

        async def fetch(self, max_id: str | None, offset: int) -> list[Found]:
                """Return one page."""
                assert offset <= MAX_OFFSET
                assert offset % PAGE_SIZE == 0
                self.requests.append((max_id, offset))
                pool = [
                        i for i in self.ids if max_id is None or i < int(max_id)
                ]
                return [
                        Found(str(i), self.content(i))
                        for i in pool[offset : offset + PAGE_SIZE]
                ]


def test_sample_positions() -> None:
        """Distinct, sorted, in range, and never more than there are."""
        rng = random.Random(1)
        positions = sample_positions(1000, 70, rng)
        assert len(set(positions)) == 70
        assert list(positions) == sorted(positions)
        assert all(0 <= p < 1000 for p in positions)
        assert sample_positions(10, 70, rng) == tuple(range(10))
        assert sample_positions(0, 70, rng) == ()


def test_plan_pages_groups_by_page_and_window() -> None:
        """Positions map to (window, page offset, slot)."""
        assert plan_pages([0, 3, 24, 25, 9999, 10000, 10026]) == (
                PageRequest(0, 0, (0, 3, 24)),
                PageRequest(0, 25, (0,)),
                PageRequest(0, 9975, (24,)),
                PageRequest(1, 0, (0,)),
                PageRequest(1, 25, (1,)),
        )
        assert WINDOW == 10_000


async def test_pages_and_per_page() -> None:
        """One request per page; at most per_page usable from each."""
        history = FakeHistory(500, blank_every=4)
        found = await sample_history(history.fetch, 500, 6, 3, random.Random(7))
        assert len(history.requests) == 6
        assert len(found) == 18
        assert all(f.content for f in found)
        # Each page's picks are from that page
        offsets = {offset for _, offset in history.requests}
        assert all(
                any(o <= 500 - int(f.id) < o + PAGE_SIZE for o in offsets)
                for f in found
        )


async def test_reaches_past_the_offset_limit() -> None:
        """Deep pages are fetched through later windows."""
        history = FakeHistory(25_000)
        found = await sample_history(
                history.fetch, 25_000, 30, 2, random.Random(3)
        )
        ids = [int(f.id) for f in found]
        assert len(ids) == 60
        assert min(ids) <= 25_000 - WINDOW
        # Two window boundaries plus one request per page
        assert len(history.requests) == 30 + 2


async def test_sample_covers_the_whole_history_evenly() -> None:
        """Over many draws, old and new messages are picked alike."""
        history = FakeHistory(2_000)
        thirds: Counter[int] = Counter()
        for seed in range(40):
                found = await sample_history(
                        history.fetch, 2_000, 10, 3, random.Random(seed)
                )
                thirds.update(min(int(f.id) * 3 // 2_001, 2) for f in found)
        assert all(300 <= thirds[t] <= 500 for t in range(3))


async def test_short_histories_and_wrong_totals() -> None:
        """Fewer pages than asked; a too-big total just finds less."""
        history = FakeHistory(40)
        found = await sample_history(
                history.fetch, 100, 18, 3, random.Random(0)
        )
        assert len(found) == 6
        assert (
                await sample_history(history.fetch, 0, 18, 3, random.Random(0))
                == ()
        )


def test_choose_skips_blank_messages() -> None:
        """Attachment-only messages don't count."""
        found = [Found(str(i), "" if i % 2 else f"t{i}") for i in range(20)]
        chosen = choose(found, 35, random.Random(0))
        assert len(chosen) == 10
        assert all(t.startswith("t") for t in chosen)
        assert len(choose(found, 3, random.Random(0))) == 3
