"""Charts as plain data (for Red's Config or JSON) and as YAML files.

The YAML format is the one the cog saved before it moved to Config::

    users:
      '111222333':
        alignment: Chaotic Good
        display_name: EpicUser
        avatar_url: https://cdn.discordapp.com/avatars/...
    admins: ['444555666']
"""

from collections.abc import Mapping
from typing import Any

import yaml

from .alignments import parse_alignment
from .chart import Chart, Entry, make_chart

Data = dict[str, Any]


class ChartFormatError(ValueError):
        """A YAML chart that can't be read."""


def entry_to_data(entry: Entry) -> Data:
        """Return ``entry`` as a plain dict."""
        return {
                "alignment": entry.alignment,
                "display_name": entry.display_name,
                "avatar_url": entry.avatar_url,
        }


def to_data(chart: Chart) -> Data:
        """Return ``chart`` as plain dicts and lists."""
        return {
                "users": {
                        uid: entry_to_data(entry)
                        for uid, entry in chart.users.items()
                },
                "admins": list(chart.admins),
        }


def entry_from_data(user_id: str, data: object) -> Entry | None:
        """Return the entry in ``data``, or None if it isn't one."""
        if not isinstance(data, Mapping):
                return None
        alignment = parse_alignment(str(data.get("alignment", "")))
        if alignment is None:
                return None
        name = data.get("display_name")
        url = data.get("avatar_url")
        return Entry(
                alignment=alignment,
                display_name=str(name) if name else user_id,
                avatar_url=str(url) if url else None,
        )


def _mapping(data: object, key: str) -> Mapping[object, object]:
        value = data.get(key) if isinstance(data, Mapping) else None
        return value if isinstance(value, Mapping) else {}


def _list(data: object, key: str) -> list[object]:
        value = data.get(key) if isinstance(data, Mapping) else None
        return list(value) if isinstance(value, list | tuple) else []


def from_data(data: object) -> Chart:
        """Return the chart in ``data``, skipping anything malformed."""
        entries = {
                str(uid): entry_from_data(str(uid), raw)
                for uid, raw in _mapping(data, "users").items()
        }
        return make_chart(
                {uid: e for uid, e in entries.items() if e is not None},
                (str(a) for a in _list(data, "admins")),
        )


def to_yaml(chart: Chart) -> str:
        """Return ``chart`` as YAML."""
        return yaml.safe_dump(
                to_data(chart), sort_keys=False, allow_unicode=True
        )


def from_yaml(text: str) -> Chart:
        """Read a YAML chart, strictly.

        Raises:
                ChartFormatError: for invalid YAML, a missing ``users``
                        mapping, or any entry without a valid alignment.

        """
        try:
                data = yaml.safe_load(text)
        except yaml.YAMLError as error:
                msg = f"not valid YAML: {error}"
                raise ChartFormatError(msg) from error
        if not isinstance(data, Mapping) or not isinstance(
                data.get("users"), Mapping
        ):
                msg = "expected a mapping with a 'users' mapping in it"
                raise ChartFormatError(msg)
        bad = sorted(
                str(uid)
                for uid, raw in data["users"].items()
                if entry_from_data(str(uid), raw) is None
        )
        if bad:
                msg = f"no valid alignment for user(s) {', '.join(bad)}"
                raise ChartFormatError(msg)
        return from_data(data)
