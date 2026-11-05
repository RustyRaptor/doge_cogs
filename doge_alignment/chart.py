"""The alignment chart: who sits where, and who may change it.

Pure functions on plain dicts (stored as-is in Red's Config), so they can be
tested without a bot. Every change returns a new chart and leaves the old one
alone.
"""

from typing import Literal, TypedDict, cast, get_args

AlignmentName = Literal[
        "Lawful Good",
        "Neutral Good",
        "Chaotic Good",
        "Lawful Neutral",
        "True Neutral",
        "Chaotic Neutral",
        "Lawful Evil",
        "Neutral Evil",
        "Chaotic Evil",
]

# The classic 3x3 chart, row by row: good, neutral, evil
ALIGNMENTS: tuple[AlignmentName, ...] = get_args(AlignmentName)


class UserAlignment(TypedDict):
        """One user's place on the chart."""

        alignment: AlignmentName
        display_name: str
        avatar_url: str | None


class AlignmentChart(TypedDict):
        """A server's chart. User ids are strings (Config keys must be)."""

        users: dict[str, UserAlignment]
        admins: list[str]


def empty_chart() -> AlignmentChart:
        """Return a chart with nobody on it."""
        return {"users": {}, "admins": []}


def is_alignment(value: str) -> bool:
        """Whether ``value`` is one of the nine alignments."""
        return value in ALIGNMENTS


def as_alignment(value: str) -> AlignmentName:
        """Return ``value`` as an alignment.

        Raises:
                ValueError: if it isn't one of the nine.

        """
        if not is_alignment(value):
                msg = f"{value!r} is not an alignment"
                raise ValueError(msg)
        return cast("AlignmentName", value)


def normalize_chart(data: object) -> AlignmentChart:
        """Return a well-formed chart from whatever was stored."""
        if not isinstance(data, dict):
                return empty_chart()
        users = data.get("users")
        admins = data.get("admins")
        return {
                "users": {
                        str(uid): entry
                        for uid, entry in (users or {}).items()
                        if isinstance(entry, dict)
                        and is_alignment(entry.get("alignment", ""))
                },
                "admins": [str(a) for a in (admins or [])],
        }


def set_user_alignment(
        chart: AlignmentChart,
        user_id: str,
        alignment: AlignmentName,
        display_name: str,
        avatar_url: str | None = None,
) -> AlignmentChart:
        """Return a new chart with ``user_id`` at ``alignment``."""
        users = dict(chart["users"])
        users[user_id] = {
                "alignment": alignment,
                "display_name": display_name,
                "avatar_url": avatar_url,
        }
        return {"users": users, "admins": list(chart["admins"])}


def remove_user_alignment(
        chart: AlignmentChart, user_id: str
) -> AlignmentChart:
        """Return a new chart without ``user_id``."""
        users = {k: v for k, v in chart["users"].items() if k != user_id}
        return {"users": users, "admins": list(chart["admins"])}


def add_admin(chart: AlignmentChart, user_id: str) -> AlignmentChart:
        """Return a new chart where ``user_id`` is an alignment admin."""
        admins = list(chart["admins"])
        if user_id not in admins:
                admins.append(user_id)
        return {"users": dict(chart["users"]), "admins": admins}


def remove_admin(chart: AlignmentChart, user_id: str) -> AlignmentChart:
        """Return a new chart where ``user_id`` is not an alignment admin."""
        admins = [a for a in chart["admins"] if a != user_id]
        return {"users": dict(chart["users"]), "admins": admins}


def forget_user(chart: AlignmentChart, user_id: str) -> AlignmentChart:
        """Return a new chart with no trace of ``user_id`` (data deletion)."""
        return remove_admin(remove_user_alignment(chart, user_id), user_id)


def may_set(
        chart: AlignmentChart,
        invoker_id: str,
        target_id: str,
        *,
        privileged: bool,
) -> bool:
        """Whether ``invoker_id`` may place ``target_id`` on the chart.

        Anyone may set their own alignment and place someone who isn't on
        the chart yet; only alignment admins (or ``privileged`` users, such
        as the bot owner) may move someone who already is.
        """
        if invoker_id == target_id or target_id not in chart["users"]:
                return True
        return privileged or invoker_id in chart["admins"]


def may_manage_admins(
        chart: AlignmentChart, invoker_id: str, *, privileged: bool
) -> bool:
        """Whether ``invoker_id`` may add or remove alignment admins.

        ``privileged`` covers the server owner and the bot owner.
        """
        return privileged or invoker_id in chart["admins"]


def format_chart(chart: AlignmentChart) -> str:
        """Return the chart as text, in chart order, with empty cells left out.

        Raises:
                ValueError: for an empty chart; check ``chart["users"]`` first.

        """
        if not chart["users"]:
                msg = "the chart is empty"
                raise ValueError(msg)
        lines: list[str] = []
        for alignment in ALIGNMENTS:
                names = sorted(
                        (
                                entry["display_name"]
                                for entry in chart["users"].values()
                                if entry["alignment"] == alignment
                        ),
                        key=str.casefold,
                )
                if names:
                        lines.append(f"**{alignment}**: {', '.join(names)}")
        return "\n".join(lines)
