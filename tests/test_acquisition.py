"""Download permission and ARR contracts over real HTTP."""

from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from redbot.core import Config
from watch_helpers import FakeAuthor, FakeContext

from watch.watch import Watch


class RequestBot:
        """Only user 99 is a bot owner; moderators receive no implicit grant."""

        url = ""

        async def is_owner(self, user: Any) -> bool:  # noqa: ANN401
                """Check the test owner identity."""
                return user.id == 99

        async def get_shared_api_tokens(self, _service: str) -> dict[str, str]:
                """Give the real client its fake HTTP edge."""
                return {"url": self.url, "api_key": "test-key"}


@pytest.fixture
async def acquisition(  # noqa: C901 (HTTP fixture edge)
        config: Config, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[Watch, list[Any], dict[str, Any]]]:
        """Serve realistic lookup, profile, root and add endpoints."""
        calls: list[Any] = []
        state: dict[str, Any] = {"existing": [], "status": 200}

        async def handler(request: web.Request) -> web.Response:  # noqa: C901 (HTTP fixture edge)
                body = await request.json() if request.can_read_body else None
                calls.append(
                        (
                                request.method,
                                request.path,
                                dict(request.query),
                                body,
                        )
                )
                if request.headers.get("X-Api-Key") != "test-key":
                        return web.json_response(
                                {"error": "bad key"}, status=401
                        )
                if state["status"] != 200:
                        return web.json_response(
                                {"error": "private-key"}, status=state["status"]
                        )
                path = request.path.removeprefix("/api/v3/")
                if path.endswith("/lookup"):
                        return web.json_response(
                                [
                                        {
                                                "title": "Dune",
                                                "year": 2021,
                                                "tmdbId": 438631,
                                                "tvdbId": 123,
                                                "titleSlug": "dune-2021",
                                                "images": [],
                                                "seasons": [
                                                        {"seasonNumber": 0},
                                                        {"seasonNumber": 1},
                                                ],
                                        }
                                ]
                        )
                if path == "queue/details":
                        if state.get("queue_error"):
                                return web.json_response(
                                        {"error": "private-path"}, status=503
                                )
                        return web.json_response(state.get("queue", []))
                if path == "qualityprofile":
                        if "profiles" in state:
                                return web.json_response(state["profiles"])
                        return web.json_response(
                                [
                                        {
                                                "id": 1,
                                                "name": "Doge - 1080p default",
                                        },
                                        {"id": 3, "name": "Doge - 4K optional"},
                                ]
                        )
                if path == "rootfolder":
                        if "on_roots" in state:
                                await state["on_roots"]()
                        return web.json_response(
                                [
                                        {
                                                "path": "/arr/library/Movies",
                                                "accessible": True,
                                        },
                                        {
                                                "path": "/arr/library/TV",
                                                "accessible": True,
                                        },
                                ]
                        )
                if path == "movie":
                        return web.json_response(
                                [
                                        t
                                        for t in state["existing"]
                                        if t.get("tmdbId")
                                ]
                        )
                if path == "series":
                        return web.json_response(
                                [
                                        t
                                        for t in state["existing"]
                                        if t.get("tvdbId")
                                ]
                        )
                if request.method == "GET":
                        return web.json_response(state["existing"])
                return web.json_response({**(body or {}), "id": 7}, status=201)

        server = TestServer(web.Application())
        server.app.router.add_route("*", "/api/v3/{path:.*}", handler)
        await server.start_server()
        bot = RequestBot()
        bot.url = str(server.make_url("/")).rstrip("/")
        monkeypatch.setattr(Config, "get_conf", lambda *_a, **_k: config)
        cog = Watch(cast("Any", bot))
        try:
                yield cog, calls, state
        finally:
                await cog.cog_unload()
                await server.close()


