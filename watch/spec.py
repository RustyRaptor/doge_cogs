"""Parse what a user wants to watch and describe a preload's progress.

Kept apart from the Discord code so it can be tested without a bot.
"""

import re
from dataclasses import dataclass

from media_preload_client.models import (
        FileStatus,
        PreloadFile,
        PreloadRequest,
        RequestStatus,
)
from media_preload_client.types import Unset

MAX_EPISODES = 50
DEFAULT_NEXT = 3

FINISHED = frozenset(
        {
                RequestStatus.DONE,
                RequestStatus.PARTIAL,
                RequestStatus.FAILED,
                RequestStatus.CANCELED,
        }
)

FILE_ICONS: dict[FileStatus, str] = {
        FileStatus.QUEUED: "⏳",
        FileStatus.FETCHING: "⬇️",
        FileStatus.CACHED: "✅",
        FileStatus.FAILED: "❌",
        FileStatus.CANCELED: "⏹️",
        FileStatus.NOT_CACHEABLE: "🚫",
}


class SpecError(ValueError):
        """The episode part of a request can't be understood."""


@dataclass(frozen=True)
class EpisodeSpec:
        """Which episodes of a series to preload.

        Exactly one form is used: a whole ``season``, some ``episodes`` of a
        season, or the ``next`` N unwatched episodes.
        """

        season: int | None = None
        episodes: tuple[int, ...] = ()
        next: int | None = None

        def describe(self) -> str:
                """Return a short human description, e.g. ``S2 E8-10``."""
                if self.next is not None:
                        return f"the next {self.next} unwatched episodes"
                if not self.episodes:
                        return f"all of season {self.season}"
                return f"S{self.season} E{compact_ranges(self.episodes)}"


# Trailing episode specs, tried in order against the end of the query
_SEASON_EPISODES = re.compile(
        r"\s+s(?:eason\s*)?(\d{1,2})\s*e(?:p(?:isodes?)?\s*)?"
        r"(\d{1,3}(?:\s*-\s*e?\d{1,3})?(?:\s*,\s*\d{1,3}(?:\s*-\s*\d{1,3})?)*)$",
        re.IGNORECASE,
)
_SEASON = re.compile(r"\s+s(?:eason\s*)?(\d{1,2})$", re.IGNORECASE)
_NEXT = re.compile(r"\s+next(?:\s+(\d{1,2}))?$", re.IGNORECASE)


def parse_episodes(text: str) -> tuple[int, ...]:
        """Turn ``"8-10,12"`` into ``(8, 9, 10, 12)``.

        Raises:
                SpecError: for backwards ranges, episode 0 or too many episodes.

        """
        numbers: set[int] = set()
        for part in text.replace(" ", "").lower().replace("e", "").split(","):
                if "-" in part:
                        start_text, end_text = part.split("-", 1)
                        start, end = int(start_text), int(end_text)
                        if end < start:
                                msg = f"episode range {part} goes backwards"
                                raise SpecError(msg)
                        numbers.update(range(start, end + 1))
                else:
                        numbers.add(int(part))
        if 0 in numbers:
                msg = "episodes start at 1"
                raise SpecError(msg)
        if len(numbers) > MAX_EPISODES:
                msg = f"that's more than {MAX_EPISODES} episodes"
                raise SpecError(msg)
        return tuple(sorted(numbers))


def parse_season(text: str) -> int:
        """Return the season number in ``text``.

        Raises:
                SpecError: if it isn't a number from 1 up.

        """
        try:
                season = int(text.strip())
        except ValueError:
                msg = f"{text!r} isn't a season number"
                raise SpecError(msg) from None
        if season < 1:
                msg = "seasons start at 1"
                raise SpecError(msg)
        return season


