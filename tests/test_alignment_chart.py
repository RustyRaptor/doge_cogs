"""The chart's rules: immutable updates and permissions."""

import dataclasses

import pytest
from alignment_helpers import chart_with

from doge_alignment.core.chart import (
        Entry,
        add_admin,
        clear,
        empty_chart,
        forget_user,
        import_chart,
        make_chart,
        may_manage,
        may_remove,
        may_set,
        place,
        remove_admin,
        unplace,
        unplace_all,
        with_spec,
)
from doge_alignment.core.spec import ChartSpec, with_title


def test_place_returns_new_chart_and_keeps_admins() -> None:
        """Placing someone neither mutates nor drops the admin list."""
        before = chart_with(("1", "Lawful Good"), admins=("9",))
        after = place(before, "2", Entry("Chaotic Evil", "two"))
        assert set(before.users) == {"1"}
        assert set(after.users) == {"1", "2"}
        assert after.admins == ("9",)


def test_charts_are_immutable() -> None:
        """Neither the chart nor its user mapping can be changed in place."""
        chart = chart_with(("1", "Lawful Good"))
        with pytest.raises(dataclasses.FrozenInstanceError):
                chart.admins = ("x",)  # ty: ignore[invalid-assignment]
        with pytest.raises(TypeError):
                chart.users["2"] = Entry("True Neutral", "x")  # ty: ignore[invalid-assignment]


def test_unplace_keeps_admins_and_others() -> None:
        """Removing one person leaves everyone and everything else."""
        chart = chart_with(
                ("1", "Lawful Good"), ("2", "True Neutral"), admins=("1",)
        )
        after = unplace(chart, "1")
        assert set(after.users) == {"2"}
        assert after.admins == ("1",)
        assert unplace(after, "404") == after


def test_unplace_all_and_clear() -> None:
        """Bulk removal, and clearing, keep the admins."""
        chart = chart_with(
                ("1", "Lawful Good"),
                ("2", "True Neutral"),
                ("3", "Chaotic Evil"),
                admins=("5",),
        )
        assert set(unplace_all(chart, ("1", "3")).users) == {"2"}
        cleared = clear(chart)
        assert not cleared.users
        assert cleared.admins == ("5",)


def test_admins_are_unique_and_ordered() -> None:
        """Adding an admin twice keeps one entry; removing is idempotent."""
        chart = add_admin(add_admin(add_admin(empty_chart(), "a"), "b"), "a")
        assert chart.admins == ("a", "b")
        assert remove_admin(remove_admin(chart, "a"), "a").admins == ("b",)
        assert make_chart(admins=["x", "x", "y"]).admins == ("x", "y")


def test_forget_user_removes_every_trace() -> None:
        """Data deletion takes the user off the chart and the admin list."""
        chart = chart_with(("1", "Lawful Good"), ("2", "Lawful Evil"))
        after = forget_user(add_admin(chart, "1"), "1")
        assert set(after.users) == {"2"}
        assert after.admins == ()


def test_import_keeps_own_admins_and_takes_custom_labels() -> None:
        """Importing brings people and custom labels, not admins."""
        ours = chart_with(("1", "Lawful Good"), admins=("a",))
        theirs = chart_with(("2", "Chaotic Good"), admins=("b",))
        merged = import_chart(ours, theirs)
        assert set(merged.users) == {"2"}
        assert merged.admins == ("a",)
        assert merged.spec == ours.spec
        custom = with_spec(theirs, with_title(ChartSpec(), "Pizza"))
        assert import_chart(ours, custom).spec.title == "Pizza"


def test_may_set_rules() -> None:
        """Self and newcomers are free; moving others needs a manager."""
        chart = chart_with(("1", "Lawful Good"), admins=("9",))
        assert may_set(chart, "1", "1", privileged=False)
        assert may_set(chart, "2", "3", privileged=False)
        assert not may_set(chart, "2", "1", privileged=False)
        assert may_set(chart, "9", "1", privileged=False)
        assert may_set(chart, "2", "1", privileged=True)


def test_may_remove_and_manage() -> None:
        """Anyone may leave; removing others and managing need privileges."""
        chart = chart_with(("1", "Lawful Good"), admins=("9",))
        assert may_remove(chart, "1", "1", privileged=False)
        assert not may_remove(chart, "2", "1", privileged=False)
        assert may_remove(chart, "9", "1", privileged=False)
        assert may_manage(chart, "9", privileged=False)
        assert not may_manage(chart, "2", privileged=False)
        assert may_manage(chart, "2", privileged=True)
