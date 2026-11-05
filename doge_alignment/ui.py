"""Discord views: the tile gallery, a confirm prompt, suggestion buttons."""

import re
from typing import Literal, Protocol, runtime_checkable

import discord

from .core.view import Cell, tile_filename

Verb = Literal["accept", "reject"]


def gallery_view(
        title: str, chart_cells: tuple[Cell, ...]
) -> discord.ui.LayoutView:
        """Return a message layout showing the nine tiles as a 3x3 gallery.

        Discord lays out a nine-item media gallery as three rows of three. The
        tiles are attached under :func:`tile_filename` names.
        """
        view = discord.ui.LayoutView(timeout=None)
        view.add_item(discord.ui.TextDisplay(f"### {title}"))
        view.add_item(
                discord.ui.MediaGallery(
                        *(
                                discord.MediaGalleryItem(
                                        f"attachment://{tile_filename(i, c)}",
                                        description=c.title,
                                )
                                for i, c in enumerate(chart_cells, start=1)
                        )
                )
        )
        return view


class ConfirmView(discord.ui.View):
        """Yes/no buttons that only ``author_id`` can press."""

        def __init__(self, author_id: int, confirm_label: str) -> None:
                """Create the prompt."""
                super().__init__(timeout=60)
                self.author_id = author_id
                self.confirmed: bool | None = None
                self.confirm.label = confirm_label

        async def interaction_check(
                self, interaction: discord.Interaction
        ) -> bool:
                """Only the person who asked may answer."""
                if interaction.user.id == self.author_id:
                        return True
                await interaction.response.send_message(
                        "This isn't your prompt.", ephemeral=True
                )
                return False

        async def _answer(
                self, interaction: discord.Interaction, *, confirmed: bool
        ) -> None:
                self.confirmed = confirmed
                for item in self.children:
                        if isinstance(item, discord.ui.Button):
                                item.disabled = True
                await interaction.response.edit_message(view=self)
                self.stop()

        @discord.ui.button(label="Confirm", style=discord.ButtonStyle.danger)
        async def confirm(
                self,
                interaction: discord.Interaction,
                _button: discord.ui.Button["ConfirmView"],
        ) -> None:
                """Go ahead."""
                await self._answer(interaction, confirmed=True)

        @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
        async def cancel(
                self,
                interaction: discord.Interaction,
                _button: discord.ui.Button["ConfirmView"],
        ) -> None:
                """Don't."""
                await self._answer(interaction, confirmed=False)


@runtime_checkable
class Answerer(Protocol):
        """What a suggestion button needs from the cog."""

        async def answer_from_button(
                self,
                interaction: discord.Interaction,
                verb: Verb,
                target_id: str,
                suggester_id: str,
        ) -> None:
                """Accept or reject a suggestion for the button's presser."""


CUSTOM_ID = (
        r"doge_alignment:(?P<verb>accept|reject):"
        r"(?P<target>[0-9]+):(?P<suggester>[0-9a-z]+)"
)


class AnswerButton(
        discord.ui.DynamicItem[discord.ui.Button],
        template=CUSTOM_ID,
):
        """Accept/Reject on a suggestion; works across bot restarts.

        Everything it needs is in its custom id, so Red can rebuild it from
        any old message (see ``bot.add_dynamic_items``).
        """

        def __init__(
                self, verb: Verb, target_id: str, suggester_id: str
        ) -> None:
                """Create the button."""
                super().__init__(
                        discord.ui.Button(
                                label="Accept"
                                if verb == "accept"
                                else "Reject",
                                style=(
                                        discord.ButtonStyle.success
                                        if verb == "accept"
                                        else discord.ButtonStyle.secondary
                                ),
                                custom_id=(
                                        f"doge_alignment:{verb}:"
                                        f"{target_id}:{suggester_id}"
                                ),
                        )
                )
                self.verb: Verb = verb
                self.target_id = target_id
                self.suggester_id = suggester_id

        @classmethod
        async def from_custom_id(
                cls,
                interaction: discord.Interaction,
                item: discord.ui.Item,
                match: re.Match[str],
                /,
        ) -> "AnswerButton":
                """Rebuild the button from a click on an old message."""
                del interaction, item
                verb: Verb = "accept" if match["verb"] == "accept" else "reject"
                return cls(verb, match["target"], match["suggester"])

        async def callback(self, interaction: discord.Interaction) -> None:
                """Hand the click to the cog."""
                cog = interaction.client.get_cog("AlignmentCog")  # ty: ignore[unresolved-attribute]
                if not isinstance(cog, Answerer):
                        await interaction.response.send_message(
                                "The alignment cog isn't loaded.",
                                ephemeral=True,
                        )
                        return
                await cog.answer_from_button(
                        interaction,
                        self.verb,
                        self.target_id,
                        self.suggester_id,
                )


def answer_view(target_id: str, suggester_id: str) -> discord.ui.View:
        """Return Accept and Reject buttons for one suggestion."""
        view = discord.ui.View(timeout=None)
        view.add_item(AnswerButton("accept", target_id, suggester_id))
        view.add_item(AnswerButton("reject", target_id, suggester_id))
        return view