def split_query(text: str) -> tuple[str, EpisodeSpec | None]:
        """Split ``"Dark Matter s2e8-10"`` into a title and an episode spec.

        Understands ``s2``, ``season 2``, ``s2e8``, ``s2e8-10``, ``s2e1,3,5``
        and ``next`` or ``next 5`` at the end of the query.

        Raises:
                SpecError: if the spec is malformed.

        """
        text = " ".join(text.split())
        padded = f" {text}"
        if match := _SEASON_EPISODES.search(padded):
                season = int(match.group(1))
                spec = EpisodeSpec(season, parse_episodes(match.group(2)))
        elif match := _SEASON.search(padded):
                spec = EpisodeSpec(season=int(match.group(1)))
        elif match := _NEXT.search(padded):
                count = int(match.group(1) or DEFAULT_NEXT)
                if not 1 <= count <= MAX_EPISODES:
                        msg = f"next takes 1 to {MAX_EPISODES} episodes"
                        raise SpecError(msg)
                spec = EpisodeSpec(next=count)
        else:
                return text, None
        if spec.season == 0:
                msg = "seasons start at 1"
                raise SpecError(msg)
        return padded[: match.start()].strip(), spec


def compact_ranges(numbers: tuple[int, ...]) -> str:
        """Turn ``(8, 9, 10, 12)`` back into ``"8-10,12"``."""
        nums = sorted(set(numbers))
        parts: list[str] = []
        i = 0
        while i < len(nums):
                j = i
                while j + 1 < len(nums) and nums[j + 1] == nums[j] + 1:
                        j += 1
                parts.append(str(nums[i]) if i == j else f"{nums[i]}-{nums[j]}")
                i = j + 1
        return ",".join(parts)


def human_size(size: int) -> str:
        """Return ``size`` bytes as e.g. ``844 MB`` or ``1.2 GB``."""
        value = float(size)
        unit = "B"
        for unit in ("B", "KB", "MB", "GB", "TB"):
                if value < 1000 or unit == "TB":
                        break
                value /= 1000
        if unit == "B" or value >= 100:
                return f"{value:.0f} {unit}"
        return f"{value:.1f} {unit}"


def _fetched(file: PreloadFile) -> int:
        if file.status == FileStatus.CACHED:
                return file.size_bytes
        fetched = file.fetched_bytes
        return 0 if isinstance(fetched, Unset) else fetched


def progress(request: PreloadRequest) -> tuple[int, int]:
        """Return (bytes fetched, bytes total) over the cacheable files."""
        files = [
                f
                for f in request.files
                if f.status
                not in (FileStatus.NOT_CACHEABLE, FileStatus.CANCELED)
        ]
        return sum(_fetched(f) for f in files), sum(f.size_bytes for f in files)


def bar(done: int, total: int, width: int = 16) -> str:
        """Return a text progress bar such as ``▓▓▓▓░░░░ 50%``."""
        fraction = 1.0 if total <= 0 else min(done / total, 1.0)
        filled = round(fraction * width)
        return f"{'▓' * filled}{'░' * (width - filled)} {fraction:.0%}"


def file_line(file: PreloadFile) -> str:
        """Return one line describing a file's state."""
        icon = FILE_ICONS.get(file.status, "•")
        line = f"{icon} {file.title}"
        if file.status == FileStatus.FETCHING:
                line += f" — {bar(_fetched(file), file.size_bytes, width=8)}"
        elif file.status == FileStatus.CACHED:
                line += f" — {human_size(file.size_bytes)}"
        elif file.status == FileStatus.FAILED and isinstance(file.error, str):
                line += f" — {file.error[:80]}"
        return line


def describe(request: PreloadRequest, max_files: int = 15) -> str:
        """Return the progress text shown in Discord for ``request``."""
        lines = [file_line(f) for f in request.files[:max_files]]
        if len(request.files) > max_files:
                lines.append(f"…and {len(request.files) - max_files} more")
        done, total = progress(request)
        sizes = f"{human_size(done)} of {human_size(total)}"
        lines += ["", f"{bar(done, total)} · {sizes}"]
        return "\n".join(lines)


def is_finished(request: PreloadRequest) -> bool:
        """Whether the request will not change any more."""
        return request.status in FINISHED
