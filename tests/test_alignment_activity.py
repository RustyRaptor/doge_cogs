"""The activity profile: rates, rhythm, bursts and style."""

from datetime import UTC, datetime, timedelta

from alignment_messages import START, msg

from doge_alignment.core.activity import (
        Anchor,
        activity,
        burstiness,
        describe,
        median_burst,
        rates_between,
        variability,
)


def test_rates_between_pages() -> None:
        """Messages between two pages over the days between them."""
        day = timedelta(days=1)
        rates = rates_between(
                [
                        Anchor(0, START),
                        Anchor(100, START - 10 * day),
                        Anchor(400, START - 40 * day),
                ]
        )
        assert [round(r.per_day, 2) for r in rates] == [10.0, 10.0]
        assert variability(rates) == 0
        uneven = rates_between(
                [
                        Anchor(0, START),
                        Anchor(10, START - 10 * day),
                        Anchor(1010, START - 20 * day),
                ]
        )
        assert variability(uneven) > 0.9  # noqa: PLR2004


def test_burstiness() -> None:
        """Regular gaps are negative, clumps are positive."""
        assert burstiness([60] * 10) == -1
        assert burstiness([1] * 30 + [10_000]) > 0.5  # noqa: PLR2004
        assert burstiness([5]) == 0


def test_median_burst() -> None:
        """Runs of messages within two minutes."""
        assert median_burst([10, 10, 500, 10, 500]) == 2
        assert median_burst([]) == 1


def test_activity_and_describe() -> None:
        """Hours, nights, style shares, and readable lines."""
        own = [
                msg(1, minutes=0, content="HELLO EVERYONE!!"),
                msg(2, minutes=1, content="check https://x.y <:e:1>"),
                msg(3, minutes=600, content="why?"),
        ]
        a = activity(own, [Anchor(0, START)], total=3, joined=START.date())
        assert a.hours[12] == 2
        assert a.shares["all caps"] == 1 / 3
        assert a.shares["links"] == 1 / 3
        assert a.shares["questions"] == 1 / 3
        text = describe(a)
        assert "Messages in this server: 3" in text
        assert "Member since: 2024-01-01" in text
        assert "not enough samples" in text
        assert "message in a row" in text or "messages in a row" in text


def test_night_and_weekend_shares() -> None:
        """UTC night is 00-06; weekends are Saturday and Sunday."""
        saturday_night = datetime(2024, 1, 6, 2, 0, tzinfo=UTC)
        offset = (saturday_night - START).total_seconds() / 60
        a = activity([msg(1, minutes=offset), msg(2)], [], total=2)
        assert a.night_share == 0.5  # noqa: PLR2004
        assert a.weekend_share == 0.5  # noqa: PLR2004
