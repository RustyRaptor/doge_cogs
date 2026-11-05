"""Discord views: the tile gallery and a confirm prompt."""

import discord

from .core.view import Cell, tile_filename


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
                                        description=c.alignment,
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
