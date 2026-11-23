"""Test per-user TV seasons, sharing and archive protection."""

from collections.abc import AsyncIterator
from typing import Any, cast

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from redbot.core import Config
from season_helpers import FakeSonarr, episode_resource, series_resource
from test_acquisition import RequestBot, WizardContext
from watch_helpers import FakeAuthor

from watch.watch import Watch


@pytest.fixture
async def seasons(
        config: Config, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[Watch, FakeSonarr]]:
        """Use real Config and client HTTP with a stateful Sonarr edge."""
        fake = FakeSonarr()
        app = web.Application()
        app.router.add_route("*", "/api/v3/{path:.*}", fake.handle)
        server = TestServer(app)
        await server.start_server()
        bot = RequestBot()
        bot.url = str(server.make_url("/")).rstrip("/")
        monkeypatch.setattr(Config, "get_conf", lambda *_a, **_k: config)
        cog = Watch(cast("Any", bot))
        # Most tests isolate transitions after the immutable baseline exists.
        await config.season_retention_baseline.set({"protected": []})
        try:
                yield cog, fake
        finally:
                await server.close()


async def claim(cog: Watch, user_id: int, season: int) -> None:
        """Create a prior explicit season choice in Red's user data."""
        await cog.config.user_from_id(user_id).tv_seasons.set({"123": season})


def existing_seasons(fake: FakeSonarr, numbers: list[int]) -> None:
        """Populate files and all three seasons' episode metadata."""
        fake.series = [series_resource()]
        fake.episodes = [
                episode_resource(n, has_file=n in numbers) for n in [1, 2, 3]
        ]
        fake.files = [
                {"id": 100 + n, "seriesId": 7, "seasonNumber": n}
                for n in numbers
        ]


async def acquire_season(
        cog: Watch,
        season: int,
        *,
        user: int = 99,
        expected: set[int] | None = None,
) -> str:
        """Invoke the real acquisition entry point with an explicit season."""
        return await cog.acquire(
                cast("Any", FakeAuthor(id=user)),
                "tv",
                series_resource(),
                "1080p",
                season=season,
                expected_delete=expected,
        )


async def test_new_show_monitors_and_searches_only_selected_season(
        seasons: tuple[Watch, FakeSonarr],
) -> None:
        """Request only season two, not all or future seasons."""
        cog, fake = seasons
        await acquire_season(cog, 2)
        assert fake.series[0]["monitorNewItems"] == "none"
        assert [
                (s["seasonNumber"], s["monitored"])
                for s in fake.series[0]["seasons"]
        ] == [(0, False), (1, False), (2, True), (3, False)]
        commands = [
                b for m, p, _, b in fake.calls if m == "POST" and p == "command"
        ]
        assert commands == [
                {"name": "SeasonSearch", "seriesId": 7, "seasonNumber": 2}
        ]
        added = next(
                b for m, p, _, b in fake.calls if m == "POST" and p == "series"
        )
        assert added["addOptions"]["searchForMissingEpisodes"] is False
        assert await cog.config.user_from_id(99).tv_seasons() == {"123": 2}


async def test_switch_deletes_unclaimed_old_season_without_watched_checks(
        seasons: tuple[Watch, FakeSonarr],
) -> None:
        """Release the old season when the user chooses another."""
        cog, fake = seasons
        existing_seasons(fake, [1])
        await claim(cog, 99, 1)
        await acquire_season(cog, 2, expected={1})
        assert fake.files == []
        assert await cog.config.user_from_id(99).tv_seasons() == {"123": 2}
        deleted = [
                b
                for m, p, _, b in fake.calls
                if m == "DELETE" and p == "episodefile/bulk"
        ]
        assert deleted == [{"episodeFileIds": [101]}]
        assert [m for m, p, _, _ in fake.calls if p == "episodefile/bulk"] == [
                "DELETE"
        ]


