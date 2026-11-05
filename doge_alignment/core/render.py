"""Paint layout plans with ImageMagick (through Wand).

This is the imperative shell around :mod:`.layout`: Wand images are mutable,
so each function here builds its own images and returns PNG bytes, and no
image escapes it. Rendering is CPU-bound; async front ends should run it in a
thread.

Text goes through Pango where ImageMagick has it (Debian's
libmagickcore-6.q16-6-extra), so emoji and other scripts fall back to fonts
that have them; :mod:`.markup` escapes it. Without Pango, plain DejaVu Sans
is used and unknown glyphs show as boxes.

Avatars are passed in as bytes keyed by user id. Only PNG, JPEG, GIF and WebP
are decoded, and always with an explicit decoder, so untrusted bytes never
reach ImageMagick's other coders.
"""

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from wand.color import Color
from wand.drawing import Drawing
from wand.image import COMPOSITE_OPERATORS, Image
from wand.version import formats

from .images import sniff
from .layout import Box, CellPlan, Plan, Slot, Theme, plan_chart, plan_tile
from .markup import markup
from .view import Cell

# ImageMagick 7 calls it copy_alpha, 6 (Debian 12's) copy_opacity
COPY_ALPHA = (
        "copy_alpha" if "copy_alpha" in COMPOSITE_OPERATORS else "copy_opacity"
)

_FONT_CANDIDATES = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
)


def find_font(theme: Theme) -> str | None:
        """Return the theme's font, or the first bold DejaVu Sans found."""
        if theme.font:
                return theme.font
        return next((p for p in _FONT_CANDIDATES if Path(p).is_file()), None)


def has_pango() -> bool:
        """Whether ImageMagick can render Pango markup (font fallback)."""
        return bool(formats("PANGO"))


def _pango_text(text: str, size: int, theme: Theme) -> Image | None:
        """Render ``text`` with Pango, or return None if that fails.

        Pango falls back per glyph to other fonts (emoji, other scripts).
        """
        try:
                # Set these before reading: Image(resolution=..., background=
                # ...) is ignored without a size, giving 96 dpi on white
                img = Image()
                img.background_color = Color("transparent")
                img.resolution = (72, 72)
                img.read(
                        filename="pango:"
                        + markup(text, size, theme.text_color, theme.family)
                )
        except Exception:  # noqa: BLE001 (ImageMagick raises many types)
                return None
        return img


def _text(
        canvas: Image,
        box: Box,
        text: str,
        size: int,
        theme: Theme,
) -> None:
        if not text or size <= 0:
                return
        rendered = _pango_text(text, size, theme) if has_pango() else None
        if rendered is not None:
                with rendered:
                        left = box.x + (box.w - rendered.width) // 2
                        top = box.y + (box.h - rendered.height) // 2
                        canvas.composite(rendered, left=left, top=top)
                return
        with Drawing() as draw:
                font = find_font(theme)
                if font:
                        draw.font = font
                draw.font_size = size
                draw.fill_color = Color(theme.text_color)
                draw.text_alignment = "center"
                baseline = box.y + (box.h + size * 0.7) / 2
                draw.text(int(box.x + box.w / 2), int(baseline), text)
                draw(canvas)


def _decode(data: bytes | None) -> Image | None:
        fmt = sniff(data) if data else None
        if data is None or fmt is None:
                return None
        try:
                with Image(blob=data, format=fmt) as loaded:
                        return Image(image=loaded.sequence[0])
        except Exception:  # noqa: BLE001 (ImageMagick raises many types)
                return None


def _square(img: Image, size: int) -> None:
        side = min(img.width, img.height)
        img.crop(
                left=(img.width - side) // 2,
                top=(img.height - side) // 2,
                width=side,
                height=side,
        )
        img.resize(size, size)


def _disc_mask(size: int) -> Image:
        mask = Image(width=size, height=size, background=Color("transparent"))
        with Drawing() as draw:
                draw.fill_color = Color("white")
                mid = (size - 1) / 2
                draw.circle((mid, mid), (mid, 0))
                draw(mask)
        return mask


