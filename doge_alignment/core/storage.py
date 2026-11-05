"""Charts as plain data (for Red's Config or JSON) and as YAML files.

The YAML format is the one the cog saved before it moved to Config::

    users:
      '111222333':
        alignment: Chaotic Good
        display_name: EpicUser
        avatar_url: https://cdn.discordapp.com/avatars/...
    admins: ['444555666']
    chart:                  # only for custom charts
      title: Pizza alignment
      columns: [Purist, Neutral, Rebel]
      rows: [Good, Neutral, Evil]
      labels: {True Neutral: Margherita}
      colors: {Chaotic Evil: '#aa2200'}

Config also keeps pending suggestions, which exports leave out.
"""

from collections.abc import Callable, Mapping
from functools import partial, reduce
from typing import Any

import yaml

from .alignments import AlignmentName, parse_alignment
from .chart import Chart, Entry, Suggestions, make_chart
from .spec import (
        ChartSpec,
        SpecError,
        is_custom,
        with_color,
        with_columns,
        with_label,
        with_rows,
        with_title,
)

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


def spec_to_data(spec: ChartSpec) -> Data:
        """Return ``spec`` as plain dicts and lists."""
        return {
                "title": spec.title,
                "columns": list(spec.columns),
                "rows": list(spec.rows),
                "labels": dict(spec.labels),
                "colors": dict(spec.colors),
        }


def to_data(chart: Chart) -> Data:
        """Return ``chart`` as plain dicts and lists."""
        return {
                "users": {
                        uid: entry_to_data(entry)
                        for uid, entry in chart.users.items()
                },
                "admins": list(chart.admins),
                "suggestions": {
                        target: dict(by)
                        for target, by in chart.suggestions.items()
                },
                "spec": spec_to_data(chart.spec),
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


SpecChange = Callable[[ChartSpec], ChartSpec]


def _try(spec: ChartSpec, change: SpecChange) -> ChartSpec:
        """Apply ``change``, or keep ``spec`` if the stored value is bad."""
        try:
                return change(spec)
        except SpecError:
                return spec


def _spec_changes(data: Mapping[object, object]) -> list[SpecChange]:
        title = data.get("title")
        columns = [str(c) for c in _list(data, "columns")]
        rows = [str(r) for r in _list(data, "rows")]
        labels = [
                (cell, str(text))
                for key, text in _mapping(data, "labels").items()
                if (cell := parse_alignment(str(key))) is not None
        ]
        colors = [
                (cell, str(color))
                for key, color in _mapping(data, "colors").items()
                if (cell := parse_alignment(str(key))) is not None
        ]
        return [
                *(
                        [partial(_title, title=title)]
                        if isinstance(title, str)
                        else []
                ),
                *([partial(_columns, names=columns)] if columns else []),
                *([partial(_rows, names=rows)] if rows else []),
                *(partial(_label, cell=c, text=t) for c, t in labels),
                *(partial(_color, cell=c, color=v) for c, v in colors),
        ]


def _title(spec: ChartSpec, *, title: str) -> ChartSpec:
        return with_title(spec, title)


def _columns(spec: ChartSpec, *, names: list[str]) -> ChartSpec:
        return with_columns(spec, *names)


def _rows(spec: ChartSpec, *, names: list[str]) -> ChartSpec:
        return with_rows(spec, *names)


def _label(spec: ChartSpec, *, cell: AlignmentName, text: str) -> ChartSpec:
        return with_label(spec, cell, text)


def _color(spec: ChartSpec, *, cell: AlignmentName, color: str) -> ChartSpec:
        return with_color(spec, cell, color)


def spec_from_data(data: object) -> ChartSpec:
        """Return the spec in ``data``, keeping defaults for bad parts."""
        if not isinstance(data, Mapping):
                return ChartSpec()
        return reduce(_try, _spec_changes(data), ChartSpec())


def suggestions_from_data(data: object) -> Suggestions:
        """Return the pending suggestions in ``data``, skipping bad ones."""
        return {
                str(target): {
                        str(suggester): cell
                        for suggester, raw in by.items()
                        if (cell := parse_alignment(str(raw))) is not None
                }
                for target, by in _mapping(data, "suggestions").items()
                if isinstance(by, Mapping)
        }


def from_data(data: object) -> Chart:
        """Return the chart in ``data``, skipping anything malformed."""
        entries = {
                str(uid): entry_from_data(str(uid), raw)
                for uid, raw in _mapping(data, "users").items()
        }
        return make_chart(
                {uid: e for uid, e in entries.items() if e is not None},
                (str(a) for a in _list(data, "admins")),
                suggestions_from_data(data),
                spec_from_data(
                        data.get("spec") if isinstance(data, Mapping) else None
                ),
        )


def to_yaml(chart: Chart) -> str:
        """Return ``chart`` as YAML (without pending suggestions)."""
        data = to_data(chart)
        exported: Data = {"users": data["users"], "admins": data["admins"]}
        if is_custom(chart.spec):
                exported["chart"] = data["spec"]
        return yaml.safe_dump(exported, sort_keys=False, allow_unicode=True)


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
        return from_data({**data, "suggestions": {}, "spec": data.get("chart")})
