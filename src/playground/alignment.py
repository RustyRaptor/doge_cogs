from __future__ import annotations

from io import BytesIO, StringIO
from pathlib import Path
from typing import Literal, TypedDict

import yaml

# Define all possible alignments (classic 3x3 chart)
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


# User entry in YAML
class UserAlignment(TypedDict):
    alignment: AlignmentName
    display_name: str
    avatar_url: str | None


class AlignmentChart(TypedDict):
    """Whole server alignment chart."""

    users: dict[str, UserAlignment]  # key: user_id as string


def parse_alignment_chart(data: BytesIO) -> AlignmentChart:
    """Parse YAML bytes into a Python dict."""
    data.seek(0)
    text = data.read().decode("utf-8") or ""
    loaded = yaml.safe_load(text)
    if not loaded:
        return {"users": {}}
    if "users" not in loaded:
        loaded["users"] = {}
    return loaded  # type: ignore


def serialize_alignment_chart(chart: AlignmentChart) -> BytesIO:
    """Convert Python dict into YAML BytesIO."""
    text_buf = StringIO()
    yaml.safe_dump(chart, text_buf, sort_keys=False)
    bytes_buf = BytesIO(text_buf.getvalue().encode("utf-8"))
    bytes_buf.seek(0)
    return bytes_buf


def set_user_alignment(
    chart: AlignmentChart,
    user_id: str,
    alignment: AlignmentName,
    display_name: str,
    avatar_url: str | None = None,
) -> AlignmentChart:
    """Return a new chart with updated alignment for a user."""
    new_chart = {"users": dict(chart["users"])}
    new_chart["users"][user_id] = {
        "alignment": alignment,
        "display_name": display_name,
        "avatar_url": avatar_url,
    }
    return new_chart  # type: ignore


def remove_user_alignment(chart: AlignmentChart, user_id: str) -> AlignmentChart:
    """Return a new chart with a user removed."""
    new_chart = {"users": dict(chart["users"])}
    new_chart["users"].pop(user_id, None)
    return new_chart  # type: ignore


def load_yaml_file(path: Path) -> BytesIO:
    """Load YAML file from disk into BytesIO. Creates empty if missing."""
    if not path.exists():
        return BytesIO()
    return BytesIO(path.read_bytes())


def save_yaml_file(path: Path, data: BytesIO) -> None:
    """Write YAML BytesIO back to disk."""
    path.write_bytes(data.getvalue())


if __name__ == "__main__":
    file_path = Path("server_12345.yaml")

    # Load from disk
    raw_data = load_yaml_file(file_path)

    # Parse
    chart = parse_alignment_chart(raw_data)

    # Modify (pure)
    updated_chart = set_user_alignment(
        chart,
        user_id="111222333",
        alignment="Chaotic Good",
        display_name="EpicUser",
        avatar_url="https://cdn.discordapp.com/avatars/111222333/avatar.png",
    )
    print(updated_chart, chart, raw_data)
    # Serialize (pure)
    new_bytes = serialize_alignment_chart(updated_chart)

    # Save (impure)
    save_yaml_file(file_path, new_bytes)
