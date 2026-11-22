"""Plan retained seasons independently of watched status."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class SeasonPlan:
        """Keep shared choices and release unclaimed seasons."""

        keep: frozenset[int]
        remove: frozenset[int]
        protected: bool
        shared: frozenset[int]


def plan_seasons(
        users: dict[int, dict[str, Any]],
        tvdb: int,
        actor: int,
        selected: int,
        present: set[int],
        *,
        protected: bool,
) -> SeasonPlan:
        """Replace the actor's choice without dropping anyone else's season."""
        keep = {selected}
        shared: set[int] = set()
        for user_id, data in users.items():
                if int(user_id) == actor:
                        continue
                number = (data.get("tv_seasons") or {}).get(str(tvdb))
                if isinstance(number, int) and number > 0:
                        keep.add(number)
                        shared.add(number)
        remove = set() if protected else present - keep - {0}
        return SeasonPlan(
                frozenset(keep), frozenset(remove), protected, frozenset(shared)
        )


def complete_archive(episodes: list[dict[str, Any]], now: datetime) -> bool:
        """Protect shows complete at snapshot time."""
        aired = []
        for episode in episodes:
                if episode.get("seasonNumber", 0) <= 0:
                        continue
                value = episode.get("airDateUtc") or episode.get("airDate")
                if not value:
                        continue
                try:
                        date = datetime.fromisoformat(
                                value.replace("Z", "+00:00")
                        )
                except ValueError:
                        continue
                if date.tzinfo is None:
                        date = date.replace(tzinfo=UTC)
                if date <= now:
                        aired.append(episode)
        return bool(aired) and all(e.get("hasFile") for e in aired)
