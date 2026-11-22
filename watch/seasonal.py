"""Shared per-person TV-season requests against the existing Sonarr API."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .errors import PreloadError, SeasonError
from .retention import SeasonPlan, complete_archive, plan_seasons

if TYPE_CHECKING:
        from .watch import Person, Watch


@dataclass
class SeasonSnapshot:
        """Resolve episode files by ID, never arbitrary paths."""

        series: dict[str, Any] | None
        episodes: list[dict[str, Any]]
        files: list[dict[str, Any]]
        queue: list[dict[str, Any]]

        def queued_season(self, row: dict[str, Any]) -> int | None:
                """Resolve episode-less season-pack rows conservatively."""
                number = row.get("seasonNumber")
                if number is None:
                        number = (row.get("episode") or {}).get("seasonNumber")
                if number is None:
                        number = next(
                                (
                                        e["seasonNumber"]
                                        for e in self.episodes
                                        if e["id"] == row.get("episodeId")
                                ),
                                None,
                        )
                return number if isinstance(number, int) else None

        def present_seasons(self) -> set[int]:
                """Collect downloaded and queued seasons for previews."""
                result = {f["seasonNumber"] for f in self.files}
                result.update(
                        n
                        for row in self.queue
                        if (n := self.queued_season(row)) is not None
                )
                return result

        def deletion_ids(self, plan: SeasonPlan) -> list[int]:
                """Delete files belonging only to unclaimed seasons."""
                result = []
                if plan.protected:
                        return result
                for file in self.files:
                        if file.get("seriesId") != (self.series or {}).get(
                                "id"
                        ):
                                msg = (
                                        "Sonarr returned a file from "
                                        "another show; nothing was "
                                        "deleted."
                                )
                                raise SeasonError(msg)
                        seasons = {
                                e["seasonNumber"]
                                for e in self.episodes
                                if e.get("episodeFileId") == file["id"]
                        }
                        seasons.add(file["seasonNumber"])
                        if seasons and seasons <= plan.remove:
                                result.append(file["id"])
                return sorted(result)

        def cancellation_ids(self, plan: SeasonPlan) -> list[int]:
                """Cancel no shared pack that still serves a retained season."""
                if plan.protected:
                        return []
                grouped: dict[str, list[dict[str, Any]]] = {}
                for row in self.queue:
                        grouped.setdefault(
                                str(row.get("downloadId") or row["id"]), []
                        ).append(row)
                result = []
                for rows in grouped.values():
                        numbers = {self.queued_season(row) for row in rows}
                        if None in numbers:
                                msg = (
                                        "An active download has an "
                                        "unknown season. Check Sonarr "
                                        "before switching."
                                )
                                raise SeasonError(msg)
                        if numbers & plan.remove:
                                if numbers & plan.keep:
                                        msg = (
                                                "A shared season pack "
                                                "spans retained "
                                                "seasons. Check Sonarr "
                                                "before switching."
                                        )
                                        raise SeasonError(msg)
                                result.append(rows[0]["id"])
                return result


class SeasonManager:
        """Apply durable per-user choices serially."""

        def __init__(self, cog: Watch) -> None:
                """Reuse the cog connection and permission checks."""
                self.cog = cog

        async def initialize(self) -> dict[str, Any]:
                """Freeze complete-show exceptions before new requests."""
                cfg = self.cog.config
                async with self.cog._acquisition_lock:
                        baseline = await cfg.season_retention_baseline()
                        if baseline is not None:
                                return baseline
                        protected = []
                        for series in await self.cog._arr_call("tv", "series"):
                                episodes = await self.cog._arr_call(
                                        "tv",
                                        "episode",
                                        params={"seriesId": str(series["id"])},
                                )
                                if complete_archive(
                                        episodes, datetime.now(UTC)
                                ):
                                        protected.append(series["tvdbId"])
                        baseline = {
                                "protected": protected,
                                "at": datetime.now(UTC).isoformat(),
                        }
                        await self.cog.config.season_retention_baseline.set(
                                baseline
                        )
                        return baseline

        async def snapshot(self, title: dict[str, Any]) -> SeasonSnapshot:
                """Resolve episode files and queues for the selected show."""
                series = await self.cog._existing_title("tv", title)
                if series is None:
                        return SeasonSnapshot(None, [], [], [])
                params = {"seriesId": str(series["id"])}
                episodes, files, queue = await asyncio.gather(
                        self.cog._arr_call("tv", "episode", params=params),
                        self.cog._arr_call("tv", "episodefile", params=params),
                        self.cog._arr_call(
                                "tv",
                                "queue/details",
                                params={**params, "includeEpisode": "true"},
                        ),
                )
                queue = [q for q in queue if q.get("seriesId") == series["id"]]
                return SeasonSnapshot(series, episodes, files, queue)

        async def preview(
                self, user: Person, title: dict[str, Any], season: int
        ) -> SeasonPlan:
                """Preview deletion after freezing archive protection."""
                baseline = await self.initialize()
                snapshot = await self.snapshot(title)
                return await self.plan(user, title, season, baseline, snapshot)

        async def plan(
                self,
                user: Person,
                title: dict[str, Any],
                season: int | None,
                baseline: dict[str, Any],
                snapshot: SeasonSnapshot,
        ) -> SeasonPlan:
                """Validate a season and combine users' claims."""
                if not isinstance(season, int) or season not in {
                        s["seasonNumber"]
                        for s in title.get("seasons", [])
                        if s["seasonNumber"] > 0
                }:
                        msg = "Choose a regular season listed for this show."
                        raise SeasonError(msg)
                if (
                        not isinstance(title.get("tvdbId"), int)
                        or title["tvdbId"] <= 0
                ):
                        msg = "The show has no valid TVDB identifier."
                        raise SeasonError(msg)
                return plan_seasons(
                        await self.cog.config.all_users(),
                        title["tvdbId"],
                        user.id,
                        season,
                        snapshot.present_seasons(),
                        protected=title["tvdbId"] in baseline["protected"],
                )

        async def write(
                self,
                user: Person,
                method: str,
                path: str,
                *,
                body: dict[str, Any] | None = None,
                params: dict[str, str] | None = None,
        ) -> Any:  # noqa: ANN401 (ARR resource)
                """Check permission before each Sonarr mutation."""
                if not await self.cog.can_download(user):
                        msg = "Download permission was revoked."
                        raise PermissionError(msg)
                return await self.cog._arr_call(
                        "tv", path, body=body, params=params, method=method
                )

        async def cleanup(
                self,
                user: Person,
                title: dict[str, Any],
                season: int | None,
                baseline: dict[str, Any],
                reviewed: SeasonPlan,
        ) -> SeasonSnapshot:
                """Re-read to catch imports racing this request."""
                current = await self.snapshot(title)
                plan = await self.plan(user, title, season, baseline, current)
                if plan.remove - reviewed.remove:
                        msg = (
                                "Season requests changed; review the "
                                "request again before deleting more "
                                "seasons."
                        )
                        raise SeasonError(msg)
                for queue_id in current.cancellation_ids(plan):
                        await self.write(
                                user,
                                "DELETE",
                                f"queue/{queue_id}",
                                params={
                                        "removeFromClient": "true",
                                        "blocklist": "false",
                                        "skipRedownload": "true",
                                },
                        )
                # A completed transfer may have imported while it was cancelled.
                current = await self.snapshot(title)
                plan = await self.plan(user, title, season, baseline, current)
                if plan.remove - reviewed.remove or current.cancellation_ids(
                        plan
                ):
                        msg = (
                                "Downloads changed during the season "
                                "switch. Run the request again to retry"
                                " safely."
                        )
                        raise SeasonError(msg)
                ids = current.deletion_ids(plan)
                if ids:
                        await self.write(
                                user,
                                "DELETE",
                                "episodefile/bulk",
                                body={"episodeFileIds": ids},
                        )
                        current = await self.snapshot(title)
                        if current.deletion_ids(
                                plan
                        ) or current.cancellation_ids(plan):
                                msg = (
                                        "The previous season is not "
                                        "fully removed. No new season "
                                        "search was started; retry the "
                                        "request."
                                )
                                raise SeasonError(msg)
                return current

        async def finish(
                self,
                user: Person,
                title: dict[str, Any],
                season: int | None,
                baseline: dict[str, Any],
                plan: SeasonPlan,
                snapshot: SeasonSnapshot,
                activation: dict[str, Any],
        ) -> str:
                """Finish a saved choice; make partial failures explicit."""
                try:
                        if not plan.protected:
                                snapshot = await self.cleanup(
                                        user, title, season, baseline, plan
                                )
                        # Only activate the new target after old files are gone.
                        await self.write(
                                user,
                                "PUT",
                                f"series/{activation['id']}",
                                body=activation,
                        )
                        active = any(
                                snapshot.queued_season(q) == season
                                for q in snapshot.queue
                        )
                        target_episodes = [
                                e
                                for e in snapshot.episodes
                                if e["seasonNumber"] == season
                        ]
                        downloaded = bool(target_episodes) and all(
                                e.get("hasFile") for e in target_episodes
                        )
                        if not active and not downloaded:
                                await self.write(
                                        user,
                                        "POST",
                                        "command",
                                        body={
                                                "name": "SeasonSearch",
                                                "seriesId": activation["id"],
                                                "seasonNumber": season,
                                        },
                                )
                except PreloadError:
                        msg = (
                                "Your season choice was saved, but the "
                                "change is incomplete. Retry the same "
                                "request to finish it safely."
                        )
                        raise SeasonError(msg) from None
                if not active and not downloaded:
                        return f"Season {season} search requested."
                if downloaded:
                        return f"Season {season} is already downloaded."
                return f"Season {season} is already in the download queue."

        async def acquire(
                self,
                user: Person,
                title: dict[str, Any],
                quality: str,
                season: int | None,
                expected_delete: set[int] | None,
        ) -> str:
                """Retain others' choices and remove unclaimed seasons."""
                if not await self.cog.can_download(user):
                        msg = "Download permission is required."
                        raise PermissionError(msg)
                baseline = await self.initialize()
                async with self.cog._acquisition_lock:
                        snapshot = await self.snapshot(title)
                        plan = await self.plan(
                                user, title, season, baseline, snapshot
                        )
                        if (
                                expected_delete is not None
                                and plan.remove - expected_delete
                        ):
                                msg = (
                                        "Season requests changed. Run "
                                        "the command again to review "
                                        "what will be deleted."
                                )
                                raise SeasonError(msg)
                        snapshot.deletion_ids(plan)
                        snapshot.cancellation_ids(plan)
                        profiles = await self.cog._arr_call(
                                "tv", "qualityprofile"
                        )
                        name = {
                                "1080p": "Doge - 1080p default",
                                "4k": "Doge - 4K optional",
                        }.get(quality)
                        profile = next(
                                (p for p in profiles if p["name"] == name), None
                        )
                        roots = await self.cog._arr_call("tv", "rootfolder")
                        root = "/arr/library/TV"
                        if profile is None or not any(
                                r["path"] == root and r.get("accessible", True)
                                for r in roots
                        ):
                                msg = (
                                        "The selected profile or TV "
                                        "library root is unavailable."
                                )
                                raise SeasonError(msg)
                        series = snapshot.series
                        if series is None:
                                body = dict(title)
                                body.pop("id", None)
                                body.pop("path", None)
                                body.update(
                                        rootFolderPath=root,
                                        qualityProfileId=profile["id"],
                                        monitored=False,
                                        monitorNewItems="none",
                                        seasonFolder=True,
                                )
                                body["seasons"] = [
                                        dict(s, monitored=False)
                                        for s in title["seasons"]
                                ]
                                body["addOptions"] = {
                                        "monitor": "skip",
                                        "searchForMissingEpisodes": False,
                                        "searchForCutoffUnmetEpisodes": False,
                                }
                                series = await self.write(
                                        user, "POST", "series", body=body
                                )
                        update = dict(series)
                        update.pop("addOptions", None)
                        update.update(
                                monitored=True, qualityProfileId=profile["id"]
                        )
                        if not plan.protected:
                                update["monitorNewItems"] = "none"
                        existing_flags = {
                                s["seasonNumber"]: s for s in series["seasons"]
                        }
                        for s in title["seasons"]:
                                existing_flags.setdefault(
                                        s["seasonNumber"],
                                        dict(s, monitored=False),
                                )
                        update["seasons"] = [
                                dict(
                                        s,
                                        monitored=(
                                                s.get("monitored", False)
                                                or n in plan.keep
                                        )
                                        if plan.protected
                                        else n in plan.keep,
                                )
                                for n, s in sorted(existing_flags.items())
                        ]
                        staging = dict(update)
                        if not plan.protected:
                                staging["qualityProfileId"] = series[
                                        "qualityProfileId"
                                ]
                                staging["seasons"] = [
                                        dict(
                                                s,
                                                monitored=s["seasonNumber"]
                                                in plan.shared,
                                        )
                                        for s in update["seasons"]
                                ]
                        await self.write(
                                user,
                                "PUT",
                                f"series/{series['id']}",
                                body=staging,
                        )
                        await self.cog.config.user(user).tv_seasons.set_raw(
                                str(title["tvdbId"]), value=season
                        )
                        outcome = await self.finish(
                                user,
                                title,
                                season,
                                baseline,
                                plan,
                                snapshot,
                                update,
                        )
                kept = ", ".join(str(n) for n in sorted(plan.keep))
                status = await self.cog.acquisition_status("tv", update)
                return (
                        f"Season {season} is now your selected season. "
                        f"Retaining requested seasons: {kept}. {outcome} "
                        + (
                                (
                                        "This complete-show archive is "
                                        "protected; no seasons were "
                                        "deleted."
                                )
                                if plan.protected
                                else (
                                        "Other seasons are kept only "
                                        "while another user requests "
                                        "them."
                                )
                        )
                        + "\n"
                        + status
                )
