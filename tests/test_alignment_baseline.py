"""The server baseline: regulars' numbers, comparison, culture sample."""

import random
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from alignment_messages import START, msg

from doge_alignment.core.baseline import (
        CULTURE_PROMPT,
        MAX_TURNS,
        build_baseline,
        compare,
        culture_body,
        culture_evidence,
        describe_baseline,
        from_data,
        is_stale,
        percentile,
        standing,
        time_points,
        to_data,
        traits,
)
from doge_alignment.core.guess import ModelConfig, estimate_tokens
from doge_alignment.core.messages import snowflake_at, snowflake_time

DENVER = ZoneInfo("America/Denver")


def chatter() -> list:
        """Two regulars (12 messages each), one passer-by, one bot."""
        quiet = [msg(i, "quiet", i * 10, "ok.") for i in range(12)]
        loud = [
                msg(100 + i, "loud", i * 10 + 1, "WHAT IS HAPPENING!!! lol")
                for i in range(12)
        ]
        once = [msg(200, "once", 5, "hi")]
        bot = [msg(300 + i, "bot", i, "beep", bot=True) for i in range(20)]
        return [*quiet, *loud, *once, *bot]


def test_traits_counts_style_and_local_nights() -> None:
        """Night is midnight to 6 in the zone given, not UTC."""
        # 12:00 UTC is 05:00 in Denver
        early = traits([msg(1, content="hey?")], DENVER)
        assert early.values["late night"] == 1.0
        assert traits([msg(1, content="hey?")]).values["late night"] == 0.0
        assert early.values["questions"] == 1.0
        assert early.values["length"] == 4


def test_build_baseline_keeps_regulars_and_shares() -> None:
        """Only people with enough sampled messages count as regulars."""
        built = datetime(2026, 1, 1, tzinfo=UTC)
        base = build_baseline(chatter(), 10_000, built=built, minimum=8)
        assert base.people == 3  # bots left out
        assert base.sampled == 25
        assert len(base.regulars) == 2
        assert base.shares == (12 / 25, 12 / 25, 1 / 25)
        assert sum(base.hours) == 25


def test_percentile_and_standing() -> None:
        """Ties count half; words follow the percentile."""
        assert percentile(5, [1, 2, 3, 4]) == 1.0
        assert percentile(1, [1, 2, 3, 4]) == 0.125
        assert percentile(1, []) == 0.5
        assert standing(0.95) == "far more than most here"
        assert standing(0.5) == "typical here"
        assert standing(0.05) == "far less than most here"


def test_compare_sets_person_against_regulars() -> None:
        """Each line gives value, typical value and standing."""
        built = datetime(2026, 1, 1, tzinfo=UTC)
        base = build_baseline(chatter(), 10_000, built=built, minimum=8)
        shouter = traits([msg(i, "x", i, "STOP SHOUTING!!!") for i in range(5)])
        text = compare(shouter, 2_000, base)
        assert "Share of all messages here: 20.0%" in text
        assert "Messages in all caps: 100% vs typical 50%" in text
        assert "of 2 regulars" in text


def test_describe_baseline_mentions_culture() -> None:
        """The server profile has the sample, hours and culture."""
        built = datetime(2026, 1, 1, tzinfo=UTC)
        base = build_baseline(
                chatter(), 10_000, built=built, culture="Mostly memes."
        )
        text = describe_baseline(base)
        assert "Messages in this server: 10,000" in text
        assert "Culture: Mostly memes." in text
        assert "Busiest hours (UTC)" in text


def test_culture_evidence_is_anonymous() -> None:
        """Everyone is a letter; bots and monologues are left out."""
        group = [
                msg(1, "11", 0, "hello there"),
                msg(2, "22", 1, "hey <@11>"),
                msg(3, "bot", 2, "beep", bot=True),
                msg(4, "carol", 600, "talking to myself"),
        ]
        items = culture_evidence([group])
        assert len(items) == 1
        text = items[0].text
        assert "11" not in text.split("\n", 1)[1]
        assert "beep" not in text
        assert "A: hello there" in text
        assert "B: hey @A" in text


def test_culture_evidence_cuts_long_conversations() -> None:
        """At most MAX_TURNS turns of one conversation are quoted."""
        group = [
                msg(i, "ab"[i % 2], i, f"line {i}")
                for i in range(MAX_TURNS + 10)
        ]
        (item,) = culture_evidence([group])
        assert len(item.text.splitlines()) == MAX_TURNS + 1  # plus header


def test_culture_body_fits_budget() -> None:
        """The request packs conversations into the budget."""
        group = [msg(i, "ab"[i % 2], i * 30, "x" * 500) for i in range(40)]
        items = culture_evidence([group])
        body = culture_body(ModelConfig("http://m"), items, budget=2_000)
        messages = body["messages"]
        assert isinstance(messages, list)
        assert messages[0]["content"] == CULTURE_PROMPT
        assert estimate_tokens(messages[1]["content"]) < 2_000


def test_time_points_newest_first_in_range() -> None:
        """Moments are random, in range and sorted newest first."""
        end = START + timedelta(days=100)
        points = time_points(START, end, 10, random.Random(1))
        assert len(points) == 10
        assert list(points) == sorted(points, reverse=True)
        assert all(START <= p <= end for p in points)


def test_snowflake_at_round_trips() -> None:
        """An id made for a time decodes to that time (to the ms)."""
        when = datetime(2024, 5, 6, 7, 8, 9, 123000, tzinfo=UTC)
        assert snowflake_time(snowflake_at(when)) == when


def test_storage_round_trip_and_staleness() -> None:
        """Stored data reads back the same; junk reads as None."""
        built = datetime(2026, 1, 1, tzinfo=UTC)
        base = build_baseline(chatter(), 10_000, built=built, culture="c")
        assert from_data(to_data(base)) == base
        assert from_data({}) is None
        assert from_data({**to_data(base), "regulars": "nope"}) is None
        assert not is_stale(base, built + timedelta(days=6))
        assert is_stale(base, built + timedelta(days=8))
