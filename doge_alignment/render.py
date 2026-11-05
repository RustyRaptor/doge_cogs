"""Building blocks for drawing the chart as an image (not wired up yet).

Uses Wand (ImageMagick). The cog doesn't import this module, so it loads on
hosts without ImageMagick, such as dogebot's Red container. Drawing the chart
is on the TODO list in the repo.
"""

from typing import Literal

from wand.color import Color
from wand.drawing import Drawing
from wand.image import Image

BorderShape = Literal["square", "circle", "rounded"]


def solid_color_background(
        width: int = 512,
        height: int = 512,
        color: str = "black",
) -> Image:
        """Return a solid colour image."""
        return Image(width=width, height=height, background=Color(color))


def grid_positions(
        count: int,
        cell_size: tuple[int, int],
        gap: int,
        columns: int = 3,
) -> list[tuple[int, int]]:
        """Return the top-left corner of ``count`` cells in a grid.

        Cells fill left to right, then top to bottom, ``gap`` pixels apart.
        """
        width, height = cell_size
        return [
                ((i % columns) * (width + gap), (i // columns) * (height + gap))
                for i in range(count)
        ]


def _mask(size: tuple[int, int], shape: BorderShape) -> Image:
        mask_img = Image(
                width=size[0], height=size[1], background=Color("transparent")
        )
        with Drawing() as mask:
                mask.fill_color = Color("white")
                if shape == "circle":
                        mask.circle(
                                (size[0] // 2, size[1] // 2),
                                (size[0] // 2, 0),
                        )
                else:
                        radius = min(size) // 8
                        mask.rectangle(
                                left=0,
                                top=0,
                                width=size[0] - 1,
                                height=size[1] - 1,
                                radius=radius,
                        )
                mask.draw(mask_img)
        return mask_img


def process_avatar(
        img: Image,
        size: tuple[int, int],
        border: int,
        border_color: str,
        shape: BorderShape,
) -> Image:
        """Return a resized copy of ``img``, cut to ``shape`` and bordered."""
        out = img.clone()
        out.resize(size[0], size[1])
        if shape != "square":
                out.alpha_channel = "activate"
                with _mask(size, shape) as mask_img:
                        out.composite_channel(
                                "alpha", mask_img, "copy_alpha", 0, 0
                        )
        if border > 0:
                out.border(Color(border_color), border, border)
        return out
