"""Suggesting an alignment for someone else, and answering suggestions.

Anyone may suggest a cell for anyone but themselves; each suggester has at
most one pending suggestion per person, and a new one replaces it. The person
(or a chart manager) accepts one, which places them and drops the rest, or
rejects one. Front ends can make suggestions under a non-user id such as
:data:`GUESS`, for the model's guesses.
"""

from collections import Counter
from dataclasses import dataclass, replace

from .alignments import ALIGNMENTS, AlignmentName
from .chart import Chart, Entry, freeze_suggestions, may_manage, place

# Suggester id for guesses made by a language model
GUESS = "guess"


@dataclass(frozen=True)
class Suggestion:
        """One pending suggestion."""

        target_id: str
        suggester_id: str
        alignment: AlignmentName


def may_suggest(invoker_id: str, target_id: str) -> bool:
        """Whether ``invoker_id`` may suggest for ``target_id`` (not self)."""
        return invoker_id != target_id


def may_answer(
        chart: Chart,
        invoker_id: str,
        target_id: str,
        *,
        privileged: bool,
) -> bool:
        """Whether ``invoker_id`` may accept or reject for ``target_id``."""
        return invoker_id == target_id or may_manage(
                chart, invoker_id, privileged=privileged
        )


def suggest(chart: Chart, suggestion: Suggestion) -> Chart:
        """Return ``chart`` with ``suggestion`` pending.

        It replaces the same suggester's earlier suggestion for that person.
        """
        target = suggestion.target_id
        current = chart.suggestions.get(target, {})
        return replace(
                chart,
                suggestions=freeze_suggestions(
                        {
                                **chart.suggestions,
                                target: {
                                        **current,
                                        suggestion.suggester_id: (
                                                suggestion.alignment
                                        ),
                                },
                        }
                ),
        )


def pending(chart: Chart, target_id: str) -> tuple[Suggestion, ...]:
        """Return the suggestions waiting for ``target_id``."""
        return tuple(
                Suggestion(target_id, suggester, alignment)
                for suggester, alignment in chart.suggestions.get(
                        target_id, {}
                ).items()
        )


def find(chart: Chart, target_id: str, suggester_id: str) -> Suggestion | None:
        """Return ``suggester_id``'s suggestion for ``target_id``, if any."""
        alignment = chart.suggestions.get(target_id, {}).get(suggester_id)
        if alignment is None:
                return None
        return Suggestion(target_id, suggester_id, alignment)


def withdraw(chart: Chart, target_id: str, suggester_id: str) -> Chart:
        """Return ``chart`` without that suggestion (a rejection)."""
        return replace(
                chart,
                suggestions=freeze_suggestions(
                        {
                                **chart.suggestions,
                                target_id: {
                                        k: v
                                        for k, v in chart.suggestions.get(
                                                target_id, {}
                                        ).items()
                                        if k != suggester_id
                                },
                        }
                ),
        )


def accept(chart: Chart, suggestion: Suggestion, entry: Entry) -> Chart:
        """Return ``chart`` with the target placed as suggested.

        ``entry`` supplies the target's name and avatar; its alignment is
        replaced by the suggestion's. Every suggestion for the target goes.
        """
        placed = place(
                chart,
                suggestion.target_id,
                replace(entry, alignment=suggestion.alignment),
        )
        return replace(
                placed,
                suggestions=freeze_suggestions(
                        {
                                k: v
                                for k, v in placed.suggestions.items()
                                if k != suggestion.target_id
                        }
                ),
        )


def tally(
        chart: Chart, target_id: str
) -> tuple[tuple[AlignmentName, int], ...]:
        """Return how often each cell was suggested, most first."""
        counts = Counter(s.alignment for s in pending(chart, target_id))
        return tuple(
                sorted(
                        counts.items(),
                        key=lambda kv: (-kv[1], ALIGNMENTS.index(kv[0])),
                )
        )
