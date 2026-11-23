"""Requester-only notifications after background work finishes."""

from typing import Any, cast


async def test_completion_pings_only_the_requester_not_names_or_roles() -> None:
        """Catch mentioning everyone/roles via a hostile playlist title."""
        from watch.notify import completion_ping

        sent: list[dict[str, Any]] = []

        class Channel:
                async def send(self, content: str, **kwargs: Any) -> None:  # noqa: ANN401
                        sent.append({"content": content, **kwargs})

        class Bot:
                def get_channel(self, _channel: int) -> Channel:
                        return Channel()

        assert (
                await completion_ping(
                        cast("Any", Bot()),
                        123,
                        42,
                        "Finished @everyone <@&999>",
                )
                is True
        )
        assert sent[0]["content"].startswith("<@42> ")
        mentions = sent[0]["allowed_mentions"].to_dict()
        assert mentions["users"] == [42]
        assert "everyone" not in mentions["parse"]
        assert "roles" not in mentions["parse"]


async def test_missing_channel_falls_back_to_requester_dm() -> None:
        """Catch losing the completion when the original channel disappears."""
        from types import SimpleNamespace

        import discord

        from watch.notify import completion_ping

        sent: list[str] = []

        class User:
                async def send(self, content: str, **_kwargs: Any) -> None:  # noqa: ANN401
                        sent.append(content)

        class Bot:
                def get_channel(self, _channel: int) -> None:
                        return None

                async def fetch_channel(self, _channel: int) -> None:
                        raise discord.NotFound(
                                SimpleNamespace(status=404, reason="gone"),
                                "gone",
                        )

                def get_user(self, user_id: int) -> User | None:
                        return User() if user_id == 42 else None

        assert (
                await completion_ping(cast("Any", Bot()), 123, 42, "Finished")
                is True
        )
        assert sent == ["<@42> Finished"]