async def test_owner_only_default_and_explicit_individual_grants(
        acquisition: tuple[Watch, list[Any], dict[str, Any]],
) -> None:  # noqa: ANN401
        """Only owners and explicitly granted users can download."""
        cog, calls, _ = acquisition
        assert await cog.can_download(cast("Any", FakeAuthor(id=99)))
        assert not await cog.can_download(cast("Any", FakeAuthor()))
        owner = FakeContext(author=FakeAuthor(id=99))
        await Watch.request_allow.callback(cog, owner, user=FakeAuthor())
        assert await cog.can_download(cast("Any", FakeAuthor()))
        await Watch.request_revoke.callback(cog, owner, user=FakeAuthor())
        assert not await cog.can_download(cast("Any", FakeAuthor()))
        assert calls == []


async def test_nonowner_cannot_grant_themselves(
        acquisition: tuple[Watch, list[Any], dict[str, Any]],
) -> None:  # noqa: ANN401
        """Direct permission operations still check the owner."""
        cog, calls, _ = acquisition
        await Watch.request_allow.callback(
                cog, FakeContext(), user=FakeAuthor()
        )
        assert not await cog.can_download(cast("Any", FakeAuthor()))
        assert calls == []


async def test_unauthorized_request_stops_before_lookup(
        acquisition: tuple[Watch, list[Any], dict[str, Any]],
) -> None:  # noqa: ANN401
        """Calling the new command never contacts ARR without a grant."""
        cog, calls, _ = acquisition
        ctx = FakeContext()
        await Watch.request.callback(cog, ctx, query="Dune")
        assert calls == []
        assert "permission" in ctx.texts[0].lower()


@pytest.mark.parametrize(
        ("kind", "quality", "endpoint", "profile", "root"),
        [
                ("movie", "1080p", "movie", 1, "/arr/library/Movies"),
                ("movie", "4k", "movie", 3, "/arr/library/Movies"),
                ("tv", "1080p", "series", 1, "/arr/library/TV"),
                ("tv", "4k", "series", 3, "/arr/library/TV"),
        ],
)
async def test_selected_profile_and_type_reach_arr(
        acquisition: tuple[Watch, list[Any], dict[str, Any]],
        kind: str,
        quality: str,
        endpoint: str,
        profile: int,
        root: str,
) -> None:  # noqa: ANN401, PLR0913
        """The selected type and profile reach the correct ARR endpoint."""
        cog, calls, _ = acquisition
        results = await cog.acquisition_search(kind, "Dune")
        assert results[0]["title"] == "Dune"
        assert all(method == "GET" for method, *_ in calls)
        await cog.acquire(
                cast("Any", FakeAuthor(id=99)), kind, results[0], quality
        )
        added = [
                body
                for method, path, _, body in calls
                if method == "POST" and path == "/api/v3/" + endpoint
        ]
        assert len(added) == 1
        assert added[0]["qualityProfileId"] == profile
        assert added[0]["rootFolderPath"] == root
        assert added[0]["monitored"] is True
        if kind == "movie":
                assert added[0]["tmdbId"] == 438631
                assert added[0]["addOptions"]["searchForMovie"] is True
        else:
                assert added[0]["tvdbId"] == 123
                assert added[0]["monitorNewItems"] == "all"
                assert added[0]["seasons"] == [
                        {"seasonNumber": 0, "monitored": False},
                        {"seasonNumber": 1, "monitored": True},
                ]
                assert (
                        added[0]["addOptions"]["searchForMissingEpisodes"]
                        is True
                )


async def test_revocation_before_submission_blocks_all_arr_writes(
        acquisition: tuple[Watch, list[Any], dict[str, Any]],
) -> None:  # noqa: ANN401
        """An open wizard cannot bypass a grant revoked while choosing."""
        cog, calls, _ = acquisition
        title = (await cog.acquisition_search("movie", "Dune"))[0]
        with pytest.raises(PermissionError):
                await cog.acquire(
                        cast("Any", FakeAuthor()), "movie", title, "1080p"
                )
        assert all(method == "GET" for method, *_ in calls)


