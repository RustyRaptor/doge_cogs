"""The image helpers for the (future) chart picture."""

from typing import get_args

import pytest

pytest.importorskip("wand.image", reason="needs ImageMagick")

from doge_alignment.render import (  # noqa: E402
        BorderShape,
        grid_positions,
        process_avatar,
        solid_color_background,
)


def test_grid_positions_fill_rows_of_three() -> None:
        """Cells go left to right, then down, with gaps between them."""
        assert grid_positions(4, (100, 50), gap=10) == [
                (0, 0),
                (110, 0),
                (220, 0),
                (0, 60),
        ]


@pytest.mark.parametrize("shape", get_args(BorderShape))
def test_process_avatar_size_and_border(shape: BorderShape) -> None:
        """Avatars are resized, shaped and bordered on a copy."""
        with solid_color_background(300, 200, "blue") as original:
                out = process_avatar(original, (64, 64), 4, "green", shape)
                assert out.size == (72, 72)
                assert original.size == (300, 200)


def test_circle_has_transparent_corners() -> None:
        """Outside the circle is see-through, inside is not."""
        with solid_color_background(64, 64, "red") as img:
                out = process_avatar(img, (64, 64), 0, "black", "circle")
                assert out[0, 0].alpha == 0
                assert out[32, 32].alpha == 1
