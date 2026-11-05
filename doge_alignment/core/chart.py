"""A server's chart: who sits where, and who may change it.

Charts are immutable. Every operation returns a new chart and leaves its
argument alone, so they compose and are trivial to test.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from .alignments import AlignmentName


@dataclass(frozen=True)
class Entry:
        """One person's place on the chart.

        ``display_name`` and ``avatar_url`` are a snapshot from when the
        alignment was set. Front ends show live ones where they can, and fall
        back to these for people who have left.
        """

        alignment: AlignmentName
        display_name: str
        avatar_url: str | None = None


def _frozen(users: Mapping[str, Entry]) -> Mapping[str, Entry]:
        return MappingProxyType(dict(users))


@dataclass(frozen=True)
class Chart:
        """A chart. User ids are strings, as Red's Config needs."""

        users: Mapping[str, Entry] = field(default_factory=lambda: _frozen({}))
        admins: tuple[str, ...] = ()


def make_chart(
        users: Mapping[str, Entry] | None = None,
        admins: Iterable[str] = (),
) -> Chart:
        """Return a chart, with ``admins`` de-duplicated in order."""
        return Chart(
                users=_frozen(users or {}),
                admins=tuple(dict.fromkeys(admins)),
        )


def empty_chart() -> Chart:
        """Return a chart with nobody on it."""
        return make_chart()


def _with_users(chart: Chart, users: Mapping[str, Entry]) -> Chart:
        return replace(chart, users=_frozen(users))


def place(chart: Chart, user_id: str, entry: Entry) -> Chart:
        """Return ``chart`` with ``user_id`` at ``entry``."""
        return _with_users(chart, {**chart.users, user_id: entry})


def unplace(chart: Chart, user_id: str) -> Chart:
        """Return ``chart`` without ``user_id`` on it (admins unchanged)."""
        return _with_users(
                chart,
                {k: v for k, v in chart.users.items() if k != user_id},
        )


def unplace_all(chart: Chart, user_ids: Iterable[str]) -> Chart:
        """Return ``chart`` without any of ``user_ids`` on it."""
        gone = frozenset(user_ids)
        return _with_users(
                chart,
                {k: v for k, v in chart.users.items() if k not in gone},
        )


def clear(chart: Chart) -> Chart:
        """Return ``chart`` with nobody on it, keeping the admins."""
        return _with_users(chart, {})


def replace_users(chart: Chart, other: Chart) -> Chart:
        """Return ``chart`` with ``other``'s people and its own admins."""
        return _with_users(chart, other.users)


def add_admin(chart: Chart, user_id: str) -> Chart:
        """Return ``chart`` where ``user_id`` is an alignment admin."""
        return make_chart(chart.users, (*chart.admins, user_id))


def remove_admin(chart: Chart, user_id: str) -> Chart:
        """Return ``chart`` where ``user_id`` is not an alignment admin."""
        return replace(
                chart, admins=tuple(a for a in chart.admins if a != user_id)
        )


def forget_user(chart: Chart, user_id: str) -> Chart:
        """Return ``chart`` with no trace of ``user_id`` (data deletion)."""
        return remove_admin(unplace(chart, user_id), user_id)


def is_admin(chart: Chart, user_id: str) -> bool:
        """Whether ``user_id`` is on the chart's admin list."""
        return user_id in chart.admins


def may_manage(chart: Chart, invoker_id: str, *, privileged: bool) -> bool:
        """Whether ``invoker_id`` may change anyone and anything on the chart.

        ``privileged`` is the front end's call: on Discord, the server and bot
        owners, administrators, and Red's admin and mod roles.
        """
        return privileged or is_admin(chart, invoker_id)


def may_set(
        chart: Chart,
        invoker_id: str,
        target_id: str,
        *,
        privileged: bool,
) -> bool:
        """Whether ``invoker_id`` may place ``target_id`` on the chart.

        Anyone may set their own alignment and place someone who isn't on
        the chart yet; only chart managers may move someone who already is.
        """
        return (
                invoker_id == target_id
                or target_id not in chart.users
                or may_manage(chart, invoker_id, privileged=privileged)
        )


def may_remove(
        chart: Chart,
        invoker_id: str,
        target_id: str,
        *,
        privileged: bool,
) -> bool:
        """Whether ``invoker_id`` may take ``target_id`` off the chart."""
        return invoker_id == target_id or may_manage(
                chart, invoker_id, privileged=privileged
        )
