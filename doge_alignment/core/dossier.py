"""Assemble what the model is told about someone from what was fetched.

Front ends fetch random pages of the person's history (their own messages,
with each page's position in the history) and, around some of those
messages, the channel conversation they were part of. This turns that into a
:class:`~.guess.Dossier`: an activity profile and evidence items, one per
conversation, grouped by page so packing can spread over the history.
"""

from collections.abc import Mapping, Sequence
from datetime import UTC, date, tzinfo

from .activity import Anchor, activity, describe
from .baseline import Baseline, compare, describe_baseline, traits
from .conversations import conversations_with, render_segment
from .guess import Dossier, Evidence
from .messages import Message
from .sampling import SampledPage


def anchors(pages: Sequence[SampledPage[Message]]) -> tuple[Anchor, ...]:
        """Pin each page's newest message to its position in the history.

        One anchor per page: rates between a page's first and last message
        cover minutes and swing wildly, so only pages are compared.
        """
        return tuple(
                Anchor(page.position, page.items[0].created)
                for page in pages
                if page.items
        )


def evidence(
        pages: Sequence[SampledPage[Message]],
        contexts: Mapping[int, Sequence[Message]],
        target_id: str,
        target_name: str,
        hidden: frozenset[str] = frozenset(),
        tz: tzinfo = UTC,
) -> tuple[Evidence, ...]:
        """Return one evidence item per conversation the target was in.

        Each page's own messages are pooled with the context fetched for it
        (keyed by page index), so messages without fetched context still
        appear, as the target's own runs of messages.
        """
        return tuple(
                Evidence(
                        segment[0].start,
                        render_segment(
                                segment, target_id, target_name, hidden, tz
                        ),
                        group,
                )
                for group, page in enumerate(pages)
                for segment in conversations_with(
                        [*page.items, *contexts.get(group, ())], target_id
                )
                if segment
        )


def build_dossier(  # noqa: PLR0913 (it gathers everything)
        *,
        name: str,
        target_id: str,
        pages: Sequence[SampledPage[Message]],
        contexts: Mapping[int, Sequence[Message]],
        total: int,
        joined: date | None = None,
        avatar: str = "",
        palette: str = "",
        hidden: frozenset[str] = frozenset(),
        tz: tzinfo = UTC,
        baseline: Baseline | None = None,
        profile: str = "",
) -> Dossier:
        """Return the dossier for ``name`` from the fetched pages.

        Times in the activity profile and transcripts are local to ``tz``.
        With a ``baseline``, the dossier describes the server and sets the
        person's numbers against its regulars.
        """
        own = [m for page in pages for m in page.items]
        return Dossier(
                server=describe_baseline(baseline) if baseline else "",
                comparison=compare(traits(own, tz), total, baseline)
                if baseline
                else "",
                profile=profile,
                name=name,
                activity=describe(
                        activity(own, anchors(pages), total, joined, tz)
                ),
                avatar=avatar,
                palette=palette,
                evidence=evidence(pages, contexts, target_id, name, hidden, tz),
        )


MAX_ROLES = 15
MAX_STATUS = 128


def describe_profile(
        *,
        username: str,
        display_name: str,
        status: str = "",
        roles: Sequence[str] = (),
) -> str:
        """Return what someone's server profile shows, as short lines.

        Discord doesn't give bots the "About me" bio, so a custom status,
        a nickname and the roles people picked are the nearest thing.
        """
        flat = " ".join(status.split())[:MAX_STATUS]
        lines = [
                *(
                        [f"Username: {username}; goes by {display_name} here"]
                        if display_name and display_name != username
                        else []
                ),
                *([f"Custom status: {flat}"] if flat else []),
                *([f"Roles: {', '.join(roles[:MAX_ROLES])}"] if roles else []),
        ]
        return "\n".join(lines)
