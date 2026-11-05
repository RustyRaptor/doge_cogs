"""Builders for chat messages in tests."""

from datetime import UTC, datetime, timedelta

from doge_alignment.core.messages import Message

START = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)


def msg(  # noqa: PLR0913 (a test builder)
        n: int,
        author: str = "me",
        minutes: float = 0,
        content: str = "",
        channel: str = "c1",
        reply_to: str | None = None,
        *,
        bot: bool = False,
) -> Message:
        """Return message ``n`` by ``author`` ``minutes`` after START."""
        return Message(
                id=str(1000 + n),
                author_id=author,
                author_name=author.title(),
                channel_id=channel,
                channel_name=f"chan-{channel}",
                created=START + timedelta(minutes=minutes),
                content=content or f"text {n}",
                reply_to=reply_to,
                bot=bot,
        )
