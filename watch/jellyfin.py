"""Owner-scoped Jellyfin playlist operations."""

from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import quote

import httpx

from .errors import PreloadError


class Jellyfin:
        """Use a named bot integration, with credentials confined to headers."""

        def __init__(
                self, tokens: Callable[[], Awaitable[dict[str, str]]]
        ) -> None:
                """Read current integration credentials at each operation."""
                self.tokens = tokens

        async def call(
                self,
                path: str,
                *,
                body: dict[str, Any] | None = None,
                params: dict[str, str] | None = None,
                method: str = "GET",
        ) -> Any:  # noqa: ANN401 (upstream JSON)
                """Confine credentials and redact upstream errors."""
                config = await self.tokens()
                if not config.get("url") or not config.get("api_key"):
                        msg = "Jellyfin playlist integration is not configured."
                        raise PreloadError(msg)
                try:
                        async with httpx.AsyncClient(
                                base_url=config["url"].rstrip("/") + "/",
                                headers={
                                        "Authorization": 'MediaBrowser Token="'
                                        + config["api_key"]
                                        + '"'
                                },
                                timeout=30,
                        ) as client:
                                response = await client.request(
                                        method, path, json=body, params=params
                                )
                except httpx.HTTPError:
                        msg = (
                                "Jellyfin could not be reached; check status "
                                "before retrying."
                        )
                        raise PreloadError(msg) from None
                if response.status_code >= 400:
                        msg = f"Jellyfin answered HTTP {response.status_code}."
                        raise PreloadError(msg)
                if response.status_code == 204 or not response.content:
                        return None
                try:
                        return response.json()
                except ValueError:
                        msg = "Jellyfin returned an invalid response."
                        raise PreloadError(msg) from None

        async def user(self, name: str) -> str:
                """Resolve exactly one enabled linked account."""
                users = await self.call("Users")
                matches = [
                        u
                        for u in users
                        if u.get("Name", "").casefold() == name.casefold()
                        and not u.get("Policy", {}).get("IsDisabled", False)
                ]
                if len(matches) != 1:
                        msg = (
                                "Link an enabled Jellyfin account with `watch "
                                "link <name>`."
                        )
                        raise PreloadError(msg)
                return str(matches[0]["Id"])

        async def create_playlist(
                self, name: str, user_id: str, ids: list[str]
        ) -> str:
                """Create a public playlist owned by the linked user."""
                config = await self.tokens()
                service_user = config.get("user_id")
                editors = (
                        [{"UserId": service_user, "CanEdit": True}]
                        if service_user and service_user != user_id
                        else []
                )
                result = await self.call(
                        "Playlists",
                        method="POST",
                        body={
                                "Name": name,
                                "UserId": user_id,
                                "Ids": ids,
                                "MediaType": "Audio",
                                "IsPublic": True,
                                "Users": editors,
                        },
                )
                return str(result["Id"])

        async def update_playlist(
                self, playlist_id: str, ids: list[str]
        ) -> None:
                """Replace playlist contents in source order."""
                await self.call(
                        "Playlists/" + quote(playlist_id, safe=""),
                        method="POST",
                        body={"Ids": ids},
                )

        async def scan(self, paths: list[str]) -> None:
                """Ask Jellyfin to index newly written files."""
                await self.call(
                        "Library/Media/Updated",
                        method="POST",
                        body={
                                "Updates": [
                                        {
                                                "Path": path,
                                                "UpdateType": "Created",
                                        }
                                        for path in paths
                                ]
                        },
                )

        async def audio_at(self, path: str, user_id: str) -> str | None:
                """Resolve the indexed item for one exact downloaded file."""
                filename = path.replace("\\", "/").rsplit("/", 1)[-1]
                stem = filename.rsplit(".", 1)[0]
                result = await self.call(
                        "Items",
                        params={
                                "userId": user_id,
                                "includeItemTypes": "Audio",
                                "recursive": "true",
                                "searchTerm": stem,
                                "fields": "Path",
                                "limit": "100",
                        },
                )
                matches = [
                        item
                        for item in result.get("Items", [])
                        if str(item.get("Path", ""))
                        .replace("\\", "/")
                        .endswith("/" + filename)
                ]
                return str(matches[0]["Id"]) if len(matches) == 1 else None

        async def audio(
                self, track: dict[str, Any], user_id: str
        ) -> str | None:
                """Find one available track within user scope."""
                from .music import find_audio

                items = []
                start = 0
                while True:
                        result = await self.call(
                                "Items",
                                params={
                                        "userId": user_id,
                                        "includeItemTypes": "Audio",
                                        "recursive": "true",
                                        "searchTerm": track["title"],
                                        "fields": "ProviderIds",
                                        "limit": "100",
                                        "startIndex": str(start),
                                },
                        )
                        page = result.get("Items", [])
                        items.extend(page)
                        start += len(page)
                        total = result.get("TotalRecordCount", start)
                        if total > 500:
                                return None
                        if start >= total or not page:
                                break
                return find_audio(track, items)
