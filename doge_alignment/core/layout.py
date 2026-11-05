"""Where everything goes in a chart picture. Pure geometry, no drawing.

:func:`plan_chart` lays out the 3x3 chart as one picture and
:func:`plan_tile` lays out a single cell as its own square picture, for front
ends that show the nine squares separately (Discord's media gallery).
:mod:`.render` paints the plans.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from .alignments import AlignmentName, grid_position
from .view import Cell, Person

DEFAULT_CELL_COLORS: Mapping[AlignmentName, str] = MappingProxyType(
        {
                "Lawful Good": "#2f6f8f",
                "Neutral Good": "#2f8f6a",
                "Chaotic Good": "#5f8f2f",
                "Lawful Neutral": "#4a5a8a",
                "True Neutral": "#6b6b6b",
                "Chaotic Neutral": "#8a7a3a",
                "Lawful Evil": "#6a3f8a",
                "Neutral Evil": "#8a3f6a",
                "Chaotic Evil": "#9a3a2f",
        }
)


@dataclass(frozen=True)
class Theme:
        """Sizes and colours. Sizes are in pixels."""

        background: str = "#1e1f22"
        cell_colors: Mapping[AlignmentName, str] = field(
                default_factory=lambda: DEFAULT_CELL_COLORS
        )
        text_color: str = "#ffffff"
        ring_color: str = "#ffffff"
        absent_ring_color: str = "#555555"
        placeholder_color: str = "#40444b"
        # Font family for Pango, and a TrueType file for when Pango is
        # missing (None lets :mod:`.render` find one)
        family: str = "DejaVu Sans"
        font: str | None = None
        cell_size: int = 360
        tile_size: int = 512
        gap: int = 12
        margin: int = 16
        padding: int = 12
        corner_radius: int = 18
        label_ratio: float = 0.13
        avatar_gap: int = 8
        max_avatar: int = 128
        min_avatar: int = 36
        # Names go under avatars at least this big
        min_named_avatar: int = 56
        # Long cell labels shrink to this font size, then get cut
        min_label: int = 16
        ring_ratio: float = 0.05


@dataclass(frozen=True)
class Box:
        """A rectangle: top-left corner and size."""

        x: int
        y: int
        w: int
        h: int


@dataclass(frozen=True)
class Slot:
        """One person's avatar, and their name under it if there's room."""

        person: Person
        avatar: Box
        name: str = ""
        name_box: Box | None = None
        font_size: int = 0


@dataclass(frozen=True)
class CellPlan:
        """One cell: its box, its label, and the avatars in it."""

        alignment: AlignmentName
        box: Box
        label_box: Box
        label_size: int
        label: str = ""
        color: str = "#6b6b6b"
        slots: tuple[Slot, ...] = ()
        # "+N" badge for people who didn't fit, and how many
        overflow: int = 0
        overflow_box: Box | None = None


@dataclass(frozen=True)
class Plan:
        """A whole picture."""

        width: int
        height: int
        cells: tuple[CellPlan, ...]


@dataclass(frozen=True)
class Grid:
        """How to arrange avatars in a cell."""

        columns: int
        rows: int
        size: int
        name_height: int


def name_height(size: int) -> int:
        """Height of the name line under an avatar of ``size``."""
        return round(min(max(size * 0.2, 12), 20)) + 4


def _fit(count: int, columns: int, area: Box, gap: int, *, names: bool) -> Grid:
        rows = math.ceil(count / columns)
        by_width = (area.w - (columns - 1) * gap) // columns
        by_height = (area.h - (rows - 1) * gap) // rows
        if not names:
                return Grid(columns, rows, min(by_width, by_height), 0)
        # name_height grows with size, so solve by_height - name_height(s)
        # for s from the largest name line down
        size = min(by_width, by_height - name_height(by_height))
        return Grid(columns, rows, size, name_height(size))


# Average width of a bold character, as a fraction of the font size
CHAR_WIDTH = 0.68

# Names are worth this much avatar size
NAMED_BONUS = 1.3


def _score(grid: Grid, theme: Theme) -> tuple[float, int]:
        size = min(grid.size, theme.max_avatar)
        return (size * (NAMED_BONUS if grid.name_height else 1), -grid.rows)


def best_grid(count: int, area: Box, theme: Theme) -> Grid:
        """Return the arrangement with the biggest avatars for ``count``.

        Names are shown when avatars can still be at least
        ``theme.min_named_avatar`` with them, and preferred unless they
        shrink avatars by more than :data:`NAMED_BONUS`.
        """
        candidates = [
                grid
                for columns in range(1, count + 1)
                for grid in (
                        _fit(
                                count,
                                columns,
                                area,
                                theme.avatar_gap,
                                names=True,
                        ),
                        _fit(
                                count,
                                columns,
                                area,
                                theme.avatar_gap,
                                names=False,
                        ),
                )
                if grid.name_height == 0 or grid.size >= theme.min_named_avatar
        ]
        best = max(candidates, key=lambda g: _score(g, theme))
        return replace(best, size=min(best.size, theme.max_avatar))


def capacity(area: Box, theme: Theme) -> int:
        """How many smallest avatars fit in ``area``."""
        step = theme.min_avatar + theme.avatar_gap
        return ((area.w + theme.avatar_gap) // step) * (
                (area.h + theme.avatar_gap) // step
        )


def max_chars(width: int, font_size: int) -> int:
        """How many characters fit in ``width`` at ``font_size``."""
        return max(int(width / (font_size * CHAR_WIDTH)), 3)


def fit_font(text: str, width: int, largest: int) -> int:
        """Return the font size, at most ``largest``, that fits ``text``."""
        if not text:
                return largest
        return max(min(largest, int(width / (len(text) * CHAR_WIDTH))), 1)


def truncate(text: str, max_chars: int) -> str:
        """Shorten ``text`` to ``max_chars``, ending in an ellipsis if cut."""
        if len(text) <= max_chars:
                return text
        return text[: max(max_chars - 1, 0)] + "…"


def _row_boxes(grid: Grid, count: int, area: Box, gap: int) -> list[Box]:
        cell_h = grid.size + grid.name_height
        block_h = grid.rows * cell_h + (grid.rows - 1) * gap
        top = area.y + (area.h - block_h) // 2
        boxes: list[Box] = []
        for row in range(grid.rows):
                in_row = min(grid.columns, count - row * grid.columns)
                row_w = in_row * grid.size + (in_row - 1) * gap
                left = area.x + (area.w - row_w) // 2
                boxes += [
                        Box(
                                left + i * (grid.size + gap),
                                top + row * (cell_h + gap),
                                grid.size,
                                grid.size,
                        )
                        for i in range(in_row)
                ]
        return boxes


def _slot(person: Person, box: Box, grid: Grid, gap: int) -> Slot:
        if not grid.name_height:
                return Slot(person, box)
        font_size = grid.name_height - 4
        width = box.w + gap
        # Keep a few pixels between neighbouring names
        return Slot(
                person,
                box,
                truncate(person.name, max_chars(width - 6, font_size)),
                Box(box.x - gap // 2, box.y + box.h, width, grid.name_height),
                font_size,
        )


def plan_cell(cell: Cell, box: Box, theme: Theme) -> CellPlan:
        """Lay out ``cell`` inside ``box``."""
        label_h = round(box.h * theme.label_ratio)
        pad = theme.padding
        label_box = Box(box.x + pad, box.y + pad, box.w - 2 * pad, label_h)
        area = Box(
                box.x + pad,
                label_box.y + label_h + pad // 2,
                box.w - 2 * pad,
                box.h - label_h - 2 * pad - pad // 2,
        )
        largest = round(label_h * 0.62)
        label_size = max(
                fit_font(cell.title, label_box.w, largest),
                min(theme.min_label, largest),
        )
        base = CellPlan(
                cell.alignment,
                box,
                label_box,
                label_size,
                truncate(cell.title, max_chars(label_box.w, label_size)),
                cell.color or theme.cell_colors[cell.alignment],
        )
        people = cell.people
        if not people:
                return base
        fits = capacity(area, theme)
        overflow = len(people) - fits + 1 if len(people) > fits else 0
        shown = people[: len(people) - overflow] if overflow else people
        grid = best_grid(len(shown) + (1 if overflow else 0), area, theme)
        boxes = _row_boxes(
                grid,
                len(shown) + (1 if overflow else 0),
                area,
                theme.avatar_gap,
        )
        return replace(
                base,
                slots=tuple(
                        _slot(p, b, grid, theme.avatar_gap)
                        for p, b in zip(shown, boxes, strict=False)
                ),
                overflow=overflow,
                overflow_box=boxes[-1] if overflow else None,
        )


def plan_chart(chart_cells: tuple[Cell, ...], theme: Theme) -> Plan:
        """Lay out the nine cells as one picture."""
        step = theme.cell_size + theme.gap
        side = 2 * theme.margin + 3 * theme.cell_size + 2 * theme.gap

        def box(cell: Cell) -> Box:
                row, column = grid_position(cell.alignment)
                return Box(
                        theme.margin + column * step,
                        theme.margin + row * step,
                        theme.cell_size,
                        theme.cell_size,
                )

        return Plan(
                side,
                side,
                tuple(plan_cell(c, box(c), theme) for c in chart_cells),
        )


_PIXEL_FIELDS = (
        "gap",
        "margin",
        "padding",
        "corner_radius",
        "avatar_gap",
        "max_avatar",
        "min_avatar",
        "min_named_avatar",
        "min_label",
)


def scaled(theme: Theme, factor: float) -> Theme:
        """Return ``theme`` with every pixel size multiplied by ``factor``."""
        return replace(
                theme,
                **{
                        name: round(getattr(theme, name) * factor)
                        for name in _PIXEL_FIELDS
                },
        )


def plan_tile(cell: Cell, theme: Theme) -> Plan:
        """Lay out one cell as a square picture of its own.

        Everything is scaled from ``theme.cell_size`` up to
        ``theme.tile_size``, so a tile looks like a big chart cell.
        """
        size = theme.tile_size
        big = scaled(theme, size / theme.cell_size)
        return Plan(size, size, (plan_cell(cell, Box(0, 0, size, size), big),))
