"""Conservative album and audio matching for playlist acquisition."""


def test_only_one_canonical_album_for_the_credited_artist_is_selected() -> None:
        """Reject first-hit and wrong-artist album matches."""
        from watch.music import choose_album

        candidates = [
                {
                        "id": 4,
                        "foreignAlbumId": "wrong",
                        "title": "Violator",
                        "artist": {"artistName": "Other"},
                },
                {
                        "id": 7,
                        "foreignAlbumId": "right",
                        "title": "Violator",
                        "artist": {"artistName": "Depeche Mode"},
                },
        ]
        selected = choose_album(
                {"title": "Violator (Remastered)", "artist": "Depeche Mode"},
                candidates,
        )
        assert selected is not None
        assert selected["id"] == 7
        candidates.append(
                {
                        "id": 8,
                        "foreignAlbumId": "different",
                        "title": "Violator",
                        "artist": {"artistName": "Depeche Mode"},
                }
        )
        assert (
                choose_album(
                        {"title": "Violator", "artist": "Depeche Mode"},
                        candidates,
                )
                is None
        )


def test_audio_match_requires_unique_artist_title_and_length() -> None:
        """Reject wrong artists, versions and ambiguity."""
        from watch.music import find_audio

        track = {"title": "Halo", "artist": "Depeche Mode", "duration": 270186}
        items = [
                {
                        "Id": "wrong-artist",
                        "Name": "Halo",
                        "Artists": ["Beyoncé"],
                        "RunTimeTicks": 2700000000,
                },
                {
                        "Id": "wrong-version",
                        "Name": "Halo",
                        "Artists": ["Depeche Mode"],
                        "RunTimeTicks": 3300000000,
                },
                {
                        "Id": "correct",
                        "Name": "Halo",
                        "Artists": ["Depeche Mode"],
                        "RunTimeTicks": 2700000000,
                },
        ]
        assert find_audio(track, items) == "correct"
        items.append(
                {
                        "Id": "ambiguous",
                        "Name": "Halo",
                        "Artists": ["Depeche Mode"],
                        "RunTimeTicks": 2701000000,
                }
        )
        assert find_audio(track, items) is None
        assert find_audio({**track, "duration": 0}, items) is None
