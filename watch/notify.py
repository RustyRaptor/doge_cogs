"""Completion delivery with only the requesting Discord user mentionable."""

from collections.abc import Awaitable, Callable
from typing import Any

import discord
from redbot.core.bot import Red


async def completion_ping(
        bot: Red,
        channel_id: int,
        user_id: int,
        text: str,
        *,
        send: Callable[..., Awaitable[Any]] | None = None,
) -> bool:
        """Try the channel, then a DM; retain failed deliveries."""
        content = f"<@{user_id}> {text}"
        mentions = discord.AllowedMentions(
                users=[discord.Object(id=user_id)],
                roles=False,
                everyone=False,
                replied_user=False,
        )
        try:
                if send is None:
                        channel = bot.get_channel(channel_id)
                        if channel is None:
                                channel = await bot.fetch_channel(channel_id)
                        send = channel.send
                await send(content, allowed_mentions=mentions)
        except discord.HTTPException:
                pass
        else:
                return True
        try:
                user = bot.get_user(user_id) or await bot.fetch_user(user_id)
                await user.send(content, allowed_mentions=mentions)
        except discord.HTTPException:
                return False
        return True
