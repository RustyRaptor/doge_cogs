"""Painting with ImageMagick (skipped where Wand can't load it)."""

import io

import pytest
from alignment_helpers import chart_with

from doge_alignment.core.images import sniff
from doge_alignment.core.layout import Theme
from doge_alignment.core.view import Profile, cells

render = pytest.importorskip(
        "doge_alignment.core.render", reason="needs ImageMagick"
)
wand_image = pytest.importorskip("wand.image")
wand_color = pytest.importorskip("wand.color")


def png(color: str, width: int = 40, height: int = 30) -> bytes:
        """Return a solid PNG."""
        with wand_image.Image(
                width=width, height=height, background=wand_color.Color(color)
        ) as img:
                img.format = "png"
                return bytes(img.make_blob())


def load(data: bytes):  # noqa: ANN201 (Wand is untyped)
        """Open PNG bytes."""
        return wand_image.Image(blob=data, format="png")


def test_sniff() -> None:
        """Only the four avatar formats are recognised."""
        assert sniff(png("red")) == "png"
        assert sniff(b"\xff\xd8\xff\xe0rest") == "jpeg"
        assert sniff(b"GIF89a...") == "gif"
        assert sniff(b"RIFF\0\0\0\0WEBPVP8 ") == "webp"
        assert sniff(b"<svg xmlns=...>") is None


def test_avatar_disc_is_round() -> None:
        """Corners are transparent, the middle is the avatar."""
        theme = Theme()
        with render.avatar_disc(
                png("red", 300, 200),
                64,
                present=True,
                fallback_text="?",
                theme=theme,
        ) as disc:
                assert disc.size == (64, 64)
                assert disc[0, 0].alpha == 0
                assert disc[63, 63].alpha == 0
                middle = disc[32, 32]
                assert middle.red > 0.9
                assert middle.green < 0.1


def test_absent_people_are_grey() -> None:
        """Someone who left is drawn without colour."""
        with render.avatar_disc(
                png("red"), 64, present=False, fallback_text="?", theme=Theme()
        ) as disc:
                middle = disc[32, 32]
                assert middle.red == pytest.approx(middle.green, abs=0.02)


def test_bad_bytes_fall_back_to_placeholder() -> None:
        """Non-images (here an SVG) are never decoded."""
        theme = Theme()
        with render.avatar_disc(
                b"<svg/>", 64, present=True, fallback_text="A", theme=theme
        ) as disc:
                assert disc[32, 4].alpha == 1


def test_render_chart_and_tiles() -> None:
        """One square PNG for the chart, nine for the tiles."""
        chart = chart_with(("1", "Lawful Good"), ("2", "Chaotic Evil"))
        chart_cells = cells(chart, {"1": Profile("one")}.get)
        avatars = {"1": png("blue")}
        theme = Theme()
        with load(render.render_chart(chart_cells, avatars, theme)) as img:
                side = 2 * theme.margin + 3 * theme.cell_size + 2 * theme.gap
                assert img.size == (side, side)
        tiles = render.render_tiles(chart_cells, avatars, theme)
        assert len(tiles) == 9
        with load(tiles[0]) as tile:
                assert tile.size == (theme.tile_size, theme.tile_size)
                # Tiles are the cell colour edge to edge
                corner = tile[0, 0]
                assert (corner.red, corner.green, corner.blue) != (0, 0, 0)
        assert all(io.BytesIO(t).read(8) == b"\x89PNG\r\n\x1a\n" for t in tiles)


def test_markup_escapes_imagemagick_and_pango() -> None:
        """Names can't inject markup, %-escapes, newlines or @file reads."""
        from doge_alignment.core.markup import escape, markup  # noqa: PLC0415

        assert escape("<b>&") == "&lt;b&gt;&amp;"
        assert escape("%[fx:1+1] %w") == "%%[fx:1+1] %%w"
        assert escape("a\\nb") == "a\\\\nb"
        text = markup("@/etc/passwd[999999x999999]", 20, "#ffffff")
        assert text.startswith("<span")
        assert text.endswith("</span>")
        assert 'size="20480"' in text


@pytest.mark.skipif(
        not render.has_pango(), reason="needs ImageMagick's Pango coder"
)
def test_pango_text_is_literal() -> None:
        """A hostile name renders as text, not as expanded escapes."""
        theme = Theme()
        hostile = render._pango_text("%[fx:1+1]%w", 20, theme)
        plain = render._pango_text("%%[fx:1+1]%%w", 20, theme)
        expanded = render._pango_text("2 0", 20, theme)
        assert hostile is not None
        assert plain is not None
        assert expanded is not None
        with hostile, plain, expanded:
                assert hostile.width > expanded.width
