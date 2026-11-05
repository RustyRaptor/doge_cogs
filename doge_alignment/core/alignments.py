"""The nine alignments, their place on the 3x3 chart, and parsing them."""

import re
from typing import Literal, cast, get_args

AlignmentName = Literal[
        "Lawful Good",
        "Neutral Good",
        "Chaotic Good",
        "Lawful Neutral",
        "True Neutral",
        "Chaotic Neutral",
        "Lawful Evil",
        "Neutral Evil",
        "Chaotic Evil",
]

# The classic chart, row by row: good, neutral, evil; lawful to chaotic
ALIGNMENTS: tuple[AlignmentName, ...] = get_args(AlignmentName)

_ABBREVIATIONS: dict[str, AlignmentName] = {
        "".join(word[0] for word in name.split()).lower(): name
        for name in ALIGNMENTS
}

# Other names people type for the middle square
_NEUTRAL_ALIASES = frozenset({"n", "nn", "neutral", "neutralneutral"})


def is_alignment(value: str) -> bool:
        """Whether ``value`` is exactly one of the nine names."""
        return value in ALIGNMENTS


def as_alignment(value: str) -> AlignmentName:
        """Return ``value`` as an alignment.

        Raises:
                ValueError: if it isn't exactly one of the nine names.

        """
        if not is_alignment(value):
                msg = f"{value!r} is not an alignment"
                raise ValueError(msg)
        return cast("AlignmentName", value)


def _squash(text: str) -> str:
        return re.sub(r"[^a-z]", "", text.lower())


def parse_alignment(text: str) -> AlignmentName | None:
        """Read an alignment the way people type it, or return None.

        Takes the full name in any case and spacing (``chaotic-good``), the
        two-letter abbreviation (``CG``), and ``neutral``/``N``/``NN`` for
        True Neutral.
        """
        squashed = _squash(text)
        if squashed in _NEUTRAL_ALIASES:
                return "True Neutral"
        by_name = {_squash(name): name for name in ALIGNMENTS}
        return by_name.get(squashed) or _ABBREVIATIONS.get(squashed)


def abbreviation(alignment: AlignmentName) -> str:
        """Return the two-letter abbreviation, such as ``CG``."""
        return "".join(word[0] for word in alignment.split())


def grid_position(alignment: AlignmentName) -> tuple[int, int]:
        """Return ``(row, column)`` of ``alignment`` on the 3x3 chart."""
        return divmod(ALIGNMENTS.index(alignment), 3)


def matching(prefix: str) -> tuple[AlignmentName, ...]:
        """Return the alignments a partly typed name could mean."""
        wanted = _squash(prefix)
        exact = parse_alignment(prefix)
        return tuple(
                name
                for name in ALIGNMENTS
                if name == exact or _squash(name).startswith(wanted)
        )
