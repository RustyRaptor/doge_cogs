"""Chat messages as plain data, for conversations and activity stats.

Front ends convert their own message objects into :class:`Message`. Times are
UTC; Discord ids carry their creation time (:func:`snowflake_time`).
"""

from dataclasses import dataclass
from datetime import UTC, datetime

DISCORD_EPOCH_MS = 1_420_070_400_000


def snowflake_time(snowflake: str | int) -> datetime:
        """Return when a Discord id was created (UTC)."""
        ms = (int(snowflake) >> 22) + DISCORD_EPOCH_MS
        return datetime.fromtimestamp(ms / 1000, tz=UTC)


@dataclass(frozen=True)
class Message:
        """One chat message."""

        id: str
        author_id: str
        author_name: str
        channel_id: str
        channel_name: str
        created: datetime
        content: str
        reply_to: str | None = None
        edited: bool = False
        attachments: int = 0
        mentions: int = 0
        bot: bool = False


def by_time(
        messages: "list[Message] | tuple[Message, ...]",
) -> tuple[Message, ...]:
        """Return ``messages`` oldest first, without duplicate ids."""
        unique = {m.id: m for m in messages}
        return tuple(
                sorted(unique.values(), key=lambda m: (m.created, int(m.id)))
        )
