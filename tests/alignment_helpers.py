"""Shared builders for the alignment tests."""

from doge_alignment.core.alignments import AlignmentName
from doge_alignment.core.chart import (
        Chart,
        Entry,
        add_admin,
        empty_chart,
        place,
)


def chart_with(
        *users: tuple[str, AlignmentName], admins: tuple[str, ...] = ()
) -> Chart:
        """Return a chart with ``(user id, alignment)`` pairs on it."""
        chart = empty_chart()
        for user_id, alignment in users:
                chart = place(
                        chart, user_id, Entry(alignment, f"user{user_id}")
                )
        for admin in admins:
                chart = add_admin(chart, admin)
        return chart
