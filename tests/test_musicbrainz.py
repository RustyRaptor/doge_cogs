"""MusicBrainz resolves a track to the release groups that contain it."""

from typing import Any, cast

GROUP_SINGLE = {
        "id": "rg-single",
        "title": "Wake Me Up",
        "primary-type": "Single",
        "secondary-types": [],
        "first-release-date": "2013-06-25",
}

GROUP_COMP = {
        "id": "rg-comp",
        "title": "MuchDance 2014",
        "primary-type": "Album",
        "secondary-types": ["Compilation"],
}


def _release(rel_id: str, title: str, group: dict, tracks: list) -> dict:
        """Build one release the way a recording search nests it."""
        return {
                "id": rel_id,
                "title": title,
                "release-group": group,
                "media": [{"track": [{"title": t} for t in tracks]}],
        }


PAYLOAD = {
        "count": 1,
        "recordings": [
                {
                        "id": "rec-1",
                        "title": "Wake Me Up",
                        "artist-credit": [{"name": "Avicii"}],
                        "releases": [
                                _release(
                                        "rel-1",
                                        "Wake Me Up",
                                        GROUP_SINGLE,
                                        ["Wake Me Up"],
                                ),
                                _release(
                                        "rel-2",
                                        "MuchDance 2014",
                                        GROUP_COMP,
                                        ["Wake Me Up (radio edit)"],
                                ),
                        ],
                }
        ],
}


def test_a_query_binds_the_track_title_and_the_artist() -> None:
        """Unbounded searches return hundreds of compilations."""
        from watch.musicbrainz import build_query

        query = build_query("Avicii", 'Wake Me Up "remix"')
        assert 'recording:"Wake Me Up \\"remix\\""' in query["query"]
        assert 'artist:"Avicii"' in query["query"]
        assert query["fmt"] == "json"


def test_recordings_become_one_row_per_release_group() -> None:
        """Keep the release group, its type and the track titles it holds."""
        from watch.musicbrainz import parse_recordings

        groups = parse_recordings(PAYLOAD)
        assert [g["foreignAlbumId"] for g in groups] == ["rg-single", "rg-comp"]
        single = groups[0]
        assert single["title"] == "Wake Me Up"
        assert single["albumType"] == "Single"
        assert single["secondaryTypes"] == []
        assert single["releaseDate"] == "2013-06-25"
        assert single["artist"] == {"artistName": "Avicii"}
        assert single["tracks"] == ["Wake Me Up"]
        assert groups[1]["tracks"] == ["Wake Me Up (radio edit)"]


def test_release_groups_keep_the_shapes_the_ranker_expects() -> None:
        """Hand the parser's rows to choose_album unchanged."""
        from watch.music import choose_album
        from watch.musicbrainz import parse_recordings

        groups = parse_recordings(PAYLOAD)
        assert choose_album({"title": "Wake Me Up", "artist": "Avicii"}, groups)


def test_a_track_title_match_ignores_case_and_punctuation() -> None:
        """MusicBrainz varies presentation between releases."""
        from watch.musicbrainz import holds_track

        groups = [
                {"foreignAlbumId": "rg", "tracks": ["Wake Me Up (Radio Edit)"]}
        ]
        assert holds_track(groups, "rg", "wake me up radio edit")
        assert not holds_track(groups, "rg", "Hey Brother")
        assert not holds_track(groups, "missing", "Wake Me Up")


def test_only_release_groups_credited_to_the_artist_are_kept() -> None:
        """Billboard compilations are never the target of a track request."""
        from watch.musicbrainz import credited_groups, parse_recordings

        groups = parse_recordings(PAYLOAD)
        groups[1]["artist"] = {"artistName": "Various Artists"}
        kept = credited_groups(groups, "Avicii")
        assert [g["foreignAlbumId"] for g in kept] == ["rg-single"]


def test_a_featured_artist_credit_still_counts() -> None:
        """Spotify credits collaborators in the artist string."""
        from watch.musicbrainz import credited_groups

        groups = [
                {
                        "foreignAlbumId": "rg",
                        "artist": {"artistName": "Avicii"},
                }
        ]
        assert credited_groups(groups, "Avicii, Aloe Blacc")


class _Reply:
        """Minimal response for the client seam."""

        def __init__(
                self, status_code: int, payload: dict | None = None
        ) -> None:
                self.status_code = status_code
                self._payload = payload

        def json(self) -> Any:  # noqa: ANN401 (test double)
                return self._payload


class _Client:
        """Scripted transport that records calls."""

        def __init__(self, replies: list[_Reply]) -> None:
                self.replies = replies
                self.calls: list[dict[str, Any]] = []

        async def get(self, url: str, **kwargs: Any) -> _Reply:  # noqa: ANN401
                self.calls.append({"url": url, **kwargs})
                return self.replies.pop(0)


async def test_rate_limit_is_retried_then_succeeds() -> None:
        """MusicBrainz answers 503 when the client is too fast."""
        from watch.musicbrainz import MusicBrainz

        client = _Client([_Reply(503), _Reply(200, PAYLOAD)])
        delays: list[float] = []

        async def sleep(seconds: float) -> None:
                delays.append(seconds)

        mb = MusicBrainz(cast("Any", client), sleep=sleep, clock=lambda: 0.0)
        groups = await mb.release_groups("Avicii", "Wake Me Up")
        assert [g["foreignAlbumId"] for g in groups] == ["rg-single", "rg-comp"]
        assert len(client.calls) == 2
        assert delays and delays[0] >= 1.0


async def test_repeated_failure_degrades_to_no_groups() -> None:
        """A MusicBrainz outage must not break the import."""
        from watch.musicbrainz import MusicBrainz

        client = _Client([_Reply(503), _Reply(503), _Reply(503), _Reply(503)])

        async def sleep(_seconds: float) -> None:
                return None

        mb = MusicBrainz(cast("Any", client), sleep=sleep, clock=lambda: 0.0)
        assert await mb.release_groups("Avicii", "Wake Me Up") == []


async def test_calls_are_spaced_by_one_second() -> None:
        """One request per second is the hard limit."""
        from watch.musicbrainz import MusicBrainz

        client = _Client([_Reply(200, PAYLOAD), _Reply(200, PAYLOAD)])
        now = {"t": 0.0}
        delays: list[float] = []

        async def sleep(seconds: float) -> None:
                delays.append(seconds)
                now["t"] += seconds

        mb = MusicBrainz(
                cast("Any", client), sleep=sleep, clock=lambda: now["t"]
        )
        await mb.release_groups("Avicii", "Wake Me Up")
        await mb.release_groups("Avicii", "Hey Brother")
        assert delays == [1.0]