def _placeholder(size: int, text: str, theme: Theme) -> Image:
        img = Image(
                width=size,
                height=size,
                background=Color(theme.placeholder_color),
        )
        _text(img, Box(0, 0, size, size), text, size // 2, theme)
        return img


def avatar_disc(
        data: bytes | None,
        size: int,
        *,
        present: bool,
        fallback_text: str,
        theme: Theme,
) -> Image:
        """Return ``data`` as a round avatar of ``size`` with a ring.

        Undecodable or missing avatars become ``fallback_text`` on a plain
        disc. People who have left are greyed out with a dim ring. The
        caller owns (and closes) the returned image.
        """
        ring = max(2, round(size * theme.ring_ratio))
        inner = size - 2 * ring
        face = _decode(data) or _placeholder(inner, fallback_text, theme)
        with face:
                _square(face, inner)
                if not present:
                        face.modulate(brightness=60, saturation=0)
                face.alpha_channel = "activate"
                with _disc_mask(inner) as mask:
                        face.composite_channel("alpha", mask, COPY_ALPHA, 0, 0)
                disc = Image(
                        width=size, height=size, background=Color("transparent")
                )
                with Drawing() as draw:
                        draw.fill_color = Color(
                                theme.ring_color
                                if present
                                else theme.absent_ring_color
                        )
                        mid = (size - 1) / 2
                        draw.circle((mid, mid), (mid, 0))
                        draw(disc)
                disc.composite(face, left=ring, top=ring)
                return disc


def _initial(name: str) -> str:
        return name.strip()[:1].upper() or "?"


def _paint_slot(
        canvas: Image,
        slot: Slot,
        avatars: Mapping[str, bytes],
        theme: Theme,
) -> None:
        person = slot.person
        with avatar_disc(
                avatars.get(person.user_id),
                slot.avatar.w,
                present=person.present,
                fallback_text=_initial(person.name),
                theme=theme,
        ) as disc:
                canvas.composite(disc, left=slot.avatar.x, top=slot.avatar.y)
        if slot.name_box is not None:
                _text(canvas, slot.name_box, slot.name, slot.font_size, theme)


def _paint_cell(
        canvas: Image,
        cell: CellPlan,
        avatars: Mapping[str, bytes],
        theme: Theme,
) -> None:
        box = cell.box
        with Drawing() as draw:
                draw.fill_color = Color(cell.color)
                corners = (
                        {"radius": theme.corner_radius}
                        if theme.corner_radius
                        else {}
                )
                # Wand draws nothing for radius=0, so leave it out
                draw.rectangle(
                        left=box.x,
                        top=box.y,
                        width=box.w - 1,
                        height=box.h - 1,
                        **corners,
                )
                draw(canvas)
        _text(canvas, cell.label_box, cell.label, cell.label_size, theme)
        for slot in cell.slots:
                _paint_slot(canvas, slot, avatars, theme)
        if cell.overflow_box is not None:
                ob = cell.overflow_box
                with avatar_disc(
                        None,
                        ob.w,
                        present=True,
                        fallback_text=f"+{cell.overflow}",
                        theme=replace(theme, placeholder_color="#202225"),
                ) as disc:
                        canvas.composite(disc, left=ob.x, top=ob.y)


def paint(plan: Plan, avatars: Mapping[str, bytes], theme: Theme) -> bytes:
        """Return ``plan`` painted as PNG bytes."""
        with Image(
                width=plan.width,
                height=plan.height,
                background=Color(theme.background),
        ) as canvas:
                for cell in plan.cells:
                        _paint_cell(canvas, cell, avatars, theme)
                canvas.format = "png"
                return bytes(canvas.make_blob())


def render_chart(
        chart_cells: tuple[Cell, ...],
        avatars: Mapping[str, bytes],
        theme: Theme | None = None,
) -> bytes:
        """Return the whole 3x3 chart as one PNG."""
        theme = theme or Theme()
        return paint(plan_chart(chart_cells, theme), avatars, theme)


def render_tiles(
        chart_cells: tuple[Cell, ...],
        avatars: Mapping[str, bytes],
        theme: Theme | None = None,
) -> tuple[bytes, ...]:
        """Return each cell as its own square PNG, in chart order."""
        square = replace(theme or Theme(), corner_radius=0)
        return tuple(
                paint(plan_tile(cell, square), avatars, square)
                for cell in chart_cells
        )
