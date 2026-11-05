"""Charts to and from Config data and YAML."""

import pytest
from alignment_helpers import chart_with

from doge_alignment.core.chart import Entry, empty_chart, place
from doge_alignment.core.storage import (
        ChartFormatError,
        from_data,
        from_yaml,
        to_data,
        to_yaml,
)

# The format the cog saved before it moved to Config
OLD_YAML = """
users:
  '111222333':
    alignment: Chaotic Good
    display_name: EpicUser
    avatar_url: https://cdn.discordapp.com/avatars/111222333/avatar.png
"""


def test_data_round_trip() -> None:
        """What goes into Config comes back out the same."""
        chart = place(
                chart_with(("1", "Lawful Good"), admins=("1", "2")),
                "3",
                Entry("Neutral Evil", "three", "https://example.com/a.png"),
        )
        assert from_data(to_data(chart)) == chart


def test_from_data_skips_malformed() -> None:
        """Bad entries are dropped; ids become strings."""
        chart = from_data(
                {
                        "users": {
                                1: {"alignment": "cg", "display_name": "a"},
                                "2": {"alignment": "nope"},
                                "3": "not a dict",
                                "4": {"alignment": "Lawful Evil"},
                        },
                        "admins": [5],
                }
        )
        assert set(chart.users) == {"1", "4"}
        assert chart.users["1"].alignment == "Chaotic Good"
        assert chart.users["4"].display_name == "4"
        assert chart.admins == ("5",)
        assert from_data(None) == empty_chart()
        assert from_data({"users": [], "admins": "x"}) == empty_chart()


def test_yaml_round_trip() -> None:
        """Export then import gives the same chart."""
        chart = chart_with(
                ("1", "Lawful Good"), ("2", "True Neutral"), admins=("1",)
        )
        assert from_yaml(to_yaml(chart)) == chart


def test_reads_the_old_yaml_format() -> None:
        """Files from the old cog still import."""
        chart = from_yaml(OLD_YAML)
        entry = chart.users["111222333"]
        assert entry.alignment == "Chaotic Good"
        assert entry.display_name == "EpicUser"
        assert chart.admins == ()


@pytest.mark.parametrize(
        ("text", "message"),
        [
                ("users: [", "not valid YAML"),
                ("- a list", "'users' mapping"),
                ("admins: []", "'users' mapping"),
                ("users:\n  '1': {alignment: bad}", "user\\(s\\) 1"),
        ],
)
def test_from_yaml_is_strict(text: str, message: str) -> None:
        """Imports fail loudly instead of silently dropping people."""
        with pytest.raises(ChartFormatError, match=message):
                from_yaml(text)
