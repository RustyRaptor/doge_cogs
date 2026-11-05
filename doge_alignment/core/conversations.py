"""Turn raw chat messages into turns, conversations and transcripts.

People often split one thought over several quick messages, so consecutive
messages by one author close together in time become one :class:`Turn`.
Turns close together in time form a conversation (a :class:`Segment`); a
long silence starts a new one. Reply links are kept on turns so a reply
never ends up separated from the conversation it answers.

This is simple time-gap disentanglement. Research tools exist for untangling
interleaved IRC conversations, but they're heavyweight models; Discord's
channel separation, reply links and time gaps get most of the way.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlsplit

from .messages import Message, by_time

BURST_GAP = timedelta(minutes=2)
CONVERSATION_GAP = timedelta(minutes=20)
HIDDEN = "[hidden: they opted out]"
# Longest single message quoted in a transcript
MAX_QUOTE = 600


@dataclass(frozen=True)
class Turn:
        """One author's consecutive messages, merged."""

        author_id: str
        messages: tuple[Message, ...]

        @property
        def start(self) -> datetime:
                """When the turn began."""
                return self.messages[0].created

        @property
        def end(self) -> datetime:
                """When the turn ended."""
                return self.messages[-1].created

        @property
        def text(self) -> str:
                """The turn's messages joined, each on its own line."""
                return "\n".join(
                        _clip(m.content) for m in self.messages if m.content
                )

        def render(self, labels: Mapping[str, str]) -> str:
                """Return the text tidied, with continuation lines indented."""
                return "\n    ".join(
                        _clip(tidy(m.content, labels))
                        for m in self.messages
                        if m.content
                )


Segment = tuple[Turn, ...]


USER_MENTION = re.compile(r"<@!?(\d+)>")
ROLE_MENTION = re.compile(r"<@&\d+>")
CHANNEL_MENTION = re.compile(r"<#\d+>")
CUSTOM_EMOJI = re.compile(r"<a?:(\w+):\d+>")
TIMESTAMP = re.compile(r"<t:\d+(?::\w)?>")
LINK = re.compile(r"https?://\S+")


def _link(match: re.Match[str]) -> str:
        host = urlsplit(match[0]).hostname or "a site"
        return f"<link: {host.removeprefix('www.')}>"


def tidy(text: str, labels: Mapping[str, str] | None = None) -> str:
        """Shrink Discord markup that costs tokens but says little.

        Custom emoji keep their name, links their site, mentions become the
        transcript's label for that person (or "someone"). Discord ids are
        long numbers that tokenize badly: chat text with them ran at about
        1.6 characters per token.
        """
        names = labels or {}
        text = USER_MENTION.sub(
                lambda m: "@" + names.get(m[1], "someone"), text
        )
        text = ROLE_MENTION.sub("@role", text)
        text = CHANNEL_MENTION.sub("#channel", text)
        text = CUSTOM_EMOJI.sub(r":\1:", text)
        text = TIMESTAMP.sub("[a time]", text)
        return LINK.sub(_link, text)


def _clip(text: str) -> str:
        flat = " ".join(text.split())
        return flat if len(flat) <= MAX_QUOTE else flat[: MAX_QUOTE - 1] + "…"


def merge_bursts(
        messages: Sequence[Message], gap: timedelta = BURST_GAP
) -> tuple[Turn, ...]:
        """Group consecutive messages by one author within ``gap``.

        Messages are sorted by time first; a reply always starts a new turn.
        """
        turns: list[Turn] = []
        for message in by_time(list(messages)):
                last = turns[-1] if turns else None
                if (
                        last is not None
                        and last.author_id == message.author_id
                        and message.created - last.end <= gap
                        and message.reply_to is None
                ):
                        turns[-1] = Turn(
                                last.author_id, (*last.messages, message)
                        )
                else:
                        turns.append(Turn(message.author_id, (message,)))
        return tuple(turns)


def segments(
        turns: Sequence[Turn], gap: timedelta = CONVERSATION_GAP
) -> tuple[Segment, ...]:
        """Split turns into conversations wherever the chat went quiet.

        A turn that replies to a message in the current conversation stays in
        it, however long the pause before it.
        """
        result: list[list[Turn]] = []
        for turn in turns:
                current = result[-1] if result else None
                ids = (
                        {m.id for t in current for m in t.messages}
                        if current
                        else set()
                )
                replies_here = any(m.reply_to in ids for m in turn.messages)
                if current and (
                        turn.start - current[-1].end <= gap or replies_here
                ):
                        current.append(turn)
                else:
                        result.append([turn])
        return tuple(tuple(s) for s in result)


def involving(segment: Segment, author_id: str) -> bool:
        """Whether ``author_id`` speaks in ``segment``."""
        return any(t.author_id == author_id for t in segment)


def around_author(
        segment: Segment, author_id: str, context: int = 4
) -> Segment:
        """Trim ``segment`` to ``author_id``'s turns and ``context`` around.

        Keeps every turn within ``context`` turns of one of theirs, so long
        conversations they dipped into don't crowd out everything else.
        """
        theirs = [i for i, t in enumerate(segment) if t.author_id == author_id]
        keep = {
                j
                for i in theirs
                for j in range(i - context, i + context + 1)
                if 0 <= j < len(segment)
        }
        return tuple(t for i, t in enumerate(segment) if i in keep)


def speaker_labels(
        segment: Segment, target_id: str, target_name: str
) -> dict[str, str]:
        """Name the target; call everyone else A, B, C... by first word."""
        others: list[str] = []
        for turn in segment:
                if turn.author_id != target_id and turn.author_id not in others:
                        others.append(turn.author_id)
        labels = {author: _letter(i) for i, author in enumerate(others)}
        return {**labels, target_id: target_name}


def _letter(index: int) -> str:
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        return letters[index % 26] + ("" if index < 26 else str(index // 26))


def render_segment(
        segment: Segment,
        target_id: str,
        target_name: str,
        hidden_authors: frozenset[str] = frozenset(),
) -> str:
        """Return a transcript: a date and channel line, then the turns.

        Other people are anonymised as letters; anyone in
        ``hidden_authors`` has their words replaced by :data:`HIDDEN`.
        """
        if not segment:
                return ""
        first = segment[0].messages[0]
        labels = speaker_labels(segment, target_id, target_name)
        header = f"[{first.created:%Y-%m-%d %H:%M} UTC, #{first.channel_name}]"
        lines = [
                f"{labels[t.author_id]}: "
                + (
                        HIDDEN
                        if t.author_id in hidden_authors
                        else t.render(labels)
                )
                for t in segment
                if t.text or t.author_id in hidden_authors
        ]
        return "\n".join([header, *lines])


def conversations_with(
        messages: Sequence[Message],
        target_id: str,
        context: int = 4,
) -> tuple[Segment, ...]:
        """Return the conversations in ``messages`` that the target is in.

        Bots are left out; each conversation is trimmed around the target.
        """
        humans = [m for m in messages if not m.bot]
        return tuple(
                around_author(s, target_id, context)
                for s in segments(merge_bursts(humans))
                if involving(s, target_id)
        )
