"""MusicBrainz containment and resolution inside the album importer."""

from typing import Any, cast


def album_row(
        album_id: int,
        title: str,
        kind: str,
        mbid: str,
        artist: str = "Avicii",
) -> dict[str, Any]:
        """Build a catalog album resource."""
        return {
                "id": album_id,
                "title": title,
                "foreignAlbumId": mbid,
                "albumType": kind,
                "secondaryTypes": [],
                "monitored": True,
                "artistId": 673,
                "artist": {
                        "artistName": artist,
                        "foreignArtistId": "artist-mbid",
                },
        }


def group_row(
        mbid: str, title: str, kind: str, tracks: list[str]
) -> dict[str, Any]:
        """Build a MusicBrainz release group as the client returns it."""
        return {
                "foreignAlbumId": mbid,
                "title": title,
                "albumType": kind,
                "secondaryTypes": [],
                "releaseDate": "2013-06-25",
                "artist": {"artistName": "Avicii"},
                "tracks": tracks,
        }


class FakeBrainz:
        """Stand in for the MusicBrainz client."""

        def __init__(
                self, groups: list[dict[str, Any]], *, broken: bool = False
        ) -> None:
                """Script the answers, or fail on demand."""
                self.groups = groups
                self.broken = broken
                self.asked: list[tuple[str, str]] = []

        async def release_groups(
                self, artist: str, title: str
        ) -> list[dict[str, Any]]:
                """Answer with the scripted release groups."""
                self.asked.append((artist, title))
                if self.broken:
                        msg = "MusicBrainz is unreachable"
                        raise RuntimeError(msg)
                return self.groups


class FakeArr:
        """Answer Lidarr reads and record writes."""

        def __init__(
                self,
                lookup: list[dict[str, Any]],
                catalog: dict[str, dict[str, Any]] | None = None,
        ) -> None:
                """Hold the search answer and the catalog lookup table."""
                self.lookup = lookup
                self.catalog = catalog or {}
                self.added: list[dict[str, Any]] = []
                self.searches: list[list[int]] = []

        async def __call__(self, path: str, **kwargs: Any) -> Any:  # noqa: ANN401
                """Serve the handful of routes the importer uses."""
                params = kwargs.get("params") or {}
                body = cast("dict[str, Any] | None", kwargs.get("body"))
                method = kwargs.get("method") or ("POST" if body else "GET")
                if path == "album/lookup":
                        return self.lookup
                if path == "album" and method == "GET":
                        mbid = params.get("foreignAlbumId")
                        row = self.catalog.get(mbid) if mbid else None
                        return [row] if row else []
                if path == "album" and method == "POST":
                        assert body is not None
                        self.added.append(body)
                        row = dict(body)
                        row["id"] = row.get("id") or (900 + len(self.added))
                        row["artistId"] = 673
                        return row
                if path == "artist/lookup":
                        return [{"artistName": "Avicii"}]
                if path == "artist":
                        return []
                if path.startswith("artist/"):
                        return {
                                "id": 673,
                                "artistName": "Avicii",
                                "foreignArtistId": "artist-mbid",
                                "monitored": True,
                                "monitorNewItems": "none",
                        }
                if path == "rootfolder":
                        return [
                                {
                                        "path": "/arr/library/Music",
                                        "defaultQualityProfileId": 1,
                                        "defaultMetadataProfileId": 1,
                                }
                        ]
                if path == "command":
                        assert body is not None
                        self.searches.append(body["albumIds"])
                        return {"id": 1}
                return {}


async def engine_with(arr: FakeArr, brainz: Any | None) -> Any:  # noqa: ANN401
        """Build an importer with optional MusicBrainz."""
        from watch.music import MusicImporter

        async def save(_job: dict[str, Any]) -> None:
                return None

        async def authorize() -> None:
                return None

        return MusicImporter(
                arr,
                cast("Any", None),
                cast("Any", None),
                save,
                authorize,
                musicbrainz=brainz,
        )


async def test_the_group_holding_the_track_beats_a_better_type() -> None:
        """A plain album that lacks the track is the wrong target."""
        rows = [
                album_row(1, "Wake Me Up", "Album", "rg-album"),
                album_row(2, "Wake Me Up", "Single", "rg-single"),
        ]
        arr = FakeArr(rows)
        brainz = FakeBrainz(
                [group_row("rg-single", "Wake Me Up", "Single", ["Wake Me Up"])]
        )
        engine = await engine_with(arr, brainz)

        album_id, reason = await engine._album(
                {"artist_checks": {}},
                {"title": "Wake Me Up", "artist": "Avicii"},
        )

        assert reason == ""
        assert album_id == 2
        assert arr.added[0]["foreignAlbumId"] == "rg-single"


async def test_without_musicbrainz_the_type_ranking_decides() -> None:
        """No client means no new behaviour."""
        rows = [
                album_row(1, "Wake Me Up", "Album", "rg-album"),
                album_row(2, "Wake Me Up", "Single", "rg-single"),
        ]
        arr = FakeArr(rows)
        engine = await engine_with(arr, None)

        await engine._album(
                {"artist_checks": {}},
                {"title": "Wake Me Up", "artist": "Avicii"},
        )

        assert arr.added[0]["foreignAlbumId"] == "rg-album"


async def test_a_dead_end_is_resolved_from_the_catalog_by_mbid() -> None:
        """The title Spotify knows can differ from MusicBrainz's."""
        catalog = {
                "rg-comp": album_row(
                        3,
                        "Hey Brother / Wake Me Up / You Make Me",
                        "EP",
                        "rg-comp",
                )
        }
        arr = FakeArr([], catalog)
        brainz = FakeBrainz(
                [
                        group_row(
                                "rg-comp",
                                "Hey Brother / Wake Me Up / You Make Me",
                                "EP",
                                ["Wake Me Up"],
                        )
                ]
        )
        engine = await engine_with(arr, brainz)

        album_id, reason = await engine._album(
                {"artist_checks": {}},
                {"title": "Wake Me Up", "artist": "Avicii"},
        )

        assert reason == ""
        assert album_id == 3


async def test_an_unreachable_musicbrainz_keeps_the_old_answer() -> None:
        """A MusicBrainz outage must not fail the import."""
        arr = FakeArr([])
        brainz = FakeBrainz([], broken=True)
        engine = await engine_with(arr, brainz)

        album_id, reason = await engine._album(
                {"artist_checks": {}},
                {"title": "Wake Me Up", "artist": "Avicii"},
        )

        assert album_id is None
        assert reason == "No unique artist/album match"
        assert arr.added == []


async def test_a_group_the_artist_does_not_own_is_not_resolved() -> None:
        """Compilations credited to someone else stay out."""
        catalog = {"rg-va": album_row(4, "MuchDance 2014", "Album", "rg-va")}
        arr = FakeArr([], catalog)
        brainz = FakeBrainz(
                [
                        {
                                **group_row(
                                        "rg-va",
                                        "MuchDance 2014",
                                        "Album",
                                        ["Wake Me Up"],
                                ),
                                "artist": {"artistName": "Various Artists"},
                        }
                ]
        )
        engine = await engine_with(arr, brainz)

        album_id, reason = await engine._album(
                {"artist_checks": {}},
                {"title": "Wake Me Up", "artist": "Avicii"},
        )

        assert album_id is None
        assert reason == "No unique artist/album match"
