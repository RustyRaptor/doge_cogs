"""Suggestions: one per suggester, accepted or rejected by the target."""

from alignment_helpers import chart_with

from doge_alignment.core.chart import Chart, Entry, forget_user
from doge_alignment.core.storage import from_data, from_yaml, to_data, to_yaml
from doge_alignment.core.suggestions import (
        GUESS,
        Suggestion,
        accept,
        find,
        may_answer,
        may_suggest,
        pending,
        suggest,
        tally,
        withdraw,
)


def suggested() -> Chart:
        """Return a chart with three suggestions for user 1."""
        chart = chart_with(("1", "Lawful Good"), admins=("9",))
        chart = suggest(chart, Suggestion("1", "2", "Chaotic Evil"))
        chart = suggest(chart, Suggestion("1", "3", "Chaotic Evil"))
        return suggest(chart, Suggestion("1", GUESS, "True Neutral"))


def test_one_suggestion_per_suggester() -> None:
        """A new suggestion replaces the same person's old one."""
        chart = suggest(suggested(), Suggestion("1", "2", "Neutral Good"))
        assert find(chart, "1", "2") == Suggestion("1", "2", "Neutral Good")
        assert len(pending(chart, "1")) == 3


def test_tally_most_first() -> None:
        """Counts per cell, most popular first."""
        assert tally(suggested(), "1") == (
                ("Chaotic Evil", 2),
                ("True Neutral", 1),
        )


def test_accept_places_and_clears_all() -> None:
        """Accepting moves the target and drops their other suggestions."""
        chart = suggested()
        chosen = find(chart, "1", "3")
        assert chosen is not None
        after = accept(chart, chosen, Entry("Lawful Good", "One", "u"))
        assert after.users["1"] == Entry("Chaotic Evil", "One", "u")
        assert pending(after, "1") == ()
        assert after.suggestions == {}
        assert pending(chart, "1")


def test_reject_removes_one() -> None:
        """Rejecting drops only that suggestion."""
        chart = withdraw(suggested(), "1", "2")
        assert find(chart, "1", "2") is None
        assert len(pending(chart, "1")) == 2
        assert withdraw(chart, "404", "2") == chart


def test_permissions() -> None:
        """No self-suggestions; the target or a manager answers."""
        chart = suggested()
        assert not may_suggest("1", "1")
        assert may_suggest("2", "1")
        assert may_answer(chart, "1", "1", privileged=False)
        assert not may_answer(chart, "2", "1", privileged=False)
        assert may_answer(chart, "9", "1", privileged=False)
        assert may_answer(chart, "2", "1", privileged=True)


def test_forget_user_drops_their_suggestions_both_ways() -> None:
        """Data deletion removes suggestions for and by the user."""
        chart = suggest(suggested(), Suggestion("2", "1", "Lawful Evil"))
        assert pending(forget_user(chart, "1"), "2") == ()
        assert pending(forget_user(chart, "1"), "1") == ()
        assert {
                s.suggester_id for s in pending(forget_user(chart, "3"), "1")
        } == {
                "2",
                GUESS,
        }


def test_suggestions_stored_but_not_exported() -> None:
        """Config keeps suggestions; YAML exports leave them out."""
        chart = suggested()
        assert from_data(to_data(chart)) == chart
        assert "suggestions" not in to_yaml(chart)
        assert from_yaml(to_yaml(chart)).suggestions == {}
