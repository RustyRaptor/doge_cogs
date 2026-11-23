"""Test completion pings for other long commands."""

from types import SimpleNamespace
from typing import Any, cast

import pytest
from redbot.core import Config, commands
from watch_helpers import FakeContext

from watch.watch import Watch


@pytest.mark.parametrize(
        ("name", "elapsed", "expected"),
        [
                ("alignment guess", 15, True),
                ("alignment baseline", 20, True),
                ("help", 1, False),
                ("watch", 30, False),
                ("request", 30, False),
                ("importplaylist", 30, False),
        ],
)
async def test_only_long_untracked_commands_get_a_generic_completion_ping(
        config: Config,
        monkeypatch: pytest.MonkeyPatch,
        name: str,
        elapsed: int,
        *,
        expected: bool,
) -> None:
        """Catch instant or duplicate completion pings."""
        monkeypatch.setattr(Config, "get_conf", lambda *_a, **_k: config)
        cog = Watch(cast("Any", SimpleNamespace()))
        ctx = FakeContext()
        ctx.channel = SimpleNamespace(id=10)
        ctx.message = SimpleNamespace(id=123)
        ctx.command = SimpleNamespace(qualified_name=name)
        now = [0.0]
        monkeypatch.setattr(
                "watch.watch.monotonic", lambda: now[0], raising=False
        )
        await cog.command_started(cast("Any", ctx))
        now[0] = elapsed
        await cog.command_finished(cast("Any", ctx))
        assert bool(ctx.sent) is expected
        if expected:
                assert ctx.texts[0].startswith("<@1>")
                assert name in ctx.texts[0]


async def test_failed_long_command_pings_without_repeating_its_private_error(
        config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
        """Catch silence or credential disclosure on failure."""
        monkeypatch.setattr(Config, "get_conf", lambda *_a, **_k: config)
        cog = Watch(cast("Any", SimpleNamespace()))
        ctx = FakeContext()
        ctx.channel = SimpleNamespace(id=10)
        ctx.message = SimpleNamespace(id=123)
        ctx.command = SimpleNamespace(qualified_name="alignment guess")
        now = [0.0]
        monkeypatch.setattr(
                "watch.watch.monotonic", lambda: now[0], raising=False
        )
        await cog.command_started(cast("Any", ctx))
        now[0] = 20
        await cog.command_failed(
                cast("Any", ctx), commands.CommandError("private-credential")
        )
        assert ctx.texts[0].startswith("<@1>")
        assert "error" in ctx.texts[0]
        assert "private-credential" not in ctx.texts[0]
