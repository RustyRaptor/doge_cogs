"""Turning Discord's data into messages, and picking context anchors."""

import random

from alignment_messages import msg

from doge_alignment.core.sampling import SampledPage
from doge_alignment.gather import (
        anchor_ids,
        message_from_json,
        search_hits,
        total_from_search,
)

RAW = {
        "id": "175928847299117063",
        "channel_id": "9",
        "content": "hi <@1>",
        "timestamp": "2016-04-30T11:18:25.796000+00:00",
        "edited_timestamp": "2016-04-30T11:19:00+00:00",
        "author": {"id": "1", "username": "doge", "global_name": "Doge"},
        "message_reference": {"message_id": "175928847299117000"},
        "attachments": [{}],
        "mentions": [{}, {}],
}


def test_message_from_json() -> None:
        """All the fields the activity profile and transcripts use."""
        m = message_from_json(RAW, {"9": "general"})
        assert (m.author_id, m.author_name, m.channel_name) == (
                "1",
                "Doge",
                "general",
        )
        assert m.created.year == 2016  # noqa: PLR2004
        assert m.reply_to == "175928847299117000"
        assert (m.edited, m.attachments, m.mentions, m.bot) == (
                True,
                1,
                2,
                False,
        )
        bare = message_from_json({"id": "175928847299117063"}, {})
        assert bare.created.year == 2016  # noqa: PLR2004
        assert bare.channel_name == "unknown"


def test_search_body_parsing() -> None:
        """Hit groups' first message is the match; junk is skipped."""
        body = {"total_results": 4826, "messages": [[RAW], [], "junk"]}
        hits = search_hits(body, {})
        assert [h.id for h in hits] == ["175928847299117063"]
        assert total_from_search(body) == 4826
        assert total_from_search({"code": 110000}) == 0
        assert search_hits({}, {}) == ()


def test_anchor_ids_one_per_stretch() -> None:
        """Anchors come from separate stretches of the page."""
        page = SampledPage(
                0,
                (
                        msg(5, minutes=500, channel="c2"),
                        msg(4, minutes=499, channel="c2"),
                        msg(3, minutes=2),
                        msg(2, minutes=1),
                        msg(1, minutes=0),
                ),
        )
        picked = anchor_ids(page, "me", random.Random(1), count=2)
        assert len(picked) == 2  # noqa: PLR2004
        assert {m.channel_id for m in picked} == {"c1", "c2"}


def test_fresh_skips_messages_already_seen() -> None:
        """Overlapping server pages don't count a message twice."""
        from alignment_messages import msg  # noqa: PLC0415

        from doge_alignment.gather import fresh  # noqa: PLC0415

        seen: set[str] = set()
        first = fresh((msg(1), msg(2)), seen)
        second = fresh((msg(2), msg(3)), seen)
        assert [m.id for m in first] == ["1001", "1002"]
        assert [m.id for m in second] == ["1003"]
