"""Public playlist input contracts, independent of Spotify account sessions."""

import pytest


def test_playlist_url_does_not_allow_an_arbitrary_fetch_target() -> None:
        """Reject arbitrary hosts and non-playlist URLs."""
        from watch.spotify import playlist_id

        assert (
                playlist_id(
                        "https://open.spotify.com/playlist/7kRUsOsraSiY4fyDcu7Bnb?si=share"
                )
                == "7kRUsOsraSiY4fyDcu7Bnb"
        )
        for bad in (
                "http://127.0.0.1/playlist/7kRUsOsraSiY4fyDcu7Bnb",
                "https://open.spotify.com.evil/playlist/7kRUsOsraSiY4fyDcu7Bnb",
                "https://user@open.spotify.com/playlist/7kRUsOsraSiY4fyDcu7Bnb",
                "https://open.spotify.com:444/playlist/7kRUsOsraSiY4fyDcu7Bnb",
                "https://open.spotify.com/track/7kRUsOsraSiY4fyDcu7Bnb",
        ):
                with pytest.raises(ValueError, match="public Spotify playlist"):
                        playlist_id(bad)


def test_snapshot_keeps_playlist_order_and_marks_public_limits() -> None:
        """Catch lost order and undisclosed public limits."""
        import json

        from watch.spotify import parse_playlist

        track = {
                "uri": "spotify:track:6kwEU2hLQpMToj1PHsBrOE",
                "title": "Halo",
                "subtitle": "Depeche Mode",
                "duration": 270186,
        }
        entity = {
                "id": "0rA4Uu2pJd3IoHLcLS8rOx",
                "name": "My mix",
                "trackList": [track, track],
        }

        def page() -> str:
                return (
                        '<script id="__NEXT_DATA__" type="application/json">'
                        + json.dumps(
                                {
                                        "props": {
                                                "pageProps": {
                                                        "state": {
                                                                "data": {
                                                                        "entity": entity  # noqa: E501
                                                                }
                                                        }
                                                }
                                        }
                                }
                        )
                        + "</script>"
                )

        result = parse_playlist(page())
        assert result["name"] == "My mix"
        assert [t["id"] for t in result["tracks"]] == [
                "6kwEU2hLQpMToj1PHsBrOE",
                "6kwEU2hLQpMToj1PHsBrOE",
        ]
        assert result["partial"] is False
        entity["trackList"] = [track] * 100
        assert parse_playlist(page())["partial"] is True
        with pytest.raises(ValueError, match="public"):
                parse_playlist("<html>Sign in to view this playlist</html>")


def test_album_pages_supply_metadata_without_guessing_a_recommendation() -> (
        None
):
        """Catch using any album link on the page instead of music:album."""
        import json

        from watch.spotify import parse_album, track_album

        assert (
                track_album(
                        "<a "
                        'href="https://open.spotify.com/album/WRONG">Other</a><meta name="music:album" content="https://open.spotify.com/album/45YmvYK4hB4CgQgTMuNRm8"/>'  # noqa: E501
                )
                == "45YmvYK4hB4CgQgTMuNRm8"
        )
        data = {
                "props": {
                        "pageProps": {
                                "state": {
                                        "data": {
                                                "entity": {
                                                        "name": "Violator",
                                                        "subtitle": "Depeche Mode",  # noqa: E501
                                                }
                                        }
                                }
                        }
                }
        }
        assert parse_album(
                '<script id="__NEXT_DATA__" type="application/json">'
                + json.dumps(data)
                + "</script>"
        ) == {"title": "Violator", "artist": "Depeche Mode"}


async def test_public_fetch_refuses_redirects_and_oversized_pages() -> None:
        """Catch redirects and unbounded public input."""
        import httpx

        from watch.spotify import SpotifyPublic

        async def handler(request: httpx.Request) -> httpx.Response:
                if request.url.path.endswith("7kRUsOsraSiY4fyDcu7Bnb"):
                        return httpx.Response(
                                302,
                                headers={
                                        "Location": "http://127.0.0.1/private"
                                },
                        )
                return httpx.Response(200, content=b"x" * (4 * 1024 * 1024 + 1))

        async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
        ) as client:
                reader = SpotifyPublic(client)
                with pytest.raises(ValueError, match="Spotify"):
                        await reader.snapshot(
                                "https://open.spotify.com/playlist/7kRUsOsraSiY4fyDcu7Bnb"
                        )
                with pytest.raises(ValueError, match="large"):
                        await reader.snapshot(
                                "https://open.spotify.com/playlist/0rA4Uu2pJd3IoHLcLS8rOx"
                        )


async def test_track_album_resolution_uses_only_explicit_public_metadata() -> (
        None
):
        """Catch guessing albums or fetching audio previews."""
        import json

        import httpx

        from watch.spotify import SpotifyPublic

        async def handler(request: httpx.Request) -> httpx.Response:
                if (
                        request.url.host != "open.spotify.com"
                        or "Authorization" in request.headers
                ):
                        return httpx.Response(403)
                if request.url.path == "/track/6kwEU2hLQpMToj1PHsBrOE":
                        return httpx.Response(
                                200,
                                text=(
                                        '<meta name="music:album" '
                                        'content="https://open.spotify.com/album/45YmvYK4hB4CgQgTMuNRm8"/>'
                                ),
                        )
                if request.url.path == "/embed/album/45YmvYK4hB4CgQgTMuNRm8":
                        entity = {
                                "name": "Violator",
                                "subtitle": "Depeche Mode",
                        }
                        return httpx.Response(
                                200,
                                text='<script id="__NEXT_DATA__">'
                                + json.dumps(
                                        {
                                                "props": {
                                                        "pageProps": {
                                                                "state": {
                                                                        "data": {  # noqa: E501
                                                                                "entity": entity  # noqa: E501
                                                                        }
                                                                }
                                                        }
                                                }
                                        }
                                )
                                + "</script>",
                        )
                return httpx.Response(404)

        async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
        ) as client:
                reader = SpotifyPublic(client)
                assert await reader.album_for_track(
                        "6kwEU2hLQpMToj1PHsBrOE"
                ) == {
                        "id": "45YmvYK4hB4CgQgTMuNRm8",
                        "title": "Violator",
                        "artist": "Depeche Mode",
                }
                with pytest.raises(ValueError):
                        await reader.album_for_track("../../private")
