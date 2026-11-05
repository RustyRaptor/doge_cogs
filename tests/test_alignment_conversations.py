"""Bursts, conversations, transcripts and tidying."""

from alignment_messages import msg

from doge_alignment.core.conversations import (
        HIDDEN,
        around_author,
        conversations_with,
        merge_bursts,
        render_segment,
        segments,
        tidy,
)


def test_bursts_merge_quick_messages_by_one_author() -> None:
        """Split-up thoughts become one turn; others and pauses split."""
        turns = merge_bursts(
                [
                        msg(1, "me", 0, "so"),
                        msg(2, "me", 0.5, "anyway"),
                        msg(3, "a", 1, "ok"),
                        msg(4, "me", 1.5, "and"),
                        msg(5, "me", 10, "later"),
                ]
        )
        assert [(t.author_id, len(t.messages)) for t in turns] == [
                ("me", 2),
                ("a", 1),
                ("me", 1),
                ("me", 1),
        ]
        assert turns[0].text == "so\nanyway"


def test_replies_start_a_turn_and_keep_a_conversation_together() -> None:
        """A reply after a long pause stays in the conversation it answers."""
        turns = merge_bursts(
                [
                        msg(1, "a", 0, "question?"),
                        msg(2, "me", 0.2),
                        msg(3, "me", 0.4, reply_to="1001"),
                        msg(4, "me", 120, "answer", reply_to="1001"),
                        msg(5, "b", 400, "new topic"),
                ]
        )
        assert len(turns) == 5  # the replies don't merge
        convos = segments(turns)
        assert [len(s) for s in convos] == [4, 1]


def test_conversations_with_target_skip_bots_and_trim() -> None:
        """Only conversations the target is in, trimmed around them."""
        chatter = [msg(i, "a" if i % 2 else "b", i) for i in range(1, 30)]
        mine = msg(40, "me", 30, "hi")
        bot = msg(41, "bot", 30.5, "beep", bot=True)
        other_convo = [msg(50, "a", 300), msg(51, "b", 301)]
        found = conversations_with([*chatter, mine, bot, *other_convo], "me", 2)
        assert len(found) == 1
        assert [t.author_id for t in found[0]] == ["b", "a", "me"]
        assert around_author(found[0], "nobody") == ()


def test_render_anonymises_and_hides() -> None:
        """Target by name, others as letters, opted-out people hidden."""
        segment = segments(
                merge_bursts(
                        [
                                msg(
                                        1,
                                        "x",
                                        0,
                                        "hey <@55> look https://www.example.com/a?b",
                                ),
                                msg(2, "55", 1, "line one line two"),
                                msg(
                                        3,
                                        "55",
                                        1.5,
                                        "<:villager:123456789012345678> <@x>",
                                ),
                                msg(4, "y", 2, "secret"),
                        ]
                )
        )[0]
        text = render_segment(segment, "55", "Doge", frozenset({"y"}))
        lines = text.splitlines()
        assert lines[0] == "[2024-01-01 12:00 UTC, #chan-c1]"
        assert lines[1] == "A: hey @Doge look <link: example.com>"
        assert lines[2] == "Doge: line one line two"
        assert lines[3] == "    :villager: <@x>"
        assert lines[4] == f"B: {HIDDEN}"


def test_tidy() -> None:
        """Discord ids shrink to words."""
        assert tidy("<@&1> <#2> <t:1700000000:R> <a:dance:99>") == (
                "@role #channel [a time] :dance:"
        )
        assert tidy("<@!5>", {"5": "A"}) == "@A"
        assert tidy("http://x") == "<link: x>"