async def test_another_person_keeps_their_season(
        seasons: tuple[Watch, FakeSonarr],
) -> None:
        """Keep season one while another user still requests it."""
        cog, fake = seasons
        existing_seasons(fake, [1])
        await claim(cog, 99, 1)
        await claim(cog, 88, 1)
        await acquire_season(cog, 2, expected=set())
        assert [f["id"] for f in fake.files] == [101]
        assert [
                s["seasonNumber"]
                for s in fake.series[0]["seasons"]
                if s["monitored"]
        ] == [1, 2]
        assert await cog.config.user_from_id(88).tv_seasons() == {"123": 1}


async def test_different_users_keep_two_different_seasons(
        seasons: tuple[Watch, FakeSonarr],
) -> None:
        """Per-user limits do not become a single global season per show."""
        cog, fake = seasons
        existing_seasons(fake, [1, 2])
        await claim(cog, 99, 1)
        await claim(cog, 88, 2)
        await acquire_season(cog, 3, expected={1})
        assert [f["id"] for f in fake.files] == [102]
        assert [
                s["seasonNumber"]
                for s in fake.series[0]["seasons"]
                if s["monitored"]
        ] == [2, 3]


async def test_fully_downloaded_archive_is_never_pruned(
        seasons: tuple[Watch, FakeSonarr],
) -> None:
        """Retain the protected show's seasons and monitoring."""
        cog, fake = seasons
        existing_seasons(fake, [1, 2, 3])
        await cog.config.season_retention_baseline.set({"protected": [123]})
        await acquire_season(cog, 2, expected=set())
        assert [f["id"] for f in fake.files] == [101, 102, 103]
        assert not any(m == "DELETE" for m, *_ in fake.calls)


async def test_season_wizard_previews_the_season_removed(
        seasons: tuple[Watch, FakeSonarr],
) -> None:
        """Preview deletion after season choice, before quality."""
        cog, fake = seasons
        existing_seasons(fake, [1])
        await claim(cog, 99, 1)
        ctx = WizardContext(["tv", "0", "2", "1080p"], author=FakeAuthor(id=99))
        await Watch.request.callback(cog, ctx, query="Dune")
        assert len(ctx.views) == 4
        assert "season" in ctx.texts[2].lower()
        assert "season 1" in ctx.texts[3].lower()
        assert "delete" in ctx.texts[3].lower()
        assert fake.files == []


async def test_cancel_season_picker_is_read_only(
        seasons: tuple[Watch, FakeSonarr],
) -> None:
        """Cancel without changing claims or deleting files."""
        cog, fake = seasons
        existing_seasons(fake, [1])
        await claim(cog, 99, 1)
        ctx = WizardContext(["tv", "0", "cancel"], author=FakeAuthor(id=99))
        await Watch.request.callback(cog, ctx, query="Dune")
        assert all(m == "GET" for m, *_ in fake.calls)
        assert len(ctx.views) == 3
        assert await cog.config.user_from_id(99).tv_seasons() == {"123": 1}


async def test_stale_preview_cannot_delete_unannounced_seasons(
        seasons: tuple[Watch, FakeSonarr],
) -> None:
        """Reject an expanded deletion without a new preview."""
        cog, fake = seasons
        existing_seasons(fake, [1, 2])
        await claim(cog, 99, 1)
        with pytest.raises(Exception, match="changed"):
                await acquire_season(cog, 3, expected={1})
        assert all(m == "GET" for m, *_ in fake.calls)
        assert [f["id"] for f in fake.files] == [101, 102]


async def test_delete_failure_never_starts_next_season_search(
        seasons: tuple[Watch, FakeSonarr],
) -> None:
        """Do not search the next season after deletion fails."""
        cog, fake = seasons
        existing_seasons(fake, [1])
        await claim(cog, 99, 1)
        fake.fail = "DELETE episodefile/bulk"
        from watch.seasonal import SeasonError

        with pytest.raises(SeasonError, match="saved.*incomplete"):
                await acquire_season(cog, 2, expected={1})
        assert [f["id"] for f in fake.files] == [101]
        assert not next(
                s for s in fake.series[0]["seasons"] if s["seasonNumber"] == 2
        )["monitored"]
        assert not any(
                m == "POST" and p == "command" for m, p, *_ in fake.calls
        )


