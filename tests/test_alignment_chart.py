"""The alignment chart's rules, as pure functions."""

import pytest

from doge_alignment.chart import (
        ALIGNMENTS,
        AlignmentChart,
        add_admin,
        as_alignment,
        empty_chart,
        forget_user,
        format_chart,
        may_manage_admins,
        may_set,
        normalize_chart,
        remove_admin,
        remove_user_alignment,
        set_user_alignment,
)


def chart_with(
        *users: tuple[str, str], admins: tuple[str, ...] = ()
) -> AlignmentChart:
        """Return a chart with ``(user id, alignment)`` pairs on it."""
        chart = empty_chart()
        for user_id, alignment in users:
                chart = set_user_alignment(
                        chart,
                        user_id,
                        as_alignment(alignment),
                        f"user{user_id}",
                )
        for admin in admins:
                chart = add_admin(chart, admin)
        return chart


def test_nine_alignments_in_chart_order() -> None:
        """The chart is the classic 3x3, good row first."""
        assert len(ALIGNMENTS) == 9
        assert ALIGNMENTS[0] == "Lawful Good"
        assert ALIGNMENTS[4] == "True Neutral"
        assert ALIGNMENTS[-1] == "Chaotic Evil"


def test_set_keeps_admins_and_original() -> None:
        """Regression: setting an alignment used to drop the admin list."""
        chart = chart_with(admins=("1",))
        updated = set_user_alignment(chart, "2", "Lawful Good", "Tester")
        assert updated["admins"] == ["1"]
        assert updated["users"]["2"]["alignment"] == "Lawful Good"
        assert chart["users"] == {}


def test_remove_keeps_admins_and_original() -> None:
        """Regression: removing an alignment used to drop the admin list."""
        chart = chart_with(("2", "Chaotic Evil"), admins=("1",))
        updated = remove_user_alignment(chart, "2")
        assert updated == {"users": {}, "admins": ["1"]}
        assert "2" in chart["users"]


def test_admins_are_unique_and_removable() -> None:
        """Adding twice keeps one entry; removing a non-admin is harmless."""
        chart = add_admin(add_admin(empty_chart(), "1"), "1")
        assert chart["admins"] == ["1"]
        assert remove_admin(chart, "1")["admins"] == []
        assert remove_admin(chart, "9")["admins"] == ["1"]


def test_forget_user_removes_everything() -> None:
        """Data deletion takes the user off the chart and the admin list."""
        chart = chart_with(
                ("1", "True Neutral"), ("2", "Lawful Good"), admins=("1",)
        )
        assert forget_user(chart, "1") == {
                "users": {"2": chart["users"]["2"]},
                "admins": [],
        }


@pytest.mark.parametrize(
        ("invoker", "target", "privileged", "allowed"),
        [
                ("1", "1", False, True),  # yourself, even if already set
                ("1", "3", False, True),  # someone not on the chart yet
                ("1", "2", False, False),  # someone already on it
                ("9", "2", False, True),  # ...unless you're an alignment admin
                ("1", "2", True, True),  # ...or the bot owner
        ],
)
def test_may_set(
        invoker: str, target: str, *, privileged: bool, allowed: bool
) -> None:
        """Only admins can move people who are already on the chart."""
        chart = chart_with(
                ("1", "Lawful Good"), ("2", "Chaotic Evil"), admins=("9",)
        )
        assert may_set(chart, invoker, target, privileged=privileged) is allowed


def test_may_manage_admins() -> None:
        """Admins and privileged users manage admins; nobody else."""
        chart = chart_with(admins=("9",))
        assert may_manage_admins(chart, "9", privileged=False)
        assert may_manage_admins(chart, "1", privileged=True)
        assert not may_manage_admins(chart, "1", privileged=False)


def test_format_chart_in_chart_order() -> None:
        """Rows follow the chart, names are sorted, empty cells are skipped."""
        chart = chart_with(
                ("2", "Chaotic Evil"),
                ("1", "Lawful Good"),
                ("3", "Lawful Good"),
        )
        assert format_chart(chart) == (
                "**Lawful Good**: user1, user3\n**Chaotic Evil**: user2"
        )
        with pytest.raises(ValueError, match="empty"):
                format_chart(empty_chart())


def test_normalize_chart_repairs_stored_data() -> None:
        """Missing keys, int ids and unknown alignments are cleaned up."""
        assert normalize_chart(None) == empty_chart()
        assert normalize_chart({"users": None}) == empty_chart()
        chart = normalize_chart(
                {
                        "users": {
                                1: {
                                        "alignment": "Lawful Good",
                                        "display_name": "a",
                                        "avatar_url": None,
                                },
                                2: {"alignment": "Lawful Awesome"},
                        },
                        "admins": [5],
                }
        )
        assert list(chart["users"]) == ["1"]
        assert chart["admins"] == ["5"]


def test_as_alignment_rejects_others() -> None:
        """Only the nine alignments are accepted."""
        assert as_alignment("True Neutral") == "True Neutral"
        with pytest.raises(ValueError, match="not an alignment"):
                as_alignment("Lawful Awesome")
