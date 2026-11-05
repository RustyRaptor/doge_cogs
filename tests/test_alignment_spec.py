"""Custom chart labels, colours and parsing on them."""

import pytest

from doge_alignment.core.alignments import ALIGNMENTS
from doge_alignment.core.spec import (
        ChartSpec,
        SpecError,
        composed_label,
        is_custom,
        label,
        labels,
        matching_cells,
        parse_cell,
        parse_color,
        with_color,
        with_columns,
        with_label,
        with_rows,
        with_title,
)

PIZZA = with_label(
        with_columns(ChartSpec(), "Purist", "Neutral", "Rebel"),
        "True Neutral",
        "Margherita",
)


def test_classic_labels_are_the_alignments() -> None:
        """The default spec names cells exactly as the classic chart."""
        assert labels(ChartSpec()) == ALIGNMENTS
        assert not is_custom(ChartSpec())


def test_composed_labels_and_overrides() -> None:
        """Cells are column + row, unless renamed one by one."""
        assert label(PIZZA, "Lawful Good") == "Purist Good"
        assert label(PIZZA, "Chaotic Evil") == "Rebel Evil"
        assert label(PIZZA, "True Neutral") == "Margherita"
        assert composed_label("Meh", "meh") == "True Meh"
        assert is_custom(PIZZA)


@pytest.mark.parametrize(
        ("text", "cell"),
        [
                ("purist good", "Lawful Good"),
                ("REBEL-evil", "Chaotic Evil"),
                ("margherita", "True Neutral"),
                ("re", "Chaotic Evil"),
                ("CG", "Chaotic Good"),
                ("chaotic good", "Chaotic Good"),
        ],
)
def test_parse_cell_on_custom_chart(text: str, cell: str) -> None:
        """Custom labels and their initials, then the classic names."""
        assert parse_cell(text, PIZZA) == cell


def test_ambiguous_initials_fall_back_to_classic() -> None:
        """'NG' could be 'Neutral Good' twice over; classic wins."""
        spec = with_columns(ChartSpec(), "Nice", "Neutral", "Nasty")
        assert parse_cell("ng", spec) == "Neutral Good"
        assert parse_cell("", spec) is None
        assert parse_cell("pizza", spec) is None


def test_matching_cells_for_autocomplete() -> None:
        """Typed prefixes match labels; labels come back for display."""
        assert matching_cells("rebel", PIZZA) == (
                ("Chaotic Good", "Rebel Good"),
                ("Chaotic Neutral", "Rebel Neutral"),
                ("Chaotic Evil", "Rebel Evil"),
        )
        assert len(matching_cells("", PIZZA)) == 9


def test_validation() -> None:
        """Labels need text, a sane length, and exactly three per axis."""
        with pytest.raises(SpecError, match="empty"):
                with_title(ChartSpec(), "   ")
        with pytest.raises(SpecError, match="32"):
                with_rows(ChartSpec(), "x" * 40, "b", "c")
        with pytest.raises(SpecError, match="three"):
                with_columns(ChartSpec(), "a", "b")
        assert (
                with_title(ChartSpec(), "  Pizza   chart ").title
                == "Pizza chart"
        )


def test_reset_label_and_colors() -> None:
        """None goes back to the default; colours are normalised."""
        assert label(
                with_label(PIZZA, "True Neutral", None), "True Neutral"
        ) == ("True Neutral")
        spec = with_color(ChartSpec(), "Lawful Good", "AA22CC")
        assert spec.colors["Lawful Good"] == "#aa22cc"
        assert with_color(spec, "Lawful Good", None).colors == {}
        assert parse_color("#123abc") == "#123abc"
        with pytest.raises(SpecError):
                parse_color("red")


def test_specs_are_immutable() -> None:
        """Changes return new specs."""
        before = ChartSpec()
        with_label(before, "Lawful Good", "x")
        assert before.labels == {}
