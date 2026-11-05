"""How someone posts: when, how steadily, in what bursts, in what style.

Everything comes from what the guess already fetched: the sampled pages of
their history (25 consecutive messages each) and where those pages sit in
the whole history. A page at position ``p`` that starts at time ``t`` pins
the history down at that point, so two pages give the posting rate between
them, and all pages together sketch how it changed over the years, without
fetching anything else.

Hours and weekdays are in the time zone the front end passes (for now one
for everyone; per-person zones can come from a timezone cog later).
"""

import math
import re
import statistics
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, tzinfo

from .messages import Message, by_time

URL = re.compile(r"https?://\S+")
EMOJI = re.compile(r"<a?:\w+:\d+>|[\U0001F300-\U0001FAFF☀-➿]")
LETTERS = re.compile(r"[A-Za-z]")


@dataclass(frozen=True)
class Anchor:
        """A point in the history: position (0 = newest) and its time."""

        position: int
        time: datetime


@dataclass(frozen=True)
class Rate:
        """Messages per day over a stretch of time."""

        start: date
        end: date
        per_day: float


@dataclass(frozen=True)
class Activity:
        """A summary of someone's posting."""

        total: int
        joined: date | None
        rates: tuple[Rate, ...]
        hours: tuple[int, ...]
        weekend_share: float
        night_share: float
        burstiness: float
        median_burst: float
        average_length: float
        shares: dict[str, float]
        zone: str = "UTC"


def rates_between(anchors: Sequence[Anchor]) -> tuple[Rate, ...]:
        """Return the posting rate between consecutive anchors, oldest first.

        Positions count back from the newest message, so an older anchor has
        a larger position; the messages between two anchors are the
        difference in positions.
        """
        ordered = sorted(anchors, key=lambda a: a.time)
        return tuple(
                Rate(
                        older.time.date(),
                        newer.time.date(),
                        (older.position - newer.position)
                        / max(
                                (newer.time - older.time).total_seconds()
                                / 86400,
                                1,
                        ),
                )
                for older, newer in zip(ordered, ordered[1:], strict=False)
                if older.position > newer.position
        )


def variability(rates: Sequence[Rate]) -> float:
        """Coefficient of variation of the rates (0 = perfectly steady)."""
        values = [r.per_day for r in rates]
        if len(values) < 2:  # noqa: PLR2004
                return 0.0
        mean = statistics.fmean(values)
        return statistics.pstdev(values) / mean if mean else 0.0


def gaps_seconds(messages: Sequence[Message]) -> list[float]:
        """Seconds between consecutive messages in the same channel."""
        ordered = by_time(list(messages))
        return [
                (b.created - a.created).total_seconds()
                for a, b in zip(ordered, ordered[1:], strict=False)
                if a.channel_id == b.channel_id
        ]


def burstiness(gaps: Sequence[float]) -> float:
        """Goh & Barabási burstiness: -1 regular, 0 random, 1 bursty."""
        if len(gaps) < 2:  # noqa: PLR2004
                return 0.0
        mean = statistics.fmean(gaps)
        spread = statistics.pstdev(gaps)
        return (spread - mean) / (spread + mean) if spread + mean else 0.0


def median_burst(gaps: Sequence[float], within: float = 120) -> float:
        """Median number of messages sent back to back (within ``within`` s)."""
        runs: list[int] = []
        run = 1
        for gap in gaps:
                if gap <= within:
                        run += 1
                else:
                        runs.append(run)
                        run = 1
        runs.append(run)
        return statistics.median(runs)


def _share(
        messages: Sequence[Message], test: Callable[[Message], object]
) -> float:
        if not messages:
                return 0.0
        return sum(1 for m in messages if test(m)) / len(messages)


def _fraction(
        times: Sequence[datetime], test: Callable[[datetime], bool]
) -> float:
        return sum(1 for t in times if test(t)) / len(times) if times else 0.0


def zone_name(tz: tzinfo) -> str:
        """Name a time zone: its IANA key (America/Denver) or its name."""
        return str(getattr(tz, "key", None) or tz.tzname(None) or "UTC")


def _shouty(text: str) -> bool:
        letters = LETTERS.findall(text)
        return (
                len(letters) >= 5
                and sum(c.isupper() for c in letters) / len(letters) > 0.7
        )  # noqa: PLR2004