async def test_current_requested_season_is_shared_without_second_search(
        seasons: tuple[Watch, FakeSonarr],
) -> None:
        """Share an existing season without regrabbing it."""
        cog, fake = seasons
        existing_seasons(fake, [2])
        await claim(cog, 88, 2)
        reply = await acquire_season(cog, 2, expected=set())
        assert "already downloaded" in reply.lower()
        assert [f["id"] for f in fake.files] == [102]
        assert not any(
                m == "POST" and p == "command" for m, p, *_ in fake.calls
        )
        assert await cog.config.user_from_id(99).tv_seasons() == {"123": 2}


async def test_complete_archive_snapshot_is_immutable(
        seasons: tuple[Watch, FakeSonarr],
) -> None:
        """Protect only shows complete at adoption."""
        from watch.seasonal import SeasonManager

        cog, fake = seasons
        existing_seasons(fake, [1, 2, 3])
        other = series_resource(8, 456)
        fake.series.append(other)
        fake.episodes.extend(
                [
                        {**episode_resource(n, has_file=n == 1), "seriesId": 8}
                        for n in [1, 2, 3]
                ]
        )
        await cog.config.season_retention_baseline.set(None)
        manager = SeasonManager(cog)
        baseline = await manager.initialize()
        assert baseline["protected"] == [123]
        fake.episodes[:] = []
        count = len(fake.calls)
        assert await manager.initialize() == baseline
        assert len(fake.calls) == count
        assert all(m == "GET" for m, *_ in fake.calls)


async def test_unused_download_is_cancelled_before_new_season_search(
        seasons: tuple[Watch, FakeSonarr],
) -> None:
        """Cancel old downloads before searching the new season."""
        cog, fake = seasons
        existing_seasons(fake, [1])
        await claim(cog, 99, 1)
        fake.queue = [
                {
                        "id": 50,
                        "seriesId": 7,
                        "seasonNumber": 1,
                        "downloadId": "old",
                }
        ]
        await acquire_season(cog, 2, expected={1})
        assert fake.queue == []
        writes = [(m, p) for m, p, *_ in fake.calls if m != "GET"]
        assert (
                writes.index(("DELETE", "queue/50"))
                < writes.index(("DELETE", "episodefile/bulk"))
                < writes.index(("POST", "command"))
        )
        params = next(
                q
                for m, p, q, _ in fake.calls
                if m == "DELETE" and p == "queue/50"
        )
        assert params == {
                "removeFromClient": "true",
                "blocklist": "false",
                "skipRedownload": "true",
        }


async def test_cannot_cancel_pack_needed_by_another_user(
        seasons: tuple[Watch, FakeSonarr],
) -> None:
        """Refuse to cancel a pack serving another user."""
        from watch.seasonal import SeasonError

        cog, fake = seasons
        existing_seasons(fake, [1, 2])
        await claim(cog, 99, 1)
        await claim(cog, 88, 2)
        fake.queue = [
                {
                        "id": 50,
                        "seriesId": 7,
                        "seasonNumber": 1,
                        "downloadId": "shared",
                },
                {
                        "id": 51,
                        "seriesId": 7,
                        "seasonNumber": 2,
                        "downloadId": "shared",
                },
        ]
        with pytest.raises(SeasonError, match="shared season pack"):
                await acquire_season(cog, 3, expected={1})
        assert all(m == "GET" for m, *_ in fake.calls)
        assert [f["id"] for f in fake.files] == [101, 102]


async def test_season_picker_supports_long_running_shows(
        seasons: tuple[Watch, FakeSonarr],
) -> None:
        """Offer season thirty through pagination."""
        cog, fake = seasons
        title = series_resource()
        title["seasons"] = [{"seasonNumber": n} for n in range(31)]
        ctx = WizardContext(["next", "30"])
        chosen = await cog._choose_requested_season(cast("Any", ctx), title)
        assert chosen == 30
        assert all(len(view.children[0].options) <= 25 for view in ctx.views)
        assert fake.calls == []


async def test_user_without_permission_cannot_switch_seasons(
        seasons: tuple[Watch, FakeSonarr],
) -> None:
        """Refuse unauthorized deletion and claim changes."""
        cog, fake = seasons
        existing_seasons(fake, [1])
        with pytest.raises(PermissionError):
                await acquire_season(cog, 2, user=1)
        assert fake.calls == []
        assert [f["id"] for f in fake.files] == [101]