async def test_existing_title_is_not_added_or_searched_again(
        acquisition: tuple[Watch, list[Any], dict[str, Any]],
) -> None:  # noqa: ANN401
        """Existing entries are neither modified nor duplicated."""
        cog, calls, state = acquisition
        state["existing"] = [{"id": 8, "tmdbId": 438631, "hasFile": True}]
        title = (await cog.acquisition_search("movie", "Dune"))[0]
        result = await cog.acquire(
                cast("Any", FakeAuthor(id=99)), "movie", title, "4k"
        )
        assert "already" in result.lower()
        assert all(method == "GET" for method, *_ in calls)


async def test_arr_error_does_not_leak_response_credentials(
        acquisition: tuple[Watch, list[Any], dict[str, Any]],
) -> None:  # noqa: ANN401
        """HTTP errors never expose private response data."""
        cog, _, state = acquisition
        state["status"] = 401
        with pytest.raises(Exception) as exc:
                await cog.acquisition_search("movie", "Dune")
        assert "401" in str(exc.value)
        assert "private-key" not in str(exc.value)


class InteractionResponse:
        """Discord response edge with no network."""

        async def edit_message(self, **_kwargs: Any) -> None:  # noqa: ANN401
                """Accept a prompt replacement."""

        async def send_message(self, *_args: Any, **_kwargs: Any) -> None:  # noqa: ANN401
                """Accept an ephemeral refusal."""


class WizardContext(FakeContext):
        """Drive the actual views from scheduled Discord interactions."""

        def __init__(self, selections: list[str], **kwargs: Any) -> None:  # noqa: ANN401
                """Store scripted human choices."""
                super().__init__(**kwargs)
                self.selections = iter(selections)
                self.views: list[Any] = []

        async def send(self, content: str | None = None, **kwargs: Any) -> Any:  # noqa: ANN401
                """Respond to the next real view as the invoking user."""
                import asyncio
                from types import SimpleNamespace

                message = await super().send(content, **kwargs)
                if view := kwargs.get("view"):
                        self.views.append(view)
                        value = next(self.selections)
                        interaction = SimpleNamespace(
                                user=self.author, response=InteractionResponse()
                        )
                        asyncio.create_task(view.picked(interaction, value))
                return message


async def test_wizard_requires_type_title_then_quality(
        acquisition: tuple[Watch, list[Any], dict[str, Any]],
) -> None:  # noqa: ANN401
        """Each selection constrains the next; only the final choice can add."""
        cog, calls, _ = acquisition
        ctx = WizardContext(["movie", "0", "4k"], author=FakeAuthor(id=99))
        await Watch.request.callback(cog, ctx, query="Dune")
        assert len(ctx.views) == 3
        assert "Dune (2021)" in ctx.texts[2]
        assert [o.value for o in ctx.views[0].children[0].options][:2] == [
                "movie",
                "tv",
        ]
        assert "2021" in ctx.views[1].children[0].options[0].label
        assert [o.value for o in ctx.views[2].children[0].options][:2] == [
                "1080p",
                "4k",
        ]
        assert sum(method == "POST" for method, *_ in calls) == 1
        assert "requested" in ctx.texts[-1].lower()


async def test_cancel_at_quality_makes_no_download(
        acquisition: tuple[Watch, list[Any], dict[str, Any]],
) -> None:  # noqa: ANN401
        """Cancelling the final step never adds or searches anything."""
        cog, calls, _ = acquisition
        ctx = WizardContext(["tv", "0", "cancel"], author=FakeAuthor(id=99))
        await Watch.request.callback(cog, ctx, query="Dune")
        assert all(method == "GET" for method, *_ in calls)


async def test_other_user_cannot_choose_request_options() -> None:
        """Another user cannot consume the requester's view."""
        from types import SimpleNamespace

        from watch import watch as module

        view = module.PickDownloadOption(
                cast("Any", FakeAuthor()),
                [("movie", "Movie"), ("tv", "TV show")],
        )
        interaction = SimpleNamespace(
                user=FakeAuthor(id=99), response=InteractionResponse()
        )
        await view.picked(cast("Any", interaction), "movie")
        assert view.chosen is None
        assert not view.is_finished()
        view.stop()


