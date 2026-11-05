"""What gets shown: cells, live names, people who left, text."""

from alignment_helpers import chart_with

from doge_alignment.core.alignments import ALIGNMENTS
from doge_alignment.core.chart import Entry, place
from doge_alignment.core.view import (
        Profile,
        absent,
        cells,
        everyone,
        format_text,
        tile_filename,
)


def test_nine_cells_in_order_even_when_empty() -> None:
        """Every alignment gets a cell, in chart order."""
        result = cells(chart_with(("1", "Chaotic Evil")))
        assert tuple(c.alignment for c in result) == ALIGNMENTS
        assert [len(c.people) for c in result] == [0] * 8 + [1]


def test_live_profiles_win_and_absent_people_are_marked() -> None:
        """Live names replace saved ones; unknown people have left."""
        chart = place(
                chart_with(("1", "Lawful Good")),
                "2",
                Entry("Lawful Good", "old name", "https://x/old.png"),
        )
        live = {"1": Profile("Alice", "https://x/a.png")}
        (good, *_) = cells(chart, live.get)
        assert [(p.name, p.present) for p in good.people] == [
                ("Alice", True),
                ("old name", False),
        ]
        assert good.people[1].avatar_url == "https://x/old.png"
        assert absent(chart, live.get) == ("2",)


def test_without_lookup_everyone_is_present() -> None:
        """Charts from files have nobody to look up."""
        result = cells(chart_with(("1", "True Neutral")))
        assert all(p.present for p in everyone(result))


def test_people_sorted_by_name_case_insensitively() -> None:
        """Within a cell, present people come first, then by name."""
        chart = chart_with(("1", "True Neutral"), ("2", "True Neutral"))
        chart = place(chart, "3", Entry("True Neutral", "Bob"))
        names = {"1": Profile("carol"), "3": Profile("Bob")}
        middle = cells(chart, names.get)[4]
        assert [p.name for p in middle.people] == ["Bob", "carol", "user2"]


def test_format_text_skips_empty_cells_and_marks_leavers() -> None:
        """Text lists only cells with people, in chart order."""
        chart = chart_with(("1", "Chaotic Evil"), ("2", "Lawful Good"))
        text = format_text(cells(chart, {"1": Profile("Eve")}.get))
        assert text.splitlines() == [
                "**Lawful Good**: ~~user2~~ (left)",
                "**Chaotic Evil**: Eve",
        ]


def test_tile_filenames() -> None:
        """Tiles are numbered in chart order with the abbreviation."""
        result = cells(chart_with())
        assert tile_filename(1, result[0]) == "1-lg.png"
        assert tile_filename(9, result[8]) == "9-ce.png"
