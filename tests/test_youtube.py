"""Test the YouTube fallback that fetches music the catalogue does not have."""

import json
from pathlib import Path

import pytest

from watch.youtube import Youtube, YoutubeError, youtube_url


def test_a_pasted_link_is_recognised_and_separated_from_the_song() -> None:
        """Catch accepting links for other sites or losing the song name."""
        assert (
                youtube_url("Halo https://youtu.be/dQw4w9WgXcQ")
                == "https://youtu.be/dQw4w9WgXcQ"
        )
        assert (
                youtube_url("Halo https://www.youtube.com/watch?v=dQw4w9WgXcQ.")
                == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
        )
        assert youtube_url("Halo https://example.com/watch") is None
        assert youtube_url("Just Halo") is None


async def test_ready_reports_a_missing_folder(tmp_path: Path) -> None:
        """Catch committing a song to a fallback that is not deployed."""
        youtube = Youtube(binary="yt-dlp", folder=tmp_path / "missing")
        assert await youtube.ready() == (
                "the YouTube music folder is not mounted"
        )
        assert (
                await Youtube(
                        binary="definitely-not-yt-dlp", folder=tmp_path
                ).ready()
                == "yt-dlp is not installed"
        )


async def test_search_reads_youtube_results_without_downloading(
        tmp_path: Path,
) -> None:
        """Catch a picker built from unchecked or missing yt-dlp output."""
        calls: list[list[str]] = []

        async def runner(
                args: list[str], _timeout: float
        ) -> tuple[int, str, str]:
                calls.append(args)
                return (
                        0,
                        json.dumps(
                                {
                                        "entries": [
                                                {
                                                        "id": "abc123",
                                                        "title": (
                                                                "Depeche Mode"
                                                                " - Halo"
                                                        ),
                                                        "uploader": (
                                                                "Depeche Mode"
                                                        ),
                                                        "duration": 271,
                                                }
                                        ]
                                }
                        ),
                        "",
                )

        youtube = Youtube(binary="yt-dlp", folder=tmp_path, runner=runner)
        results = await youtube.search("Halo Depeche Mode")
        assert calls[0][-1].startswith("ytsearch")
        assert results == [
                {
                        "url": "https://www.youtube.com/watch?v=abc123",
                        "title": "Depeche Mode - Halo",
                        "uploader": "Depeche Mode",
                        "duration": 271,
                }
        ]


async def test_download_writes_inside_the_music_folder(tmp_path: Path) -> None:
        """Catch accepting a path outside the library or another site."""
        seen: list[list[str]] = []
        target = tmp_path / "Depeche Mode" / "Halo.opus"

        async def runner(
                args: list[str], _timeout: float
        ) -> tuple[int, str, str]:
                seen.append(args)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(b"audio")
                return 0, str(target) + "\n", ""

        youtube = Youtube(binary="yt-dlp", folder=tmp_path, runner=runner)
        path = await youtube.download(
                "https://www.youtube.com/watch?v=abc123",
                "Depeche Mode",
                "Halo",
        )
        assert path == target
        assert "--no-playlist" in seen[0]
        assert str(tmp_path) in str(path)


async def test_download_refuses_links_from_other_sites(
        tmp_path: Path,
) -> None:
        """Catch yt-dlp being pointed at an arbitrary URL by a user."""
        youtube = Youtube(binary="yt-dlp", folder=tmp_path)
        with pytest.raises(YoutubeError, match="YouTube"):
                await youtube.download("https://example.com/x", "A", "B")


async def test_download_failure_and_escaping_output_are_reported(
        tmp_path: Path,
) -> None:
        """Catch treating a broken download or a foreign path as success."""

        async def broken(
                _args: list[str], _timeout: float
        ) -> tuple[int, str, str]:
                return 1, "", "ERROR: Video unavailable"

        youtube = Youtube(binary="yt-dlp", folder=tmp_path, runner=broken)
        with pytest.raises(YoutubeError, match="unavailable"):
                await youtube.download("https://youtu.be/dQw4w9WgXcQ", "A", "B")

        async def escaping(
                _args: list[str], _timeout: float
        ) -> tuple[int, str, str]:
                outside = tmp_path.parent / "escape.opus"
                outside.write_bytes(b"audio")
                return 0, str(outside), ""

        youtube = Youtube(binary="yt-dlp", folder=tmp_path, runner=escaping)
        with pytest.raises(YoutubeError, match="outside"):
                await youtube.download("https://youtu.be/dQw4w9WgXcQ", "A", "B")
