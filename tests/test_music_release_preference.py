"""Prefer cacheable release groups when a track resolves to several albums."""

from typing import Any


def album(
        album_id: int,
        title: str,
        kind: str | None,
        secondary: list[str] | None = None,
) -> dict[str, Any]:
        """Build a Lidarr album resource as album/lookup returns it."""
        return {
                "id": album_id,
                "foreignAlbumId": f"mbid-{album_id}",
                "title": title,
                "albumType": kind,
                "secondaryTypes": secondary or [],
                "artist": {"artistName": "Avicii"},
        }


def choose(
        track_title: str, candidates: list[dict[str, Any]]
) -> dict[str, Any] | None:
        """Select an album for a track by the artist above."""
        from watch.music import choose_album

        source = {"title": track_title, "artist": "Avicii"}
        return choose_album(source, candidates)


def test_a_studio_album_wins_over_a_single_with_the_same_title() -> None:
        """Uncached singles stall downloads; the album is the safe target."""
        candidates = [
                album(1, "Wake Me Up", "Single"),
                album(2, "Wake Me Up", "Album"),
        ]
        selected = choose("Wake Me Up", candidates)
        assert selected is not None
        assert selected["id"] == 2


def test_an_ep_wins_over_a_single_but_not_over_an_album() -> None:
        """Rank album, then EP, then single."""
        candidates = [
                album(1, "Levels", "Single"),
                album(2, "Levels", "EP"),
        ]
        selected = choose("Levels", candidates)
        assert selected is not None
        assert selected["id"] == 2

        candidates.append(album(3, "Levels", "Album"))
        selected = choose("Levels", candidates)
        assert selected is not None
        assert selected["id"] == 3


def test_a_plain_album_wins_over_a_compilation_or_live_edition() -> None:
        """A studio album imports more reliably than a compilation."""
        candidates = [
                album(1, "True", "Album", ["Compilation"]),
                album(2, "True", "Album", ["Live"]),
                album(3, "True", "Album"),
        ]
        selected = choose("True", candidates)
        assert selected is not None
        assert selected["id"] == 3


def test_a_single_wins_over_a_remix_edition() -> None:
        """A remix collection is worse than the plain single for one track."""
        candidates = [
                album(1, "Wake Me Up", "Single", ["Remix"]),
                album(2, "Wake Me Up", "Single"),
        ]
        selected = choose("Wake Me Up", candidates)
        assert selected is not None
        assert selected["id"] == 2


def test_equally_ranked_albums_stay_ambiguous() -> None:
        """Never guess between two genuine candidates."""
        candidates = [
                album(1, "True", "Album"),
                album(2, "True", "Album"),
        ]
        assert choose("True", candidates) is None


def test_albums_without_a_release_type_stay_ambiguous() -> None:
        """Keep the old behaviour for lookups that carry no type."""
        candidates = [
                album(1, "Violator", None),
                album(2, "Violator", None),
        ]
        assert choose("Violator", candidates) is None


def test_a_lone_single_is_still_selected() -> None:
        """Preference is a tie-break, not a filter."""
        selected = choose("Wake Me Up", [album(1, "Wake Me Up", "Single")])
        assert selected is not None
        assert selected["id"] == 1


def test_release_preference_never_overrides_the_artist_check() -> None:
        """A better-ranked wrong artist is still rejected."""
        candidates = [
                album(1, "True", "Album"),
                {
                        "id": 2,
                        "foreignAlbumId": "mbid-2",
                        "title": "True",
                        "albumType": "Album",
                        "secondaryTypes": [],
                        "artist": {"artistName": "A Tribute Band"},
                },
        ]
        selected = choose("True", candidates)
        assert selected is not None
        assert selected["id"] == 1
