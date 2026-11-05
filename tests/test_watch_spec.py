"""Parsing what to watch, and describing preload progress."""

from datetime import UTC, datetime

import pytest
from media_preload_client.models import (
        FileStatus,
        PreloadFile,
        PreloadRequest,
        RequestStatus,
)

from watch.spec import (
        EpisodeSpec,
        SpecError,
        bar,
        compact_ranges,
        describe,
        human_size,
        is_finished,
        parse_episodes,
        parse_season,
        progress,
        split_query,
)


@pytest.mark.parametrize(
        ("query", "title", "spec"),
        [
                ("The Matrix", "The Matrix", None),
                (
                        "Dark Matter s2e8-10",
                        "Dark Matter",
                        EpisodeSpec(2, (8, 9, 10)),
                ),
                ("Dark Matter S2E8", "Dark Matter", EpisodeSpec(2, (8,))),
                (
                        "Dark Matter s2 e8-e10",
                        "Dark Matter",
                        EpisodeSpec(2, (8, 9, 10)),
                ),
                (
                        "Dark Matter s2e1,3,5",
                        "Dark Matter",
                        EpisodeSpec(2, (1, 3, 5)),
                ),
                ("Dark Matter s2", "Dark Matter", EpisodeSpec(season=2)),
                ("Dark Matter season 2", "Dark Matter", EpisodeSpec(season=2)),
                ("Dark Matter next", "Dark Matter", EpisodeSpec(next=3)),
                ("Dark Matter NEXT 5", "Dark Matter", EpisodeSpec(next=5)),
                ("  Frieren   s1e3 ", "Frieren", EpisodeSpec(1, (3,))),
                # Titles that merely contain the words stay titles
                ("Next Friday", "Next Friday", None),
                ("Seasons", "Seasons", None),
        ],
)
def test_split_query(query: str, title: str, spec: EpisodeSpec | None) -> None:
        """The title and the episode spec are split apart."""
        assert split_query(query) == (title, spec)


@pytest.mark.parametrize(
        "query",
        ["Show s2e10-8", "Show s0", "Show s0e1", "Show s1e0", "Show next 99"],
)
def test_bad_specs_are_rejected(query: str) -> None:
        """Backwards ranges, season or episode 0, and huge "next" are errors."""
        with pytest.raises(SpecError):
                split_query(query)


def test_parse_episodes_merges_and_sorts() -> None:
        """Overlapping ranges collapse into one sorted list."""
        assert parse_episodes("10, 8-9, 9") == (8, 9, 10)


def test_parse_episodes_limits_size() -> None:
        """A single request can't ask for more than 50 episodes."""
        with pytest.raises(SpecError):
                parse_episodes("1-51")


def test_parse_season() -> None:
        """Seasons are positive numbers."""
        assert parse_season(" 3 ") == 3
        for bad in ("0", "x", ""):
                with pytest.raises(SpecError):
                        parse_season(bad)


def test_describe_spec() -> None:
        """Specs read naturally in messages."""
        assert EpisodeSpec(2, (8, 9, 10, 12)).describe() == "S2 E8-10,12"
        assert EpisodeSpec(season=2).describe() == "all of season 2"
        assert EpisodeSpec(next=3).describe() == "the next 3 unwatched episodes"


def test_compact_ranges() -> None:
        """Runs of episodes become ranges."""
        assert compact_ranges((1, 2, 3, 5, 7, 8)) == "1-3,5,7-8"
        assert compact_ranges(()) == ""


@pytest.mark.parametrize(
        ("size", "text"),
        [(0, "0 B"), (999, "999 B"), (1500, "1.5 KB"), (844_000_000, "844 MB")],
)
def test_human_size(size: int, text: str) -> None:
        """Sizes use decimal units like the rest of the home lab."""
        assert human_size(size) == text


def test_bar() -> None:
        """Bars fill proportionally and handle empty totals."""
        assert bar(1, 2, width=4) == "▓▓░░ 50%"
        assert bar(0, 0, width=4) == "▓▓▓▓ 100%"


def make_request(status: RequestStatus) -> PreloadRequest:
        """Dark Matter S2E8-10 as homelab's end-to-end test left it."""
        files = [
                PreloadFile(
                        id=1,
                        path="/media2/Videos/tvshows/Dark Matter/S02E08.mkv",
                        title="Dark Matter S02E08",
                        status=FileStatus.CACHED,
                        size_bytes=800_000_000,
                ),
                PreloadFile(
                        id=2,
                        path="/media2/Videos/tvshows/Dark Matter/S02E10.mkv",
                        title="Dark Matter S02E10",
                        status=FileStatus.FETCHING,
                        size_bytes=844_000_000,
                        fetched_bytes=422_000_000,
                ),
                PreloadFile(
                        id=3,
                        path="/media/elsewhere.mkv",
                        title="Elsewhere",
                        status=FileStatus.NOT_CACHEABLE,
                        size_bytes=5,
                ),
        ]
        return PreloadRequest(
                id=7,
                item_id="abc",
                title="Dark Matter",
                requested_by="discord:doge",
                status=status,
                created_at=datetime(2026, 11, 5, tzinfo=UTC),
                files=files,
        )


def test_progress_ignores_uncacheable_files() -> None:
        """Totals count cached and fetching files only."""
        assert progress(make_request(RequestStatus.FETCHING)) == (
                1_222_000_000,
                1_644_000_000,
        )


def test_describe_request() -> None:
        """Each file gets a line, plus an overall bar."""
        text = describe(make_request(RequestStatus.FETCHING))
        assert "✅ Dark Matter S02E08 — 800 MB" in text
        assert "⬇️ Dark Matter S02E10 — ▓▓▓▓░░░░ 50%" in text
        assert "🚫 Elsewhere" in text
        assert text.endswith("74% · 1.2 GB of 1.6 GB")


def test_describe_truncates_long_requests() -> None:
        """Long seasons don't overflow the embed."""
        request = make_request(RequestStatus.QUEUED)
        request.files = request.files[:1] * 20
        assert "…and 5 more" in describe(request)


def test_is_finished() -> None:
        """Polling stops once nothing will change."""
        assert not is_finished(make_request(RequestStatus.FETCHING))
        assert is_finished(make_request(RequestStatus.PARTIAL))
