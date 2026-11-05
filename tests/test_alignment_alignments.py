"""The nine alignments and how people type them."""

import pytest

from doge_alignment.core.alignments import (
        ALIGNMENTS,
        abbreviation,
        as_alignment,
        grid_position,
        matching,
        parse_alignment,
)


def test_nine_alignments_in_chart_order() -> None:
        """The classic 3x3: good row first, lawful column first."""
        assert len(ALIGNMENTS) == 9
        assert ALIGNMENTS[0] == "Lawful Good"
        assert ALIGNMENTS[4] == "True Neutral"
        assert ALIGNMENTS[-1] == "Chaotic Evil"


@pytest.mark.parametrize(
        ("text", "expected"),
        [
                ("Lawful Good", "Lawful Good"),
                ("chaotic-good", "Chaotic Good"),
                ("  CHAOTIC_neutral ", "Chaotic Neutral"),
                ("ce", "Chaotic Evil"),
                ("LN", "Lawful Neutral"),
                ("tn", "True Neutral"),
                ("neutral", "True Neutral"),
                ("NN", "True Neutral"),
                ("n", "True Neutral"),
                ("ne", "Neutral Evil"),
        ],
)
def test_parse_alignment_accepts_common_spellings(
        text: str, expected: str
) -> None:
        """Full names in any case or spacing, and abbreviations."""
        assert parse_alignment(text) == expected


@pytest.mark.parametrize("text", ["", "chaotic", "good", "xx", "lawful bad"])
def test_parse_alignment_rejects_others(text: str) -> None:
        """Half an alignment isn't one."""
        assert parse_alignment(text) is None


def test_as_alignment_is_strict() -> None:
        """as_alignment only takes the exact names."""
        assert as_alignment("Neutral Good") == "Neutral Good"
        with pytest.raises(ValueError, match="not an alignment"):
                as_alignment("ng")


def test_grid_position_and_abbreviation() -> None:
        """Rows are good/neutral/evil, columns lawful/neutral/chaotic."""
        assert grid_position("Lawful Good") == (0, 0)
        assert grid_position("Chaotic Neutral") == (1, 2)
        assert grid_position("Neutral Evil") == (2, 1)
        assert [abbreviation(a) for a in ALIGNMENTS[:3]] == ["LG", "NG", "CG"]


def test_matching_for_autocomplete() -> None:
        """Prefixes narrow the list; abbreviations find their alignment."""
        assert matching("") == ALIGNMENTS
        assert matching("chaotic") == (
                "Chaotic Good",
                "Chaotic Neutral",
                "Chaotic Evil",
        )
        assert matching("cg") == ("Chaotic Good",)
