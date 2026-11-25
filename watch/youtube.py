"""Fetch a reviewed song from YouTube when MusicBrainz has no artist."""

import asyncio
import json
import os
import re
import shutil
from collections.abc import Awaitable, Callable
from pathlib import Path
from urllib.parse import urlsplit

LINK = re.compile(r"https?://\S+")
YOUTUBE_HOSTS = {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtu.be",
}


class YoutubeError(Exception):
        """A search or download the user can be told about."""


def is_youtube(url: str) -> bool:
        """Accept only YouTube links, never arbitrary downloader input."""
        parts = urlsplit(url)
        return parts.scheme == "https" and parts.netloc in YOUTUBE_HOSTS


def youtube_url(text: str) -> str | None:
        """Return a pasted YouTube link without its trailing punctuation."""
        for match in LINK.finditer(text):
                url = match[0].rstrip(".,;:)]}")
                if is_youtube(url):
                        return url
        return None


def _safe(value: str) -> str:
        """Keep one path component for the artist and title folders."""
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", value).strip(" .")
        return (cleaned or "Unknown")[:120]


def _inside(path: Path, folder: Path) -> bool:
        """Reject output that escaped the configured music folder."""
        try:
                path.resolve().relative_to(folder.resolve())
        except ValueError:
                return False
        return True


def _reason(stderr: str) -> str:
        """Keep yt-dlp's last error line without dumping its whole log."""
        lines = [line.strip() for line in stderr.splitlines() if line.strip()]
        if not lines:
                return "yt-dlp failed."
        return lines[-1].removeprefix("ERROR: ")[:300]


async def _run(args: list[str], limit: float) -> tuple[int, str, str]:
        """Run yt-dlp without a shell and bound how long it may take."""
        process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
        )
        try:
                stdout, stderr = await asyncio.wait_for(
                        process.communicate(), limit
                )
        except TimeoutError:
                process.kill()
                await process.wait()
                msg = "yt-dlp timed out."
                raise YoutubeError(msg) from None
        return (
                process.returncode or 0,
                stdout.decode(errors="replace"),
                stderr.decode(errors="replace"),
        )


Runner = Callable[[list[str], float], Awaitable[tuple[int, str, str]]]


class Youtube:
        """Search and download audio into the Jellyfin music folder."""

        def __init__(
                self,
                *,
                binary: str = "yt-dlp",
                folder: str | os.PathLike[str] = "/music/YouTube",
                runner: Runner | None = None,
        ) -> None:
                """Use the deployed yt-dlp unless a test injects a runner."""
                self.binary = binary
                self.folder = Path(folder)
                self.runner = runner or _run

        async def ready(self) -> str | None:
                """Name what is missing before a track is committed to it."""
                if not await asyncio.to_thread(self.folder.is_dir):
                        return "the YouTube music folder is not mounted"
                found = await asyncio.to_thread(shutil.which, self.binary)
                if found is None:
                        return "yt-dlp is not installed"
                return None

        async def search(
                self, query: str, limit: int = 5
        ) -> list[dict[str, object]]:
                """List candidate videos without downloading anything."""
                code, stdout, stderr = await self.runner(
                        [
                                self.binary,
                                "--dump-single-json",
                                "--flat-playlist",
                                f"ytsearch{limit}:{query}",
                        ],
                        120,
                )
                if code:
                        raise YoutubeError(_reason(stderr))
                try:
                        data = json.loads(stdout)
                except ValueError:
                        msg = "YouTube search returned no usable results."
                        raise YoutubeError(msg) from None
                results = []
                for entry in data.get("entries") or []:
                        video = str(entry.get("id") or "")
                        if not video:
                                continue
                        results.append(
                                {
                                        "url": str(
                                                entry.get("webpage_url")
                                                or "https://www.youtube.com/"
                                                f"watch?v={video}"
                                        ),
                                        "title": str(
                                                entry.get("title") or "Untitled"
                                        ),
                                        "uploader": str(
                                                entry.get("uploader")
                                                or entry.get("channel")
                                                or "unknown channel"
                                        ),
                                        "duration": entry.get("duration") or 0,
                                }
                        )
                return results[:limit]

        async def download(self, url: str, artist: str, title: str) -> Path:
                """Download one video's audio as a Jellyfin-readable file."""
                if not is_youtube(url):
                        msg = "Use a YouTube link for this fallback."
                        raise YoutubeError(msg)
                template = (
                        self.folder
                        / _safe(artist)
                        / (f"{_safe(title)}.%(ext)s")
                )
                code, stdout, stderr = await self.runner(
                        [
                                self.binary,
                                "--no-playlist",
                                "--extract-audio",
                                "--audio-format",
                                "opus",
                                "--audio-quality",
                                "0",
                                "--no-progress",
                                "--print",
                                "after_move:filepath",
                                "--output",
                                str(template),
                                url,
                        ],
                        900,
                )
                if code:
                        raise YoutubeError(_reason(stderr))
                lines = [line.strip() for line in stdout.splitlines()]
                lines = [line for line in lines if line]
                if not lines:
                        msg = "yt-dlp did not report a downloaded file."
                        raise YoutubeError(msg)
                path = Path(lines[-1])
                if not _inside(path, self.folder):
                        msg = "yt-dlp wrote outside the music folder."
                        raise YoutubeError(msg)
                if not await asyncio.to_thread(path.is_file):
                        msg = "The downloaded audio file is missing."
                        raise YoutubeError(msg)
                return path