def style_shares(messages: Sequence[Message]) -> dict[str, float]:
        """Share of messages with links, attachments, replies, etc."""
        return {
                "links": _share(messages, lambda m: URL.search(m.content)),
                "attachments": _share(messages, lambda m: m.attachments > 0),
                "replies": _share(messages, lambda m: m.reply_to is not None),
                "mentions": _share(messages, lambda m: m.mentions > 0),
                "edited": _share(messages, lambda m: m.edited),
                "questions": _share(messages, lambda m: "?" in m.content),
                "exclamations": _share(messages, lambda m: "!" in m.content),
                "all caps": _share(messages, lambda m: _shouty(m.content)),
                "emoji": _share(messages, lambda m: EMOJI.search(m.content)),
        }


def activity(
        own: Sequence[Message],
        anchors: Sequence[Anchor],
        total: int,
        joined: date | None = None,
        tz: tzinfo = UTC,
) -> Activity:
        """Summarise ``own`` messages (the sampled pages) and the anchors.

        Hours, nights and weekends are counted in ``tz``.
        """
        local = [m.created.astimezone(tz) for m in own]
        hours = Counter(t.hour for t in local)
        gaps = gaps_seconds(own)
        lengths = [len(m.content) for m in own if m.content]
        return Activity(
                total=total,
                joined=joined,
                rates=rates_between(anchors),
                hours=tuple(hours.get(h, 0) for h in range(24)),
                weekend_share=_fraction(local, lambda t: t.weekday() >= 5),  # noqa: PLR2004
                night_share=_fraction(local, lambda t: t.hour < 6),  # noqa: PLR2004
                burstiness=burstiness(gaps),
                median_burst=median_burst(gaps) if own else 0,
                average_length=statistics.fmean(lengths) if lengths else 0.0,
                shares=style_shares(own),
                zone=zone_name(tz),
        )


def _steadiness(cv: float) -> str:
        if cv < 0.5:  # noqa: PLR2004
                return "steady"
        if cv < 1.0:
                return "uneven"
        return "erratic: long quiet spells and big spikes"


def _busiest_hours(hours: Sequence[int]) -> str:
        total = sum(hours)
        if not total:
                return "unknown"
        top = sorted(range(24), key=lambda h: -hours[h])[:3]
        return ", ".join(f"{h:02d}:00 ({hours[h] / total:.0%})" for h in top)


def _hour_spread(hours: Sequence[int]) -> str:
        total = sum(hours)
        if not total:
                return "unknown"
        entropy = -sum(
                c / total * math.log2(c / total) for c in hours if c
        ) / math.log2(24)
        if entropy > 0.85:  # noqa: PLR2004
                return "posts at all hours"
        if entropy > 0.65:  # noqa: PLR2004
                return "a loose daily rhythm"
        return "a tight daily routine"


def describe(a: Activity) -> str:
        """Return the summary as short lines for the model."""
        cv = variability(a.rates)
        rate_lines = [
                f"  {r.start:%Y-%m}..{r.end:%Y-%m}: {r.per_day:.1f}/day"
                for r in a.rates
        ]
        shares = ", ".join(
                f"{name} {share:.0%}" for name, share in a.shares.items()
        )
        lines = [
                f"Messages in this server: {a.total:,}",
                *([f"Member since: {a.joined:%Y-%m-%d}"] if a.joined else []),
                (
                        f"Posting rate over time ({_steadiness(cv)}, "
                        f"variation {cv:.2f}):"
                ),
                *(rate_lines or ["  not enough samples"]),
                (
                        f"Busiest hours ({a.zone}): {_busiest_hours(a.hours)}; "
                        f"{_hour_spread(a.hours)}"
                ),
                (
                        f"Late night (00-06 {a.zone}): {a.night_share:.0%}; "
                        f"weekends: {a.weekend_share:.0%}"
                ),
                (
                        f"Burstiness {a.burstiness:+.2f} (-1 clockwork, 0 "
                        f"random, +1 bursts); typically {a.median_burst:g} "
                        f"message{'' if a.median_burst == 1 else 's'} in a row"
                ),
                f"Average message length: {a.average_length:.0f} characters",
                f"Style: {shares}",
        ]
        return "\n".join(lines)
