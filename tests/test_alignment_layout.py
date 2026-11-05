"""Pure layout: avatars fit their cells and never overlap."""

import itertools

import pytest
from alignment_helpers import chart_with

from doge_alignment.core.alignments import ALIGNMENTS
from doge_alignment.core.layout import (
        Box,
        Theme,
        best_grid,
        capacity,
        plan_cell,
        plan_chart,
        plan_tile,
        scaled,
        truncate,
)
from doge_alignment.core.view import Cell, Person, cells


def people(n: int) -> tuple[Person, ...]:
        """Return ``n`` people named p0, p1, ..."""
        return tuple(Person(str(i), f"p{i}", None) for i in range(n))


def inside(inner: Box, outer: Box) -> bool:
        """Whether ``inner`` lies within ``outer``."""
        return (
                outer.x <= inner.x
                and outer.y <= inner.y
                and inner.x + inner.w <= outer.x + outer.w
                and inner.y + inner.h <= outer.y + outer.h
        )


def overlap(a: Box, b: Box) -> bool:
        """Whether two boxes overlap."""
        return (
                a.x < b.x + b.w
                and b.x < a.x + a.w
                and a.y < b.y + b.h
                and b.y < a.y + a.h
        )


@pytest.mark.parametrize("count", [1, 2, 3, 5, 9, 14, 40])
def test_avatars_fit_and_do_not_overlap(count: int) -> None:
        """Every avatar is inside its cell, below the label, apart."""
        theme = Theme()
        box = Box(100, 50, theme.cell_size, theme.cell_size)
        plan = plan_cell(Cell("True Neutral", people(count)), box, theme)
        assert len(plan.slots) == count
        assert plan.overflow == 0
        for slot in plan.slots:
                assert inside(slot.avatar, box)
                assert slot.avatar.y >= plan.label_box.y + plan.label_box.h
                assert theme.min_avatar <= slot.avatar.w <= theme.max_avatar
        for a, b in itertools.combinations(plan.slots, 2):
                assert not overlap(a.avatar, b.avatar)


def test_names_only_when_avatars_are_big_enough() -> None:
        """Few people get names; crowds don't."""
        theme = Theme()
        box = Box(0, 0, theme.cell_size, theme.cell_size)
        few = plan_cell(Cell("Lawful Good", people(3)), box, theme)
        crowd = plan_cell(Cell("Lawful Good", people(40)), box, theme)
        assert all(s.name_box is not None for s in few.slots)
        assert all(s.name_box is None for s in crowd.slots)


def test_overflow_badge_when_too_many() -> None:
        """Past capacity, the last slot becomes a +N badge."""
        theme = Theme()
        box = Box(0, 0, theme.cell_size, theme.cell_size)
        plan = plan_cell(Cell("Chaotic Evil", people(500)), box, theme)
        assert plan.overflow_box is not None
        assert len(plan.slots) + plan.overflow == 500
        assert len(plan.slots) + 1 <= capacity(
                Box(0, 0, theme.cell_size, theme.cell_size), theme
        )


def test_best_grid_prefers_bigger_avatars() -> None:
        """Two people side by side beat two stacked in a wide area."""
        grid = best_grid(2, Box(0, 0, 400, 150), Theme())
        assert (grid.columns, grid.rows) == (2, 1)


def test_chart_plan_is_a_3x3_square() -> None:
        """Cells sit in chart order on a square picture."""
        theme = Theme()
        plan = plan_chart(cells(chart_with()), theme)
        assert plan.width == plan.height == 2 * 16 + 3 * 360 + 2 * 12
        xs = sorted({c.box.x for c in plan.cells})
        ys = sorted({c.box.y for c in plan.cells})
        assert len(xs) == len(ys) == 3
        assert [c.alignment for c in plan.cells] == list(ALIGNMENTS)
        assert plan.cells[5].box.x == xs[2]
        assert plan.cells[5].box.y == ys[1]


def test_tile_is_a_scaled_cell() -> None:
        """A tile fills its picture and scales sizes up."""
        theme = Theme()
        plan = plan_tile(Cell("Lawful Good", people(1)), theme)
        assert (plan.width, plan.height) == (theme.tile_size,) * 2
        (cell,) = plan.cells
        assert cell.slots[0].avatar.w > theme.max_avatar
        assert scaled(theme, 2).max_avatar == theme.max_avatar * 2


def test_truncate() -> None:
        """Long names end in an ellipsis at the limit."""
        assert truncate("short", 10) == "short"
        assert truncate("a very long name", 6) == "a ver…"
