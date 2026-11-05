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


def test_custom_spec_round_trips_and_exports() -> None:
        """Custom labels survive Config and YAML; classic ones stay out."""
        from doge_alignment.core.chart import with_spec  # noqa: PLC0415
        from doge_alignment.core.spec import (  # noqa: PLC0415
                ChartSpec,
                with_color,
                with_columns,
                with_label,
                with_title,
        )

        spec = with_color(
                with_label(
                        with_columns(
                                with_title(ChartSpec(), "Pizza"),
                                "Purist",
                                "Neutral",
                                "Rebel",
                        ),
                        "True Neutral",
                        "Margherita",
                ),
                "Chaotic Evil",
                "#aa2200",
        )
        chart = with_spec(chart_with(("1", "True Neutral")), spec)
        assert from_data(to_data(chart)) == chart
        text = to_yaml(chart)
        assert "chart:" in text
        assert from_yaml(text).spec == spec
        assert "chart:" not in to_yaml(chart_with(("1", "True Neutral")))


def test_bad_stored_spec_parts_fall_back() -> None:
        """Broken spec pieces in Config don't break the chart."""
        chart = from_data(
                {
                        "users": {},
                        "spec": {
                                "title": "",
                                "columns": ["a", "b"],
                                "rows": ["Up", "Mid", "Down"],
                                "labels": {"nope": "x", "cg": "Rebel"},
                                "colors": {"lg": "red", "le": "#00ff00"},
                        },
                }
        )
        assert chart.spec.title == "Alignment chart"
        assert chart.spec.columns == ("Lawful", "Neutral", "Chaotic")
        assert chart.spec.rows == ("Up", "Mid", "Down")
        assert dict(chart.spec.labels) == {"Chaotic Good": "Rebel"}
        assert dict(chart.spec.colors) == {"Lawful Evil": "#00ff00"}