async def test_late_old_season_import_is_removed_before_next_search(
        seasons: tuple[Watch, FakeSonarr],
) -> None:
        """Clean up an old-season import racing this request."""
        cog, fake = seasons
        existing_seasons(fake, [1])
        await claim(cog, 99, 1)

        imported_once = False

        def imported(method: str, path: str) -> None:
                nonlocal imported_once
                if method == "PUT" and path == "series/7" and not imported_once:
                        imported_once = True
                        fake.files.append(
                                {"id": 111, "seriesId": 7, "seasonNumber": 1}
                        )
                        fake.episodes.append(
                                {
                                        **episode_resource(1),
                                        "id": 711,
                                        "episodeNumber": 2,
                                        "episodeFileId": 111,
                                }
                        )

        fake.after_write = imported
        await acquire_season(cog, 2, expected={1})
        assert fake.files == []


async def test_post_add_scan_preserves_selected_season_monitoring(
        seasons: tuple[Watch, FakeSonarr],
) -> None:
        """Delayed Sonarr add processing must not undo the chosen season."""
        cog, fake = seasons
        await acquire_season(cog, 2)
        fake.finish_initial_scan()
        assert [
                s["seasonNumber"]
                for s in fake.series[0]["seasons"]
                if s["monitored"]
        ] == [2]


async def test_season_claim_still_reports_existing_download_progress(
        seasons: tuple[Watch, FakeSonarr],
) -> None:
        """Preserve download progress after recording a season choice."""
        cog, fake = seasons
        existing_seasons(fake, [])
        fake.queue = [
                {
                        "id": 50,
                        "seriesId": 7,
                        "seasonNumber": 2,
                        "downloadId": "shared",
                        "size": 1000,
                        "sizeleft": 250,
                        "status": "downloading",
                }
        ]
        await claim(cog, 88, 2)
        reply = await acquire_season(cog, 2, expected=set())
        assert "75%" in reply
        assert await cog.config.user_from_id(99).tv_seasons() == {"123": 2}
        assert not any(
                m == "POST" and p == "command" for m, p, *_ in fake.calls
        )


async def test_revoked_permission_blocks_deletion_mid_switch(
        seasons: tuple[Watch, FakeSonarr],
) -> None:
        """Recheck permission after the preceding Sonarr mutation."""
        cog, fake = seasons
        existing_seasons(fake, [1])
        await claim(cog, 1, 1)
        await cog.config.user_from_id(1).download_allowed.set(True)

        async def revoked(method: str, _path: str) -> None:
                if method == "PUT":
                        await cog.config.user_from_id(
                                1
                        ).download_allowed.clear()

        fake.after_write = revoked
        with pytest.raises(PermissionError):
                await acquire_season(cog, 2, user=1, expected={1})
        assert [f["id"] for f in fake.files] == [101]
        assert not any(
                m == "DELETE" or p == "command" for m, p, *_ in fake.calls
        )


async def test_user_data_deletion_forgets_claims_without_deleting_media(
        seasons: tuple[Watch, FakeSonarr],
) -> None:
        """A privacy request clears choices, not the shared library itself."""
        cog, fake = seasons
        existing_seasons(fake, [1])
        await claim(cog, 1, 1)
        await cog.red_delete_data_for_user(requester="user", user_id=1)
        assert await cog.config.user_from_id(1).tv_seasons() == {}
        assert [f["id"] for f in fake.files] == [101]
        assert fake.calls == []


async def test_tv_request_completion_subscription_keeps_the_selected_season(
        seasons: tuple[Watch, FakeSonarr],
) -> None:
        """Catch sending a completion for a different retained user's season."""
        cog, fake = seasons
        existing_seasons(fake, [1])
        ctx = WizardContext(["tv", "0", "2", "1080p"], author=FakeAuthor(id=99))
        await Watch.request.callback(cog, ctx, query="Dune")
        jobs = await cog.config.pending_jobs()
        assert len(jobs) == 1
        job = next(iter(jobs.values()))
        assert job["owner"] == 99
        assert job["kind"] == "tv"
        assert job["season"] == 2