async def test_grant_revoked_during_http_cannot_submit(
        acquisition: tuple[Watch, list[Any], dict[str, Any]],
) -> None:
        """Revocation while checking profiles blocks the eventual POST."""
        cog, calls, state = acquisition
        user = FakeAuthor()
        await cog.config.user(user).download_allowed.set(True)
        title = (await cog.acquisition_search("movie", "Dune"))[0]

        async def revoke() -> None:
                await cog.config.user(user).download_allowed.clear()

        state["on_roots"] = revoke
        with pytest.raises(PermissionError):
                await cog.acquire(cast("Any", user), "movie", title, "1080p")
        assert all(method == "GET" for method, *_ in calls)


async def test_timeout_discards_prompt_without_contacting_arr(
        acquisition: tuple[Watch, list[Any], dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
) -> None:
        """An expired type picker is cleaned up and cannot trigger lookup."""
        from watch.watch import PickDownloadOption

        async def timed_out(_self: PickDownloadOption) -> bool:
                return True

        monkeypatch.setattr(PickDownloadOption, "wait", timed_out)
        cog, calls, _ = acquisition
        ctx = FakeContext(author=FakeAuthor(id=99))
        await Watch.request.callback(cog, ctx, query="Dune")
        assert calls == []
        assert ctx.sent[0].edits[-1]["view"] is None


async def test_deleting_user_data_removes_download_grant(
        acquisition: tuple[Watch, list[Any], dict[str, Any]],
) -> None:
        """Red data deletion does not leave a forgotten download permission."""
        cog, _, _ = acquisition
        await cog.config.user(FakeAuthor()).download_allowed.set(True)
        await cog.red_delete_data_for_user(requester="user", user_id=1)
        assert not await cog.can_download(cast("Any", FakeAuthor()))


async def test_missing_quality_profile_never_posts(
        acquisition: tuple[Watch, list[Any], dict[str, Any]],
) -> None:
        """A missing profile cannot silently fall back to another quality."""
        cog, calls, state = acquisition
        state["profiles"] = [{"id": 1, "name": "Doge - 1080p default"}]
        title = (await cog.acquisition_search("movie", "Dune"))[0]
        with pytest.raises(Exception, match="unavailable"):
                await cog.acquire(
                        cast("Any", FakeAuthor(id=99)), "movie", title, "4k"
                )
        assert all(method == "GET" for method, *_ in calls)


def queued_download(**changes: Any) -> dict[str, Any]:  # noqa: ANN401
        """Build a queue resource with private release details to protect."""
        return {
                "id": 10,
                "movieId": 8,
                "seriesId": 8,
                "episodeId": 81,
                "seasonNumber": 1,
                "episode": {"id": 81, "seasonNumber": 1, "episodeNumber": 2},
                "size": 1000,
                "sizeleft": 250,
                "timeleft": "00:10:00",
                "status": "downloading",
                "trackedDownloadState": "downloading",
                "trackedDownloadStatus": "ok",
                "downloadId": "private-id",
                "title": "private-release",
                "outputPath": "/private/path",
                "errorMessage": "private-credential",
                "statusMessages": [],
                **changes,
        }


async def test_existing_movie_reports_progress_without_duplicate(
        acquisition: tuple[Watch, list[Any], dict[str, Any]],
) -> None:
        """A duplicate request reads queue bytes/ETA and performs no writes."""
        cog, calls, state = acquisition
        state["existing"] = [
                {
                        "id": 8,
                        "title": "Dune",
                        "year": 2021,
                        "tmdbId": 438631,
                        "hasFile": False,
                }
        ]
        state["queue"] = [
                queued_download(),
                queued_download(movieId=99, sizeleft=0),
        ]
        title = (await cog.acquisition_search("movie", "Dune"))[0]
        result = await cog.acquire(
                cast("Any", FakeAuthor(id=99)), "movie", title, "4k"
        )
        assert "downloading" in result.lower()
        assert "75%" in result
        assert "10m" in result
        assert "1 download in ARR:" in result
        assert "private-" not in result
        assert all(method == "GET" for method, *_ in calls)
        assert any(
                path == "/api/v3/queue/details" and params.get("movieId") == "8"
                for _, path, params, _ in calls
        )


async def test_request_existing_title_skips_quality_and_shows_progress(
        acquisition: tuple[Watch, list[Any], dict[str, Any]],
) -> None:
        """Existing-title requests do not ask for an irrelevant new profile."""
        cog, calls, state = acquisition
        state["existing"] = [
                {"id": 8, "title": "Dune", "year": 2021, "tmdbId": 438631}
        ]
        state["queue"] = [queued_download()]
        ctx = WizardContext(["movie", "0", "4k"], author=FakeAuthor(id=99))
        await Watch.request.callback(cog, ctx, query="Dune")
        assert len(ctx.views) == 2
        assert "75%" in ctx.texts[-1]
        assert all(method == "GET" for method, *_ in calls)


@pytest.mark.parametrize(
        ("updates", "expected", "absent"),
        [
                ({"status": "paused"}, "paused", "downloading"),
                (
                        {
                                "status": "downloading",
                                "trackedDownloadState": "importPending",
                        },
                        "awaiting import",
                        "downloading",
                ),
                (
                        {"status": "downloadClientUnavailable"},
                        "client unavailable",
                        "downloading",
                ),
                ({"status": "stalled"}, "stalled", "downloading"),
                ({"status": "queued"}, "queued", "downloading"),
                (
                        {
                                "status": "completed",
                                "trackedDownloadState": "importPending",
                                "sizeleft": 0,
                        },
                        "awaiting import",
                        "downloading",
                ),
                (
                        {
                                "status": "completed",
                                "trackedDownloadState": "importing",
                                "sizeleft": 0,
                        },
                        "importing",
                        "downloading",
                ),
                (
                        {"size": 0, "sizeleft": 0, "timeleft": None},
                        "progress unavailable",
                        "100%",
                ),
                ({"sizeleft": 1500}, "0%", "-50%"),
                ({"sizeleft": -100}, "100%", "110%"),
                (
                        {"trackedDownloadStatus": "warning"},
                        "needs attention",
                        "private-credential",
                ),
        ],
)
async def test_download_status_does_not_misrepresent_queue_state(
        acquisition: tuple[Watch, list[Any], dict[str, Any]],
        updates: dict[str, Any],
        expected: str,
        absent: str,
) -> None:
        """Report queue states and safely handle unknown byte counts."""
        cog, _, state = acquisition
        state["queue"] = [queued_download(**updates)]
        text = await cog.acquisition_status("movie", {"id": 8, "title": "Dune"})
        assert expected in text.lower()
        assert absent not in text.lower()


async def test_no_active_download_is_not_called_downloading(
        acquisition: tuple[Watch, list[Any], dict[str, Any]],
) -> None:
        """Managed-but-unavailable media does not imply a download exists."""
        cog, _, _ = acquisition
        text = await cog.acquisition_status(
                "movie", {"id": 8, "title": "Dune", "hasFile": False}
        )
        assert "no active download" in text.lower()
        assert "downloading" not in text.lower()


async def test_tv_season_pack_is_not_counted_once_per_episode(
        acquisition: tuple[Watch, list[Any], dict[str, Any]],
) -> None:
        """Show one transfer row for a multi-episode season pack."""
        cog, _, state = acquisition
        state["queue"] = [
                queued_download(),
                queued_download(id=11, episodeId=82),
                queued_download(id=12, downloadId="other", sizeleft=500),
        ]
        text = await cog.acquisition_status("tv", {"id": 8, "title": "Dune"})
        assert text.count("75%") == 1
        assert text.count("50%") == 1
        assert "2 downloads" in text.lower()
        assert "private-" not in text


@pytest.mark.parametrize("query", ["Dune", "Dune 2021"])
async def test_watch_reports_arr_progress_without_download_permission(
        acquisition: tuple[Watch, list[Any], dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
        query: str,
) -> None:
        """Allow read-only status without permission to download."""
        from watch_helpers import TOKEN, FakePreload, serve

        cog, calls, state = acquisition
        state["existing"] = [
                {"id": 8, "title": "Dune", "year": 2021, "tmdbId": 438631}
        ]
        state["queue"] = [queued_download()]
        fake = FakePreload()
        async with serve(fake) as url:
                monkeypatch.setenv("PRELOAD_API_URL", url)
                monkeypatch.setenv("PRELOAD_BOT_TOKEN", TOKEN)
                ctx = FakeContext()
                await Watch.watch.callback(cog, ctx, query=query)
        assert "75%" in ctx.texts[-1]
        assert "request Dune" not in ctx.texts[-1]
        assert not await cog.can_download(cast("Any", FakeAuthor()))
        assert all(method == "GET" for method, *_ in calls)
        assert fake.calls_to("POST", "/v1/requests") == []


async def test_queue_failure_does_not_claim_download_is_missing(
        acquisition: tuple[Watch, list[Any], dict[str, Any]],
) -> None:
        """Report unavailable status when the queue API fails."""
        cog, _, state = acquisition
        state["queue_error"] = True
        text = await cog.acquisition_status("movie", {"id": 8, "title": "Dune"})
        assert "temporarily unavailable" in text.lower()
        assert "private-path" not in text
        assert "no active download" not in text.lower()


async def test_partial_tv_library_reports_progress_when_episode_is_missing(
        acquisition: tuple[Watch, list[Any], dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
) -> None:
        """A library series may exist while its requested episode downloads."""
        from watch_helpers import TOKEN, FakePreload, media_item, serve

        cog, calls, state = acquisition
        state["existing"] = [
                {"id": 8, "title": "Dune", "year": 2021, "tvdbId": 123}
        ]
        state["queue"] = [queued_download()]
        fake = FakePreload(
                search_results=[media_item("jf1", "Dune", kind="series")]
        )

        async def missing_episode(_request: web.Request) -> web.Response:
                return web.json_response(
                        {
                                "error": (
                                        "nothing to cache for Dune "
                                        "with those options"
                                )
                        },
                        status=400,
                )

        monkeypatch.setattr(fake, "_create", missing_episode)
        async with serve(fake) as url:
                monkeypatch.setenv("PRELOAD_API_URL", url)
                monkeypatch.setenv("PRELOAD_BOT_TOKEN", TOKEN)
                ctx = FakeContext()
                await Watch.watch.callback(cog, ctx, query="Dune s1e2")
        assert "75%" in ctx.texts[-1]
        assert "S01E02" in ctx.texts[-1]
        assert all(method == "GET" for method, *_ in calls)


async def test_ambiguous_watch_selects_correct_media_type(
        acquisition: tuple[Watch, list[Any], dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
) -> None:
        """A movie and series sharing a name must not show each other's job."""
        from watch_helpers import TOKEN, FakePreload, serve

        cog, calls, state = acquisition
        state["existing"] = [
                {"id": 8, "title": "Dune", "year": 2021, "tmdbId": 438631},
                {"id": 9, "title": "Dune", "year": 2000, "tvdbId": 123},
        ]
        state["queue"] = [
                queued_download(),
                queued_download(id=11, seriesId=9, movieId=None, sizeleft=500),
        ]
        async with serve(FakePreload()) as url:
                monkeypatch.setenv("PRELOAD_API_URL", url)
                monkeypatch.setenv("PRELOAD_BOT_TOKEN", TOKEN)
                ctx = WizardContext(["1"])
                await Watch.watch.callback(cog, ctx, query="Dune")
        assert len(ctx.views) == 1
        assert "50%" in ctx.texts[-1]
        assert "75%" not in ctx.texts[-1]
        assert all(method == "GET" for method, *_ in calls)
