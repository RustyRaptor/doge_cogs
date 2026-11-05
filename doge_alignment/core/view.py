"""What to show: the chart's nine cells, with live names where known."""

from collections.abc import Callable
from dataclasses import dataclass

from .alignments import ALIGNMENTS, AlignmentName, abbreviation
from .chart import Chart, Entry
from .spec import label


@dataclass(frozen=True)
class Profile:
        """A person's current name and avatar, from the front end."""

        name: str
        avatar_url: str | None = None


# Live profile for a user id, or None if they aren't around any more
Lookup = Callable[[str], Profile | None]


@dataclass(frozen=True)
class Person:
        """Someone to draw in a cell."""

        user_id: str
        name: str
        avatar_url: str | None
        present: bool = True


@dataclass(frozen=True)
class Cell:
        """One square of the chart and the people in it, sorted by name.

        ``label`` is what the chart calls the square; ``color`` overrides the
        theme's colour for it.
        """

        alignment: AlignmentName
        people: tuple[Person, ...] = ()
        label: str = ""
        color: str | None = None

        @property
        def title(self) -> str:
                """The label, or the classic name if there is none."""
                return self.label or self.alignment


def person(user_id: str, entry: Entry, lookup: Lookup | None) -> Person:
        """Return how to show ``user_id``.

        With no ``lookup`` (say, a chart read from a file) everyone counts as
        present. Otherwise people the lookup doesn't know have left, and are
        shown with the name and avatar saved when they were placed.
        """
        live = (
                lookup(user_id)
                if lookup
                else Profile(entry.display_name, entry.avatar_url)
        )
        if live is None:
                return Person(
                        user_id,
                        entry.display_name,
                        entry.avatar_url,
                        present=False,
                )
        return Person(user_id, live.name, live.avatar_url)


def _sort_key(p: Person) -> tuple[bool, str, str]:
        return (not p.present, p.name.casefold(), p.user_id)


def _sorted_in(
        people: tuple[tuple[AlignmentName, Person], ...],
        alignment: AlignmentName,
) -> tuple[Person, ...]:
        return tuple(
                sorted(
                        (p for a, p in people if a == alignment),
                        key=_sort_key,
                )
        )


def cells(chart: Chart, lookup: Lookup | None = None) -> tuple[Cell, ...]:
        """Return all nine cells in chart order, empty ones included."""
        people = tuple(
                (entry.alignment, person(uid, entry, lookup))
                for uid, entry in chart.users.items()
        )
        return tuple(
                Cell(
                        alignment,
                        _sorted_in(people, alignment),
                        label(chart.spec, alignment),
                        chart.spec.colors.get(alignment),
                )
                for alignment in ALIGNMENTS
        )


def absent(chart: Chart, lookup: Lookup) -> tuple[str, ...]:
        """Return the ids on ``chart`` that ``lookup`` doesn't know."""
        return tuple(uid for uid in chart.users if lookup(uid) is None)


def everyone(chart_cells: tuple[Cell, ...]) -> tuple[Person, ...]:
        """Return every person in ``chart_cells``."""
        return tuple(p for cell in chart_cells for p in cell.people)


def _label(p: Person) -> str:
        return p.name if p.present else f"~~{p.name}~~ (left)"


def format_text(chart_cells: tuple[Cell, ...]) -> str:
        """Return the chart as Markdown lines, leaving out empty cells."""
        return "\n".join(
                f"**{cell.title}**: "
                + ", ".join(_label(p) for p in cell.people)
                for cell in chart_cells
                if cell.people
        )


def tile_filename(index: int, cell: Cell) -> str:
        """Return a file name for a cell's own picture, like ``1-lg.png``."""
        return f"{index}-{abbreviation(cell.alignment).lower()}.png"
