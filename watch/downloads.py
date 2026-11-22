"""Safe user-facing ARR queue status, without release paths or identifiers."""

import math
import re
from typing import Any

import discord


def normalized_title(text: str) -> str:
        """Compare title/year queries despite punctuation differences."""
        return re.sub(r"[\W_]+", "", text.casefold())


def _progress(row: dict[str, Any]) -> str:
        """Handle unknown sizes and clamp inconsistent client byte counters."""
        size = row.get("size")
        left = row.get("sizeleft", row.get("sizeLeft"))
        if not isinstance(size, (int, float)) or not isinstance(
                left, (int, float)
        ):
                return "progress unavailable"
        if not math.isfinite(size) or not math.isfinite(left) or size <= 0:
                return "progress unavailable"
        percent = max(0, min(100, 100 * (1 - left / size)))
        return f"{percent:.0f}%"


def _eta(row: dict[str, Any]) -> str:
        """Accept only ARR durations, never arbitrary download-client text."""
        value = row.get("timeleft", row.get("timeLeft"))
        if not isinstance(value, str):
                return ""
        match = re.fullmatch(
                r"(?:(\d+)\.)?(\d{1,2}):(\d{2}):(\d{2})(?:\.\d+)?", value
        )
        if match is None:
                return ""
        days, hours, minutes, seconds = (int(v or 0) for v in match.groups())
        total = days * 86400 + hours * 3600 + minutes * 60 + seconds
        if total <= 0:
                return ""
        parts = []
        for unit, length in [("d", 86400), ("h", 3600), ("m", 60)]:
                count, total = divmod(total, length)
                if count:
                        parts.append(f"{count}{unit}")
        return " · ETA " + (" ".join(parts[:2]) if parts else "<1m")


def _state(row: dict[str, Any]) -> str:
        """Separate downloading from failed/stalled/import states."""
        status = str(row.get("status", "")).lower()
        tracked = str(row.get("trackedDownloadState", "")).lower()
        health = str(row.get("trackedDownloadStatus", "")).lower()
        if health in {"warning", "error"} or "failed" in tracked:
                return "Needs attention"
        if tracked == "importpending":
                return "Downloaded; awaiting import"
        if tracked == "importing":
                return "Importing into library"
        if tracked == "imported":
                return "Imported; waiting for Jellyfin"
        return {
                "paused": "Paused",
                "stalled": "Stalled",
                "downloadclientunavailable": "Download client unavailable",
                "queued": "Queued",
                "delay": "Queued",
                "warning": "Needs attention",
                "failed": "Needs attention",
                "completed": "Downloaded; awaiting import",
                "downloading": "Downloading",
        }.get(
                status,
                "Downloaded; awaiting import"
                if tracked == "importpending"
                else "Download in progress",
        )


def describe_downloads(
        kind: str,
        title: dict[str, Any],
        records: list[dict[str, Any]],
) -> str:
        """Describe matching transfers once each, even Sonarr season packs."""
        name = discord.utils.escape_markdown(str(title.get("title", "Title")))
        year = f" ({title['year']})" if title.get("year") else ""
        heading = f"Already requested: **{name}{year}**. "
        id_key = "movieId" if kind == "movie" else "seriesId"
        records = [r for r in records if r.get(id_key) == title["id"]]
        if not records:
                if kind == "movie" and title.get("hasFile"):
                        return heading + (
                                "Already downloaded; Jellyfin "
                                "may still be scanning the "
                                "import."
                        )
                count = title.get("statistics", {}).get("episodeFileCount", 0)
                available = (
                        f"{count} episodes available. "
                        if kind == "tv" and count
                        else ""
                )
                return (
                        heading
                        + available
                        + (
                                "No active download reported. Waiting "
                                "for a matching release or a future "
                                "search. No new download was started."
                        )
                )
        grouped: dict[str, list[dict[str, Any]]] = {}
        for index, row in enumerate(records):
                key = str(row.get("downloadId") or f"row:{index}")
                grouped.setdefault(key, []).append(row)
        noun = "download" if len(grouped) == 1 else "downloads"
        lines = [heading + f"{len(grouped)} {noun} in ARR:"]
        for group in list(grouped.values())[:5]:
                row = group[0]
                label = ""
                if kind == "tv":
                        episode = row.get("episode") or {}
                        season = row.get(
                                "seasonNumber", episode.get("seasonNumber")
                        )
                        number = episode.get("episodeNumber")
                        if isinstance(season, int):
                                label = (
                                        f"Season {season} pack"
                                        if len(group) > 1
                                        else f"S{season:02}"
                                )
                                if len(group) == 1 and isinstance(number, int):
                                        label += f"E{number:02}"
                                label += " — "
                state = _state(row)
                eta = _eta(row) if state == "Downloading" else ""
                lines.append(f"{label}{state} · {_progress(row)}{eta}")
        if len(grouped) > 5:  # noqa: PLR2004
                lines.append(f"…and {len(grouped) - 5} more downloads.")
        lines.append(
                "Progress is a snapshot. No duplicate download was started."
        )
        return "\n".join(lines)
