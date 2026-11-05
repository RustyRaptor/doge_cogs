"""Custom charts: a server's own axes, cell names and colours.

A chart always has the same nine positions, named internally by their classic
alignment (``"Lawful Good"`` is the top-left cell). A :class:`ChartSpec`
relabels them: other column and row names (``Pineapple``/``Neutral``/
``Anchovy``), a different name for any single cell, other colours, and a
title. Entries keep the classic key, so changing labels never moves anyone.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from .alignments import ALIGNMENTS, AlignmentName, parse_alignment

CLASSIC_COLUMNS = ("Lawful", "Neutral", "Chaotic")
CLASSIC_ROWS = ("Good", "Neutral", "Evil")
MAX_LABEL = 32
MAX_TITLE = 64

Triple = tuple[str, str, str]

_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


class SpecError(ValueError):
        """A label, title or colour that can't be used."""


def _frozen(
        mapping: Mapping[AlignmentName, str],
) -> Mapping[AlignmentName, str]:
        return MappingProxyType(dict(mapping))


@dataclass(frozen=True)
class ChartSpec:
        """How a server's chart is labelled and coloured."""

        title: str = "Alignment chart"
        columns: Triple = CLASSIC_COLUMNS
        rows: Triple = CLASSIC_ROWS
        # Per-cell names and colours that override the defaults
        labels: Mapping[AlignmentName, str] = field(
                default_factory=lambda: _frozen({})
        )
        colors: Mapping[AlignmentName, str] = field(
                default_factory=lambda: _frozen({})
        )


def _clean(text: str, limit: int) -> str:
        cleaned = " ".join(text.split())
        if not cleaned:
                msg = "labels can't be empty"
                raise SpecError(msg)
        if len(cleaned) > limit:
                msg = f"keep it to {limit} characters"
                raise SpecError(msg)
        return cleaned


def composed_label(column: str, row: str) -> str:
        """Name a cell from its column and row: ``Chaotic Good``.

        A cell whose column and row have the same name gets ``True`` in
        front, as the classic chart's middle does.
        """
        if column.casefold() == row.casefold():
                return f"True {column}"
        return f"{column} {row}"


def label(spec: ChartSpec, alignment: AlignmentName) -> str:
        """Return what ``spec`` calls the cell at ``alignment``."""
        if alignment in spec.labels:
                return spec.labels[alignment]
        row, column = divmod(ALIGNMENTS.index(alignment), 3)
        return composed_label(spec.columns[column], spec.rows[row])


def labels(spec: ChartSpec) -> tuple[str, ...]:
        """Return all nine labels in chart order."""
        return tuple(label(spec, a) for a in ALIGNMENTS)


def _squash(text: str) -> str:
        return re.sub(r"[^\w]", "", text.casefold())


def _initials(text: str) -> str:
        return "".join(word[0] for word in text.split()).casefold()


def parse_cell(text: str, spec: ChartSpec) -> AlignmentName | None:
        """Read a cell the way people type it on ``spec``'s chart.

        Takes the server's own labels (any case or spacing) and their
        initials when those are unambiguous, then falls back to the classic
        names and abbreviations, so ``CG`` always means the top-right cell.
        """
        squashed = _squash(text)
        if not squashed:
                return None
        by_label = {_squash(label(spec, a)): a for a in ALIGNMENTS}
        if squashed in by_label:
                return by_label[squashed]
        initials = [
                a for a in ALIGNMENTS if _initials(label(spec, a)) == squashed
        ]
        if len(initials) == 1:
                return initials[0]
        return parse_alignment(text)


def matching_cells(
        prefix: str, spec: ChartSpec
) -> tuple[tuple[AlignmentName, str], ...]:
        """Return ``(cell, label)`` pairs a partly typed label could mean."""
        wanted = _squash(prefix)
        exact = parse_cell(prefix, spec)
        return tuple(
                (a, label(spec, a))
                for a in ALIGNMENTS
                if a == exact or _squash(label(spec, a)).startswith(wanted)
        )


def is_custom(spec: ChartSpec) -> bool:
        """Whether ``spec`` changes anything from the classic chart."""
        return spec != ChartSpec()


def with_title(spec: ChartSpec, title: str) -> ChartSpec:
        """Return ``spec`` with a new title.

        Raises:
                SpecError: for an empty or over-long title.

        """
        return replace(spec, title=_clean(title, MAX_TITLE))


def _triple(names: tuple[str, ...]) -> Triple:
        if len(names) != 3:  # noqa: PLR2004
                msg = "give exactly three names"
                raise SpecError(msg)
        a, b, c = (_clean(n, MAX_LABEL) for n in names)
        return (a, b, c)


def with_columns(spec: ChartSpec, *names: str) -> ChartSpec:
        """Return ``spec`` with new column names, left to right.

        Raises:
                SpecError: unless given three usable names.

        """
        return replace(spec, columns=_triple(names))


def with_rows(spec: ChartSpec, *names: str) -> ChartSpec:
        """Return ``spec`` with new row names, top to bottom.

        Raises:
                SpecError: unless given three usable names.

        """
        return replace(spec, rows=_triple(names))


def with_label(
        spec: ChartSpec, alignment: AlignmentName, text: str | None
) -> ChartSpec:
        """Return ``spec`` with one cell renamed, or back to its default.

        Raises:
                SpecError: for an empty or over-long label.

        """
        rest = {k: v for k, v in spec.labels.items() if k != alignment}
        if text is None:
                return replace(spec, labels=_frozen(rest))
        return replace(
                spec,
                labels=_frozen({**rest, alignment: _clean(text, MAX_LABEL)}),
        )


def parse_color(text: str) -> str:
        """Return ``text`` as a ``#rrggbb`` colour.

        Raises:
                SpecError: for anything else.

        """
        value = text.strip()
        value = value if value.startswith("#") else f"#{value}"
        if not _HEX.match(value):
                msg = "colours look like #3a7bd5"
                raise SpecError(msg)
        return value.lower()


def with_color(
        spec: ChartSpec, alignment: AlignmentName, color: str | None
) -> ChartSpec:
        """Return ``spec`` with one cell's colour set, or back to default.

        Raises:
                SpecError: for something that isn't a ``#rrggbb`` colour.

        """
        rest = {k: v for k, v in spec.colors.items() if k != alignment}
        if color is None:
                return replace(spec, colors=_frozen(rest))
        return replace(
                spec, colors=_frozen({**rest, alignment: parse_color(color)})
        )
