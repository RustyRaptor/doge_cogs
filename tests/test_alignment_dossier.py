"""Assembling the dossier from fetched pages and contexts."""

import random

from alignment_messages import msg

from doge_alignment.core.colors import describe_palette, name_color
from doge_alignment.core.dossier import anchors, build_dossier, evidence
from doge_alignment.core.guess import (
        Dossier,
        Evidence,
        build_prompt,
        estimate_tokens,
        pack,
        pages_for,
)
from doge_alignment.core.sampling import SampledPage
from doge_alignment.core.spec import ChartSpec


def pages() -> tuple[SampledPage, ...]:
        """Two pages of 'me', newest first, in different channels."""
        newer = SampledPage(
                0,
                (
                        msg(3, minutes=600, channel="c2"),
                        msg(2, minutes=599, channel="c2"),
                ),
        )
        older = SampledPage(500, (msg(1, minutes=0),))
        return (newer, older)


def test_anchors_one_per_page() -> None:
        """Each page's newest message pins its position."""
        assert [(a.position, a.time.minute) for a in anchors(pages())] == [
                (0, 0),
                (500, 0),
        ]


def test_evidence_pools_pages_with_context() -> None:
        """Context joins the page's own messages; groups follow pages."""
        context = {1: (msg(10, "a", -1, "hi"), msg(11, "a", 0.5, "you there?"))}
        items = evidence(pages(), context, "me", "Doge")
        assert [e.group for e in items] == [0, 1]
        assert "A: hi" in items[1].text
        assert "Doge:" in items[0].text


def test_build_dossier() -> None:
        """Activity, avatar notes and evidence all land in the dossier."""
        d = build_dossier(
                name="Doge",
                target_id="me",
                pages=pages(),
                contexts={},
                total=1234,
                avatar="a dog",
                palette="gold 60%",
        )
        assert "Messages in this server: 1,234" in d.activity
        assert d.avatar == "a dog"
        assert len(d.evidence) == 2


def test_pack_spreads_over_groups_and_budget() -> None:
        """Round-robin by group, within budget, oldest first."""
        rng = random.Random(0)
        items = [
                Evidence(
                        msg(i).created.replace(day=1 + i % 28),
                        "x" * rng.randint(50, 300),
                        i % 4,
                )
                for i in range(40)
        ]
        chosen = pack(items, 400)
        assert sum(estimate_tokens(e.text) + 2 for e in chosen) <= 400
        assert {e.group for e in chosen} == {0, 1, 2, 3}
        assert list(chosen) == sorted(chosen, key=lambda e: e.when)
        assert pack(items, 0) == ()


def test_build_prompt_fills_the_budget() -> None:
        """Bigger budgets quote more; the head is always there."""
        items = tuple(
                Evidence(msg(i).created, f"[t]\nDoge: {'word ' * 40}", i % 5)
                for i in range(200)
        )
        d = Dossier(
                name="Doge",
                activity="ACT",
                avatar="pic",
                palette="red",
                evidence=items,
        )
        small = build_prompt(d, ChartSpec(), 4_000)[1]["content"]
        large = build_prompt(d, ChartSpec(), 20_000)[1]["content"]
        assert "## Activity\nACT" in small
        assert "Description: pic\nColours: red" in small
        assert large.count("Doge: word") > 2 * small.count("Doge: word")
        assert sum(estimate_tokens(m) for m in [large]) < 20_000


def test_pages_for_budget() -> None:
        """More budget, more pages, within limits."""
        assert pages_for(4_000) == 6
        assert pages_for(32_000) == 25
        assert pages_for(131_072) == 40


def test_palette_names() -> None:
        """Hex colours get everyday names; duplicates merge."""
        assert name_color("#ff0000") == "red"
        assert name_color("#101010") == "black"
        assert describe_palette(
                [("#ff0000", 0.5), ("#dd1133", 0.2), ("#ffffff", 0.3)]
        ) == ("red 70%, white 30%")
